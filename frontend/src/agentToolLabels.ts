export type AgentToolSummary = { result_count?: number; characters?: number }

export function agentToolStatusLabel(name: string, status: string, summary?: AgentToolSummary) {
  if (status !== 'completed') return `失败/拒绝 (${status})`
  if (name === 'web_search') return `成功 · ${summary?.result_count ?? 0} 条搜索摘要（尚未读取正文）`
  if (name === 'web_fetch') return `成功 · 已读取正文 ${summary?.characters ?? 0} 字符`
  return '成功'
}
