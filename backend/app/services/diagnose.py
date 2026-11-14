"""服务健康诊断业务规则：状态流转、批次结论、归档冻结与追踪证据联动都收在这里。

核心约定：
- 诊断报告只能按 草稿 → 诊断中 → 已生成 → 已处理 推进，处理异常只能从已生成开始；
- 每次诊断生成一个批次，健康分值、异常证据、根因分析必须写入同一批次；
- 归档冻结当时的健康分值、异常证据和根因分析，归档后只能重采样生成新版本；
- 并发诊断同一诊断对象只允许一个批次写入最终结论，失败时保留上一份完整报告并说明证据缺口。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.store import store

MODULE = "diagnose"
TRACE_MODULE = "trace"
REQUIRED_FIELDS = ["报告编号", "诊断对象"]
STATUS_ORDER = ["草稿", "诊断中", "已生成", "已处理"]

# 报告内容字段：登记时全部保留，避免字段错位丢失根因证据。
CONTENT_FIELDS = ["健康分值", "异常类型", "异常证据", "根因分析", "修复建议", "诊断耗时", "诊断时间"]

# 动作 → {允许的源状态: 目标状态}；不在表内的源状态一律拒绝。
ACTION_TRANSITIONS: dict[str, dict[str, str]] = {
    "开始诊断": {"草稿": "诊断中", "诊断中": "已生成"},
    "处理异常": {"已生成": "已处理"},
    "归档报告": {"已处理": "已处理"},  # 状态不变，冻结结论快照
}

ACTION_HINTS = {
    "开始诊断": "开始诊断只能从草稿或诊断中推进",
    "处理异常": "处理异常只能从已生成开始",
    "归档报告": "归档报告只能从已处理开始",
}

PLACEHOLDER = "—"


def _today() -> str:
    return datetime.date.today().isoformat()


def _batch_no(entry: dict[str, Any]) -> str:
    return f"BATCH-{datetime.date.today():%Y%m%d}-{int(entry.get('id', 0)):04d}-V{int(entry.get('版本', 1))}"


def _trace_rows_for(target: str) -> list[dict[str, Any]]:
    return [row for row in store.rows(TRACE_MODULE) if row.get("服务名称") == target]


def _abnormal_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if row.get("abnormal") or str(row.get("调用状态", "")).strip() not in ("", "正常", PLACEHOLDER)
    ]


class DiagnoseService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("报告编号", "")) or keyword in str(row.get("诊断对象", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._with_actions(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._with_actions(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        # 必填字段与内容字段全部落库，健康分值等待诊断批次写入。
        for field in REQUIRED_FIELDS + CONTENT_FIELDS:
            text = str(values.get(field) or "").strip()
            entry[field] = text if text else PLACEHOLDER
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["版本"] = 1
        entry["批次号"] = PLACEHOLDER
        entry["已归档"] = False
        rows.append(entry)
        return self._with_actions(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"诊断报告 {entry_id} 不存在或已归档"
        if action not in ACTION_TRANSITIONS:
            return None, f"动作「{action}」不属于服务健康诊断可执行范围"
        if entry.get("已归档"):
            if action == "开始诊断":
                return self._resample_new_version(entry)
            return None, (
                f"诊断报告 {entry.get('报告编号')} 已归档冻结，健康分值与根因分析已锁定在批次 "
                f"{entry.get('批次号')}；如需重新诊断请通过开始诊断重采样生成新版本"
            )
        current = str(entry.get("status", ""))
        target = ACTION_TRANSITIONS[action].get(current)
        if target is None:
            return None, (
                f"{ACTION_HINTS[action]}；诊断报告只能按{'→'.join(STATUS_ORDER)}推进，"
                f"当前状态「{current}」不能执行「{action}」"
            )
        if action == "开始诊断" and target == "诊断中":
            return self._start_diagnosis(entry)
        if action == "开始诊断" and target == "已生成":
            return self._finish_diagnosis(entry)
        if action == "处理异常":
            entry["status"] = target
            entry["abnormal"] = False
            entry["pending"] = True
            return self._with_actions(entry), f"诊断报告异常已处理，待归档冻结批次 {entry.get('批次号')}"
        # 归档报告：冻结当时的健康分值、异常证据和根因分析。
        entry["status"] = target
        entry["pending"] = False
        entry["已归档"] = True
        entry["归档快照"] = {
            "批次号": entry.get("批次号"),
            "版本": entry.get("版本", 1),
            "健康分值": entry.get("健康分值"),
            "异常类型": entry.get("异常类型"),
            "异常证据": entry.get("异常证据"),
            "根因分析": entry.get("根因分析"),
            "修复建议": entry.get("修复建议"),
            "归档时间": _today(),
        }
        return self._with_actions(entry), (
            f"诊断报告已归档：健康分值 {entry.get('健康分值')} 与根因分析已冻结在批次 "
            f"{entry.get('批次号')}（版本 {entry.get('版本', 1)}）"
        )

    def _start_diagnosis(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        conflict = self._running_batch(entry)
        if conflict is not None:
            return None, (
                f"诊断对象「{entry.get('诊断对象')}」已有批次 {conflict.get('批次号')} 在诊断中，"
                "并发诊断同一诊断对象只允许一个批次写入最终结论"
            )
        entry["批次号"] = _batch_no(entry)
        traces = _trace_rows_for(str(entry.get("诊断对象", "")))
        self._resample_traces(entry, traces)
        entry["status"] = "诊断中"
        entry["pending"] = True
        return self._with_actions(entry), (
            f"诊断报告已开始诊断，批次 {entry['批次号']} 已重新采样 {len(traces)} 条追踪记录"
        )

    def _finish_diagnosis(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        traces = _trace_rows_for(str(entry.get("诊断对象", "")))
        if not traces:
            # 失败时保留上一份完整报告并说明证据缺口，不回写半成品结论。
            return None, (
                f"批次 {entry.get('批次号')} 结论生成失败：诊断对象「{entry.get('诊断对象')}」"
                "缺少调用链采样证据（证据缺口：无追踪记录），已保留上一份完整报告"
            )
        # 结论落库前重新采样异常耗时，保证报告用的是本批次最新证据。
        self._resample_traces(entry, traces)
        self._write_conclusion(entry, traces)
        entry["status"] = "已生成"
        entry["pending"] = True
        return self._with_actions(entry), (
            f"诊断报告已生成：健康分值 {entry['健康分值']} 与根因分析已写入批次 {entry['批次号']}"
        )

    def _resample_new_version(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """已归档报告不允许改动，重采样只生成新版本。"""
        conflict = self._running_batch(entry)
        if conflict is not None:
            return None, (
                f"诊断对象「{entry.get('诊断对象')}」已有批次 {conflict.get('批次号')} 在诊断中，"
                "并发诊断同一诊断对象只允许一个批次写入最终结论"
            )
        rows = store.rows(MODULE)
        version = int(entry.get("版本", 1)) + 1
        new_entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "报告编号": f"{entry.get('报告编号')}-V{version}",
            "诊断对象": entry.get("诊断对象"),
            "版本": version,
            "status": "诊断中",
            "pending": True,
            "abnormal": False,
            "已归档": False,
            "诊断时间": _today(),
        }
        for field in CONTENT_FIELDS:
            new_entry.setdefault(field, PLACEHOLDER)
        new_entry["批次号"] = _batch_no(new_entry)
        rows.append(new_entry)
        traces = _trace_rows_for(str(new_entry.get("诊断对象", "")))
        self._resample_traces(new_entry, traces)
        return self._with_actions(new_entry), (
            f"原报告已归档冻结，重采样生成新版本 {new_entry['报告编号']}"
            f"（版本 {version}，批次 {new_entry['批次号']}）"
        )

    def _running_batch(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        for other in store.rows(MODULE):
            if (
                int(other.get("id", 0)) != int(entry.get("id", 0))
                and other.get("诊断对象") == entry.get("诊断对象")
                and other.get("status") == "诊断中"
                and not other.get("已归档")
            ):
                return other
        return None

    def _resample_traces(self, entry: dict[str, Any], traces: list[dict[str, Any]]) -> None:
        """重新采样诊断对象的追踪记录：异常耗时记录刷新采样时间与耗时。"""
        version = int(entry.get("版本", 1))
        for row in traces:
            row["关联报告"] = entry.get("报告编号")
            row["采样时间"] = _today()
            if row in _abnormal_rows(traces):
                base = (int(row.get("id", 0)) * 137 + version * 29) % 2600 + 400
                row["调用耗时"] = f"{base}ms"
                row["status"] = "已采样"  # 重采样后回到已采样，等待重新分析

    def _write_conclusion(self, entry: dict[str, Any], traces: list[dict[str, Any]]) -> None:
        """把健康分值、异常证据、根因分析写入同一批次，并同步到追踪记录。"""
        abnormal = _abnormal_rows(traces)
        score = max(100 - 15 * len(abnormal), 5)
        batch = str(entry.get("批次号"))
        target = str(entry.get("诊断对象"))
        evidence = "、".join(str(row.get("记录编号")) for row in abnormal) or "无"
        entry["健康分值"] = str(score)
        entry["异常类型"] = "、".join(sorted({str(row.get("调用状态")) for row in abnormal})) or "无异常"
        entry["异常证据"] = evidence
        if abnormal:
            entry["根因分析"] = (
                f"批次{batch}：{target}存在{len(abnormal)}条异常调用（{evidence}），"
                f"耗时超出阈值，健康分值降至{score}"
            )
            entry["修复建议"] = f"对{target}下游依赖做超时治理，重采样后复测健康分值"
        else:
            entry["根因分析"] = f"批次{batch}：{target}调用链耗时均在阈值内，未见异常证据"
            entry["修复建议"] = "保持当前巡检节奏"
        entry["诊断耗时"] = f"{20 + 6 * len(traces)}s"
        entry["诊断时间"] = _today()
        entry["abnormal"] = bool(abnormal)
        for row in traces:
            # 追踪详情页与诊断列表页共用同一份健康分值证据。
            row["健康分值"] = str(score)

    def _with_actions(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {**entry, "可执行动作": self.allowed_actions(entry)}

    @staticmethod
    def allowed_actions(entry: dict[str, Any]) -> list[str]:
        if entry.get("已归档"):
            return ["开始诊断"]  # 归档后只允许重采样生成新版本
        current = str(entry.get("status", ""))
        return [action for action, transitions in ACTION_TRANSITIONS.items() if current in transitions]
