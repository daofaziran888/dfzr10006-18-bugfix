import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/diagnose'
const columns = [
  "报告编号",
  "诊断对象",
  "健康分值",
  "异常类型",
  "异常证据",
  "根因分析",
  "修复建议",
  "诊断耗时",
  "诊断时间",
  "批次号",
  "版本"
]
const statuses = [
  "草稿",
  "诊断中",
  "已生成",
  "已处理"
]

type Row = Record<string, string | number | boolean | null | string[]>

interface ActionPayload {
  ok: boolean
  message: string
}

function allowedActions(row: Row): string[] {
  const value = row['可执行动作']
  return Array.isArray(value) ? (value as string[]) : []
}

function EvidenceCell({ value }: { value: string }) {
  const tokens = value.split(/[、,，]/).map((token) => token.trim()).filter(Boolean)
  if (tokens.length === 0 || (tokens.length === 1 && tokens[0] === '—')) {
    return <>—</>
  }
  return (
    <>
      {tokens.map((token, index) => (
        <span key={token}>
          {index > 0 && '、'}
          {/^TRAC-\d+/.test(token) ? (
            <Link className="link" to={`/trace?keyword=${encodeURIComponent(token)}`}>{token}</Link>
          ) : (
            token
          )}
        </span>
      ))}
    </>
  )
}

export default function DiagnoseView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('')
  const [stats, setStats] = useState([
    { label: '诊断报告总数', value: 0 },
    { label: '已生成报告', value: 0 },
    { label: '已处理报告', value: 0 },
  ])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (keyword) query.set('keyword', keyword)
    if (status) query.set('status', status)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('诊断报告列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      const statsResponse = await request(`${ENDPOINT}?size=200`)
      if (statsResponse.ok) {
        const statsPayload = await statsResponse.json()
        const items: Row[] = statsPayload.items ?? []
        setStats([
          { label: '诊断报告总数', value: statsPayload.total ?? items.length },
          { label: '已生成报告', value: items.filter((item) => item.status === '已生成').length },
          { label: '已处理报告', value: items.filter((item) => item.status === '已处理').length },
        ])
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '服务健康诊断列表读取失败')
    }
  }, [keyword, status])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload: ActionPayload | null = await response.json().catch(() => null)
      if (!response.ok || !payload?.ok) {
        throw new Error(payload?.message ?? '服务健康诊断动作未生效，请稍后重试')
      }
      setNotice(payload.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '服务健康诊断操作失败')
    }
  }

  return (
    <section className="page" data-module="diagnose">
      <header className="page-head">
        <div>
          <h2>服务健康诊断管理</h2>
          <p className="page-desc">维护服务健康的异常检测、根因分析与修复建议，为服务健康诊断提供可审计的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('诊断报告登记入口尚未接入审批流')}>
            登记诊断报告
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出服务健康诊断清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>关键字</span>
          <input
            value={keyword}
            placeholder="按报告编号或诊断对象检索"
            onChange={(event) => setKeyword(event.target.value)}
          />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部状态</option>
            {statuses.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => { setKeyword(''); setStatus('') }}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => (
                <td key={column}>
                  {column === '异常证据'
                    ? <EvidenceCell value={String(row[column] ?? '—')} />
                    : String(row[column] ?? '—')}
                </td>
              ))}
              <td className="row-actions">
                {allowedActions(row).map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                {row['已归档'] === true && <span className="empty-state">已归档冻结</span>}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无服务健康诊断数据，可先登记诊断报告</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条服务健康诊断记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
