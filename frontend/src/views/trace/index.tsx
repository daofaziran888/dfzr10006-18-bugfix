import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/trace'
const columns = [
  "记录编号",
  "调用链ID",
  "服务名称",
  "调用耗时",
  "调用状态",
  "上游服务",
  "下游服务",
  "采样时间",
  "关联报告",
  "健康分值"
]
const statuses = [
  "已采样",
  "已分析",
  "已归档"
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

export default function TraceView() {
  const [searchParams] = useSearchParams()
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [keyword, setKeyword] = useState(searchParams.get('keyword') ?? '')
  const [status, setStatus] = useState('')
  const [stats, setStats] = useState([
    { label: '追踪记录总数', value: 0 },
    { label: '已分析记录', value: 0 },
    { label: '已归档记录', value: 0 },
  ])

  // 从诊断报告列表页跳转过来时，按 URL 里的关键字过滤，保证两个页面对应同一份健康分值证据。
  useEffect(() => {
    setKeyword(searchParams.get('keyword') ?? '')
  }, [searchParams])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (keyword) query.set('keyword', keyword)
    if (status) query.set('status', status)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('追踪记录列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      const statsResponse = await request(`${ENDPOINT}?size=200`)
      if (statsResponse.ok) {
        const statsPayload = await statsResponse.json()
        const items: Row[] = statsPayload.items ?? []
        setStats([
          { label: '追踪记录总数', value: statsPayload.total ?? items.length },
          { label: '已分析记录', value: items.filter((item) => item.status === '已分析').length },
          { label: '已归档记录', value: items.filter((item) => item.status === '已归档').length },
        ])
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '调用链追踪列表读取失败')
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
        throw new Error(payload?.message ?? '调用链追踪动作未生效，请稍后重试')
      }
      setNotice(payload.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '调用链追踪操作失败')
    }
  }

  return (
    <section className="page" data-module="trace">
      <header className="page-head">
        <div>
          <h2>调用链追踪管理</h2>
          <p className="page-desc">维护调用链的调用层级、耗时统计与上下追溯，为调用链追踪提供可审计的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('追踪记录登记入口尚未接入审批流')}>
            登记追踪记录
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出调用链追踪清单
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
            placeholder="按记录编号或服务名称检索"
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
              {columns.map((column) => <td key={column}>{String(row[column] ?? '—')}</td>)}
              <td className="row-actions">
                {allowedActions(row).map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                {allowedActions(row).length === 0 && <span className="empty-state">已归档冻结</span>}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无调用链追踪数据，可先登记追踪记录</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条调用链追踪记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
