export function apiFailureMessage(status: number, payload: unknown): string {
  const detail = (payload as { detail?: string | { code?: string; message?: string } } | null)?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  if (detail?.code) return detail.code
  if ([502, 503, 504].includes(status)) {
    return `Personal OS 后端暂时不可用（HTTP ${status}）。请在服务器打开 PersonalOS-Manager.cmd 检查服务状态。`
  }
  return `请求失败（HTTP ${status}）`
}

export function apiConnectionFailureMessage(): string {
  return '无法连接 Personal OS 服务。请确认服务器在线，并在服务器打开 PersonalOS-Manager.cmd 检查服务状态。'
}
