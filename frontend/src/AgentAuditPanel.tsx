import { useEffect, useState } from 'react'

type Run = { id: number; model: string; provider: string; status: string; prompt_tokens: number; completion_tokens: number; authorized_scope: string }
type Call = { tool: string; status: string; duration_ms: number | null; error_code: string | null; result: string }

function callCounts(calls: Call[]) {
  const successful = calls.filter(call => call.status === 'completed').length
  const failed = calls.filter(call => call.status === 'failed' || call.status === 'timeout').length
  const denied = calls.filter(call => call.status === 'denied')
  const budgetRejected = denied.filter(call => ['TOOL_CALL_LIMIT', 'SEARCH_CALL_LIMIT', 'FETCH_CALL_LIMIT'].includes(call.error_code ?? '')).length
  return { requested: calls.length, executed: successful + failed, successful, failed, budgetRejected, denied: denied.length - budgetRejected }
}

export function AgentAuditPanel({ request }: { request: (path: string) => Promise<any> }) {
  const [runs, setRuns] = useState<Run[]>([])
  const [calls, setCalls] = useState<Call[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState('')
  async function load() {
    try { setRuns(await request('/api/agent/runs')); setError('') }
    catch (err) { setError(err instanceof Error ? err.message : '读取审计失败') }
  }
  async function inspect(id: number) {
    try { setCalls(await request(`/api/agent/runs/${id}/tools`)); setSelected(id); setError('') }
    catch (err) { setError(err instanceof Error ? err.message : '读取工具记录失败') }
  }
  useEffect(() => { void load() }, [])
  return <section className="panel"><div className="panel-head"><h3>真实 Agent 执行审计</h3><button className="secondary" onClick={load}>刷新记录</button></div>
    {error && <div className="notice error-notice">{error}</div>}
    {runs.map(run => <div className="proposal-card" key={run.id}><button className="text-btn" onClick={() => inspect(run.id)}>运行 #{run.id} · {run.provider} / {run.model} · {run.status}</button><p>Token：{run.prompt_tokens} 输入 / {run.completion_tokens} 输出</p><small>授权：{run.authorized_scope}</small>
      {selected === run.id && <><p className="muted">工具计数：请求 {callCounts(calls).requested} · 已执行 {callCounts(calls).executed} · 成功 {callCounts(calls).successful} · 失败 {callCounts(calls).failed} · 预算拒绝 {callCounts(calls).budgetRejected} · 其他拒绝 {callCounts(calls).denied}</p>{calls.map((call, i) => <div className="tool-log" key={i}><strong>{call.tool} · {call.status} · {call.duration_ms ?? 'UNKNOWN'} ms</strong>{call.error_code && <p>{call.error_code}</p>}<pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{call.result}</pre></div>)}</>}
    </div>)}{runs.length === 0 && <p>暂无执行记录。</p>}
  </section>
}
