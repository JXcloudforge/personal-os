import { describe, expect, it } from 'vitest'
import { apiConnectionFailureMessage, apiFailureMessage } from './apiErrors'

describe('API failure messages', () => {
  it('explains an HTML reverse-proxy 502 instead of showing a generic request failure', () => {
    expect(apiFailureMessage(502, null)).toContain('Personal OS 后端暂时不可用')
    expect(apiFailureMessage(502, null)).toContain('PersonalOS-Manager.cmd')
  })

  it('preserves structured authentication errors from FastAPI', () => {
    expect(apiFailureMessage(401, { detail: 'Invalid username or password' })).toBe('Invalid username or password')
    expect(apiFailureMessage(500, { detail: { code: 'INTERNAL_ERROR', message: 'Internal server error' } })).toBe('Internal server error')
  })

  it('shows the explicit server denial reason for disabled instance backup access', () => {
    expect(apiFailureMessage(403, { detail: { code: 'INSTANCE_BACKUP_WEB_DISABLED', message: 'Web instance backup management is disabled.' } })).toBe('Web instance backup management is disabled.')
  })

  it('provides a safe instruction for a transport-level failure', () => {
    expect(apiConnectionFailureMessage()).toContain('无法连接 Personal OS 服务')
  })
})
