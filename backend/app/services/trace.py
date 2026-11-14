"""调用链追踪业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "trace"
REQUIRED_FIELDS = ["记录编号", "调用链ID", "服务名称"]
STATUS_ORDER = ["已采样", "已分析", "已归档"]

# 记录内容字段：登记时全部保留，避免字段错位丢失耗时证据。
CONTENT_FIELDS = ["调用耗时", "调用状态", "上游服务", "下游服务", "采样时间", "关联报告", "健康分值"]

# 动作 → {允许的源状态: 目标状态}；不在表内的源状态一律拒绝。
ACTION_TRANSITIONS: dict[str, dict[str, str]] = {
    "分析追踪": {"已采样": "已分析"},
    "归档追踪": {"已分析": "已归档"},
}

PLACEHOLDER = "—"


class TraceService:
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
                if keyword in str(row.get("记录编号", "")) or keyword in str(row.get("服务名称", ""))
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
        for field in REQUIRED_FIELDS + CONTENT_FIELDS:
            text = str(values.get(field) or "").strip()
            entry[field] = text if text else PLACEHOLDER
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._with_actions(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"追踪记录 {entry_id} 不存在或已归档"
        if action not in ACTION_TRANSITIONS:
            return None, f"动作「{action}」不属于调用链追踪可执行范围"
        current = str(entry.get("status", ""))
        target = ACTION_TRANSITIONS[action].get(current)
        if target is None:
            return None, (
                f"追踪记录只能按{'→'.join(STATUS_ORDER)}推进，"
                f"当前状态「{current}」不能执行「{action}」"
            )
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        return self._with_actions(entry), f"追踪记录已{action}"

    def _with_actions(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {**entry, "可执行动作": self.allowed_actions(entry)}

    @staticmethod
    def allowed_actions(entry: dict[str, Any]) -> list[str]:
        current = str(entry.get("status", ""))
        return [action for action, transitions in ACTION_TRANSITIONS.items() if current in transitions]
