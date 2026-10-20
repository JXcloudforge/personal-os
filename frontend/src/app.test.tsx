import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './main'
import { ChatWorkspace } from './ChatWorkspace'

describe('Personal OS authenticated task workflow', () => {
  let conversations: Array<Record<string, unknown>> = []
  beforeEach(() => {
    localStorage.removeItem('personal-os-theme-cache')
    localStorage.removeItem('personal-os-chat-sidebar')
    document.cookie = 'personal_os_csrf=test-csrf; path=/'
    let tasks: Array<Record<string, unknown>> = []
    let conversationMessages: Array<Record<string, unknown>> = []
    let appearance = { theme: 'classic', has_background: false, background_url: null, background_darkness: 48, background_blur: 3, background_opacity: 48 }
    conversations = []
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const method = init.method || 'GET'
      const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
      if (url === '/api/auth/status') return response({ setup_required: false })
      if (url === '/api/system/info') return response({ app_version: '0.3.0', runtime_environment: 'test', database: 'sqlite', database_path: 'test.sqlite3', alembic_revision: 'test-revision', service_online: true, offsite_backup: 'not_configured', releases: [{ version: '0.3.0', name: 'AI 对话与任务工作台', release_date: '2026-10-13', features: ['持久化会话'], user_problem: '管理长期对话', technical_details: '测试架构细节', limitations: ['iPhone 待验收'] }, { version: '0.2.0', name: '账号与数据管理', release_date: null, features: ['备份和账户管理'], user_problem: '方便管理个人数据', technical_details: '基础备份架构', limitations: ['异地备份未配置'] }, { version: '0.1.0', name: '基础框架', release_date: null, features: ['基础框架'], user_problem: '集中保存任务', technical_details: '测试基础技术', maintenance_results: ['后续安全维护'], limitations: ['需要主机在线'] }], roadmap: [{ version: '0.4.0', name: 'K2 结构化知识卡片', status: 'planned' }] })
      if (url === '/api/health') return response({ status: 'ok', database: 'ok' })
      if (url === '/api/auth/login' && method === 'POST') return response({ username: 'tester' })
      if (url === '/api/account/appearance' && method === 'GET') return response(appearance)
      if (url === '/api/account/appearance' && method === 'PATCH') { appearance = { ...appearance, ...JSON.parse(String(init.body)) }; return response(appearance) }
      if (url === '/api/dashboard') return response({ today_date: '2026-10-13', due_today_count: tasks.length, today_tasks: tasks, completed_today_count: 0, completed_today_tasks: [], overdue_count: 0, overdue_tasks: [], unscheduled_count: 0, unscheduled_tasks: [], today_review: null, task_counts: { TODO: tasks.length, DOING: 0, BLOCKED: 0, VERIFY: 0, DONE: 0, CANCELLED: 0 }, recent_tasks: tasks, study_count: 0, workout_count: 0, review_count: 0 })
      if ((url === '/api/tasks' || url.startsWith('/api/tasks?')) && method === 'GET') return response(tasks)
      if (/^\/api\/tasks\/\d+$/.test(url) && method === 'GET') { const item = tasks.find((task) => task.id === Number(url.split('/').pop())); return response(item ? { ...item, evidence: [], history: [], links: [] } : { detail: 'not found' }, item ? 200 : 404) }
      if (url === '/api/tasks' && method === 'POST') {
        const body = JSON.parse(String(init.body))
        const created = { id: 31, task_code: null, title: body.title, description: body.description, module: body.module, status: 'TODO', priority: body.priority, estimated_minutes: body.estimated_minutes, start_date: body.start_date, due_date: body.due_date, expected_duration_days: body.expected_duration_days, acceptance_criteria: body.acceptance_criteria, is_pinned: false, archived: false, deleted: false, evidence_count: 0 }
        tasks = [created]
        return response(created)
      }
      if (url === '/api/agent/proposals') return response([])
      if (url === '/api/agent/runs') return response([])
      if (url === '/api/agent/chat/history') return response({ enabled: true, messages: [] })
      if (url.startsWith('/api/agent/chat/conversations?') && method === 'GET') return response({ enabled: true, conversations })
      if (url.startsWith('/api/agent/chat/conversations?limit=100') && method === 'GET') return response({ enabled: true, conversations })
      if (url === '/api/agent/chat/conversations' && method === 'POST') { const body = JSON.parse(String(init.body)); const created = { id: '11111111-1111-4111-8111-111111111111', title: '新会话', domain: body.domain || 'general', is_pinned: false, archived: false, deleted: false }; conversations = [created, ...conversations.filter((item) => item.id !== created.id)]; return response(created) }
      if (url.startsWith('/api/agent/chat/conversations/11111111-1111-4111-8111-111111111111') && method === 'GET') return response({ id: '11111111-1111-4111-8111-111111111111', messages: conversationMessages })
      if (url === '/api/agent/chat/conversations/11111111-1111-4111-8111-111111111111' && method === 'PATCH') { const body = JSON.parse(String(init.body)); conversations = conversations.map((item) => item.id === '11111111-1111-4111-8111-111111111111' ? { ...item, ...body } : item); return response(conversations[0]) }
      if (url === '/api/agent/chat/conversations/11111111-1111-4111-8111-111111111111' && method === 'DELETE') { conversations = conversations.map((item) => ({ ...item, deleted: true })); return response({ deleted: true }) }
      if (url.endsWith('/11111111-1111-4111-8111-111111111111/restore') && method === 'POST') return response({ deleted: false })
      if (url === '/api/agent/chat' && method === 'POST') { const body = JSON.parse(String(init.body)); conversationMessages = [...conversationMessages, { id: conversationMessages.length + 1, role: 'user', content: body.message, status: 'completed', run_id: 88 }, { id: conversationMessages.length + 2, role: 'assistant', content: 'mock response', status: 'completed', run_id: 88 }]; return response({ content: 'mock response', run_id: 88, tools: [] }) }
      if (url.startsWith('/api/settings/ai/usage') && method === 'GET') return response({ timezone: 'Asia/Taipei', refreshed_at: '2026-10-12T23:00:00+08:00', periods: { today: { input_tokens: 11, output_tokens: 4, total_tokens: 15, unknown_usage_records: 0, legacy_aggregated_runs: 0 }, last_7_days: { input_tokens: 21, output_tokens: 8, total_tokens: 29, unknown_usage_records: 1, legacy_aggregated_runs: 0 }, month_to_date: { input_tokens: 31, output_tokens: 12, total_tokens: 43, unknown_usage_records: 1, legacy_aggregated_runs: 2 } }, conversation_usage: null, by_model: [{ provider: 'deepseek', model: 'deepseek-flash', input_tokens: 11, output_tokens: 4, requests: 1, unknown_usage: 0 }], recent_requests: [{ id: 1, run_id: 88, request_index: 1, provider: 'deepseek', model: 'deepseek-flash', input_tokens: 11, output_tokens: 4, total_tokens: 15, status: 'completed', requested_at: '2026-10-12T15:00:00Z' }], legacy_detail_note: '迁移前为运行级聚合统计。', budget: { max_tool_calls: 8, max_output_tokens: 1600 }, search: { today_calls: 1, last_7_days_calls: 2, month_to_date_calls: 3, credits: null, credits_note: '尚无可核验 Credits 数据。' } })
      if (url === '/api/settings/ai/usage/budget' && method === 'PUT') return response(JSON.parse(String(init.body)))
      if (url === '/api/account/profile' && method === 'GET') return response({ id: 7, username: 'tester', display_name: null, active_sessions: 1, created_at: 'today', last_login_at: 'today', current_session_created_at: 'today' })
      if (url === '/api/account/profile' && method === 'PATCH') return response({ id: 7, username: JSON.parse(String(init.body)).username, display_name: JSON.parse(String(init.body)).display_name })
      if (url === '/api/data/backups' && method === 'GET') return response({ detail: { code: 'INSTANCE_BACKUP_WEB_DISABLED', message: 'Web instance backup management is disabled.' } }, 403)
      if (url === '/api/data/backups' && method === 'POST') return response({ detail: { code: 'INSTANCE_BACKUP_WEB_DISABLED', message: 'Web instance backup management is disabled.' } }, 403)
      if (url === '/api/account/data-settings') return response({ chat_history_enabled: true, auto_backup_enabled: false, backup_interval_days: 1, last_local_backup_at: null, last_offsite_backup_at: null })
      return response({ detail: 'Unexpected test route' }, 404)
    }) as typeof fetch
  })

  it('registers with a fresh challenge, confirms the server session, and never stores registration secrets', async () => {
    const requests: Array<{ url: string; init: RequestInit }> = []
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const method = init.method || 'GET'
      requests.push({ url, init })
      const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
      if (url === '/api/auth/status') return response({ setup_required: false })
      if (url === '/api/auth/registration/challenge') return response({ challenge: 'synthetic-one-time-challenge' })
      if (url === '/api/auth/register') return response({ username: 'new.user' }, 201)
      if (url === '/api/auth/me') return response({ id: 8, username: 'new.user', display_name: null })
      if (url === '/api/dashboard') return response({ today_date: '2026-10-19', task_counts: {}, today_tasks: [], completed_today_tasks: [], overdue_tasks: [], unscheduled_tasks: [], recent_tasks: [], review_count: 0 })
      if (url === '/api/tasks') return response([])
      if (url === '/api/agent/proposals') return response([])
      if (url.startsWith('/api/agent/chat/conversations?')) return response({ conversations: [] })
      if (url === '/api/system/info') return response({ app_version: '0.4.0' })
      if (url === '/api/account/appearance') return response({ theme: 'classic', background_darkness: 48, background_blur: 3, background_opacity: 48 })
      return response({ detail: 'Not found' }, 404)
    }) as typeof fetch

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '邀请码注册' }))
    fireEvent.change(screen.getByLabelText('邀请码'), { target: { value: 'synthetic-invitation-never-log' } })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: ' New.User ' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'Strong-Password-2026!' } })
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'Strong-Password-2026!' } })
    fireEvent.click(screen.getByRole('button', { name: '创建账户' }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })

    const challengeRequest = requests.find((request) => request.url === '/api/auth/registration/challenge')
    const registrationRequest = requests.find((request) => request.url === '/api/auth/register')
    expect(challengeRequest?.init.credentials).toBe('include')
    expect(new Headers(registrationRequest?.init.headers).get('X-Registration-CSRF')).toBe('synthetic-one-time-challenge')
    expect(JSON.parse(String(registrationRequest?.init.body))).toEqual({ invitation_code: 'synthetic-invitation-never-log', username: 'new.user', password: 'Strong-Password-2026!' })
    expect(requests.some((request) => request.url === '/api/auth/me')).toBe(true)
    expect(localStorage.getItem('personal-os-registration')).toBeNull()
    expect(document.body.textContent).not.toContain('synthetic-invitation-never-log')
  })

  it('does not automatically retry an uncertain registration response and explains login recovery', async () => {
    let registrationCalls = 0
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url === '/api/auth/status') return response({ setup_required: false })
      if (url === '/api/auth/registration/challenge') return response({ challenge: 'one-time-challenge' })
      if (url === '/api/auth/register') { registrationCalls += 1; throw new TypeError('network disconnected') }
      return response({})
    }) as typeof fetch
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: '邀请码注册' }))
    fireEvent.change(screen.getByLabelText('邀请码'), { target: { value: 'synthetic-invitation' } })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'new.user' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'Strong-Password-2026!' } })
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'Strong-Password-2026!' } })
    fireEvent.click(screen.getByRole('button', { name: '创建账户' }))
    expect((await screen.findByRole('alert')).textContent).toContain('请先尝试使用该用户名和密码登录')
    expect(registrationCalls).toBe(1)
  })

  it('logs in, creates a task with CSRF, and reloads the saved task', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: '欢迎回来' })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'tester' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'a-secure-password' } })
    fireEvent.click(screen.getByRole('button', { name: /进入 Personal OS/ }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })
    fireEvent.click(screen.getByRole('button', { name: '统一任务' }))
    fireEvent.click(await screen.findByText('新建任务'))
    const input = await screen.findByLabelText('名称')
    fireEvent.change(input, { target: { value: 'Test-authenticated task' } })
    fireEvent.click(screen.getByRole('button', { name: /保存任务/ }))
    expect((await screen.findAllByText('Test-authenticated task')).length).toBeGreaterThanOrEqual(1)
    await waitFor(() => {
      const calls = vi.mocked(globalThis.fetch).mock.calls
      const createCall = calls.find(([url, init]) => url === '/api/tasks' && init?.method === 'POST')
      expect(createCall).toBeTruthy()
      expect(new Headers(createCall?.[1]?.headers).get('X-CSRF-Token')).toBe('test-csrf')
    })
    fireEvent.click(screen.getByRole('button', { name: '总览' }))
    expect(await screen.findByRole('heading', { name: '今日任务' })).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /Test-authenticated task/ }).length).toBeGreaterThanOrEqual(1)
  })

  it('opens account settings and saves an editable profile without changing the account id', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: '欢迎回来' })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'tester' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'a-secure-password' } })
    fireEvent.click(screen.getByRole('button', { name: /进入 Personal OS/ }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })
    fireEvent.click(screen.getByRole('button', { name: '设置' }))
    const displayName = await screen.findByLabelText('显示名称')
    fireEvent.change(displayName, { target: { value: 'My Personal OS' } })
    fireEvent.click(screen.getByRole('button', { name: '保存账号资料' }))
    expect(await screen.findByText('账户资料已更新；账号 ID 保持不变。')).toBeTruthy()
    const patch = vi.mocked(globalThis.fetch).mock.calls.find(([url, init]) => url === '/api/account/profile' && init?.method === 'PATCH')
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ username: 'tester', display_name: 'My Personal OS' })
  })

  it('applies and persists the selected visual theme through the appearance settings', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: '欢迎回来' })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'tester' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'a-secure-password' } })
    fireEvent.click(screen.getByRole('button', { name: /进入 Personal OS/ }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })
    fireEvent.click(screen.getByRole('button', { name: '设置' }))
    fireEvent.click(await screen.findByRole('tab', { name: '外观与主题' }))
    fireEvent.click(await screen.findByRole('radio', { name: /Cyberpunk Night/ }))
    fireEvent.click(screen.getByRole('button', { name: '应用并保存' }))
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('cyberpunk-night'))
    expect(await screen.findByText('主题偏好已保存，会在刷新和重新登录后保留。')).toBeTruthy()
    const patch = vi.mocked(globalThis.fetch).mock.calls.find(([url, init]) => url === '/api/account/appearance' && init?.method === 'PATCH')
    expect(JSON.parse(String(patch?.[1]?.body))).toMatchObject({ theme: 'cyberpunk-night', background_darkness: 48, background_blur: 3, background_opacity: 48 })
  })

  it('removes the legacy AI daily-plan panel and keeps the daily settlement summary on dashboard', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: '欢迎回来' })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'tester' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'a-secure-password' } })
    fireEvent.click(screen.getByRole('button', { name: /进入 Personal OS/ }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })
    await screen.findByRole('heading', { name: '最近对话' })
    expect(screen.queryByText('AI 今日计划')).toBeNull()
    expect(screen.queryByRole('button', { name: '生成 DeepSeek 草案' })).toBeNull()
    expect(screen.getByRole('heading', { name: '每日复盘' })).toBeTruthy()
    expect(screen.getByText('暂无可复盘记录')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'AI 工作区' }))
    expect(await screen.findByRole('heading', { name: '对话工作区' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '生成 DeepSeek 草案' })).toBeNull()
    expect(screen.getByPlaceholderText('在「通用」继续对话…')).toBeTruthy()
  })

  it('clearly disables Web instance backup management for ordinary accounts', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: '欢迎回来' })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'tester' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'a-secure-password' } })
    fireEvent.click(screen.getByRole('button', { name: /进入 Personal OS/ }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })
    fireEvent.click(screen.getByRole('button', { name: '设置' }))
    fireEvent.click(await screen.findByRole('tab', { name: '数据与备份' }))
    expect(await screen.findByText(/Web 实例备份管理当前不可用/)).toBeTruthy()
    expect(screen.getByText(/Web 全库迁移导出已禁用/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /立即创建并校验本机快照/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /生成并下载加密迁移包/ })).toBeNull()
    expect(vi.mocked(globalThis.fetch).mock.calls.some(([url]) => url === '/api/data/backups')).toBe(false)
  })

  it('shows exact model usage records and saves per-run request guards', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: '欢迎回来' })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'tester' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'a-secure-password' } })
    fireEvent.click(screen.getByRole('button', { name: /进入 Personal OS/ }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })
    fireEvent.click(screen.getByRole('button', { name: '设置' }))
    fireEvent.click(await screen.findByRole('tab', { name: 'AI 用量与成本' }))
    expect(await screen.findByText('输入 11')).toBeTruthy()
    fireEvent.click(screen.getByText('最近模型请求（最多 50 条）'))
    expect(await screen.findByText(/Run #88 · 请求 1 · deepseek\/deepseek-flash/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('最多工具调用'), { target: { value: '3' } })
    fireEvent.submit(screen.getByRole('button', { name: '保存限制' }).closest('form')!)
    await waitFor(() => expect(vi.mocked(globalThis.fetch).mock.calls.some(([url, init]) => url === '/api/settings/ai/usage/budget' && init?.method === 'PUT')).toBe(true))
  })

  it('shows published releases separately from planned versions in system settings', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: '欢迎回来' })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'tester' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'a-secure-password' } })
    fireEvent.click(screen.getByRole('button', { name: /进入 Personal OS/ }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })
    fireEvent.click(screen.getByRole('button', { name: '设置' }))
    fireEvent.click(await screen.findByRole('tab', { name: '版本与工程记录' }))
    expect(await screen.findByText('0.3.0')).toBeTruthy()
    const releaseCards = document.querySelectorAll('.release-card')
    expect(releaseCards).toHaveLength(3)
    expect(releaseCards[0].textContent).toContain('AI 对话与任务工作台')
    expect(releaseCards[1].textContent).toContain('账号与数据管理')
    expect(screen.getByText('v0.4.0')).toBeTruthy()
    expect(screen.getByText('规划中')).toBeTruthy()
    expect(screen.getByTestId('engineering-journal')).toBeTruthy()
    expect(screen.getByText('v0.4.0 · Source Release Candidate')).toBeTruthy()
    expect(screen.getByText(/源码候选等待发布审批；v0.3.3 历史发布记录保留/)).toBeTruthy()
    expect(document.querySelectorAll('.release-date')).toHaveLength(3)
    expect(document.querySelectorAll('.release-date')[0].textContent).toContain('2026-10-13')
    fireEvent.click(document.querySelectorAll('.release-card summary')[0])
    expect(await screen.findByText('测试架构细节')).toBeTruthy()
  })

  it('creates a domain conversation, sends its stable id, and reloads the stored assistant reply', async () => {
    render(<App />)
    await screen.findByRole('heading', { name: '欢迎回来' })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'tester' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'a-secure-password' } })
    fireEvent.click(screen.getByRole('button', { name: /进入 Personal OS/ }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })
    fireEvent.click(screen.getByRole('button', { name: 'AI 工作区' }))
    expect(await screen.findByRole('heading', { name: '对话工作区' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /新建对话/ }))
    const input = await screen.findByPlaceholderText(/在「通用」继续对话/)
    fireEvent.change(input, { target: { value: 'First turn' } })
    fireEvent.click(screen.getByRole('button', { name: /^发送/ }))
    expect(await screen.findByText(/mock response/)).toBeTruthy()
    const chatCall = vi.mocked(globalThis.fetch).mock.calls.find(([url, init]) => url === '/api/agent/chat' && init?.method === 'POST')
    expect(JSON.parse(String(chatCall?.[1]?.body)).conversation_id).toBe('11111111-1111-4111-8111-111111111111')
    expect(JSON.parse(String(chatCall?.[1]?.body)).domain).toBe('general')
  })

  it('searches the domain conversation list and sends title changes through the API', async () => {
    conversations = [{ id: '11111111-1111-4111-8111-111111111111', title: 'Existing research', domain: 'global', is_pinned: false, archived: false, deleted: false, last_message_preview: 'saved excerpt' }]
    render(<App />)
    await screen.findByRole('heading', { name: '欢迎回来' })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'tester' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'a-secure-password' } })
    fireEvent.click(screen.getByRole('button', { name: /进入 Personal OS/ }))
    await screen.findByRole('heading', { name: '今天，把重要的事变得可验证。' })
    fireEvent.click(screen.getByRole('button', { name: 'AI 工作区' }))
    fireEvent.click(await screen.findByRole('button', { name: '全球信息' }))
    expect((await screen.findAllByText('Existing research')).length).toBeGreaterThanOrEqual(2)
    const search = screen.getByPlaceholderText('搜索标题和消息内容')
    fireEvent.change(search, { target: { value: 'research' } })
    await waitFor(() => expect(vi.mocked(globalThis.fetch).mock.calls.some(([url]) => String(url).includes('q=research'))).toBe(true))
    vi.spyOn(window, 'prompt').mockReturnValue('Renamed research')
    fireEvent.click(screen.getByRole('button', { name: '重命名会话' }))
    await waitFor(() => expect(vi.mocked(globalThis.fetch).mock.calls.some(([url, init]) => url === '/api/agent/chat/conversations/11111111-1111-4111-8111-111111111111' && init?.method === 'PATCH' && JSON.parse(String(init.body)).title === 'Renamed research')).toBe(true))
  })

  it('collapses the rail, loads only the next history page, and opens a real linked task', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    conversations = [{ id, title: 'Research thread', domain: 'general', is_pinned: false, archived: false, deleted: false }]
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input)
      const respond = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url === `/api/agent/chat/conversations/${id}`) return respond({ id, messages: [{ id: 21, role: 'user', content: 'new question' }, { id: 22, role: 'assistant', content: 'new answer' }], has_more: true, next_before_id: 21, linked_tasks: [{ id: 31, title: 'Linked exercise', status: 'TODO', module: 'stem' }] })
      if (url === `/api/agent/chat/conversations/${id}?before_id=21`) return respond({ id, messages: [{ id: 1, role: 'user', content: 'old question' }, { id: 2, role: 'assistant', content: 'old answer' }], has_more: false, next_before_id: null })
      return originalFetch(input, init)
    }) as typeof fetch
    const onOpenTask = vi.fn()
    render(<ChatWorkspace initialConversationId={id} onOpenTask={onOpenTask} onBack={() => {}} />)
    expect(await screen.findByText('new answer')).toBeTruthy()
    expect(screen.queryByText('old answer')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '折叠会话侧栏' }))
    expect(localStorage.getItem('personal-os-chat-sidebar')).toBe('collapsed')
    expect(screen.getByRole('button', { name: '展开会话侧栏' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '加载更早消息' }))
    expect(await screen.findByText('old answer')).toBeTruthy()
    expect(screen.getAllByText('new answer')).toHaveLength(1)
    fireEvent.click(screen.getByText('关联任务 · 1'))
    fireEvent.click(await screen.findByRole('button', { name: /Linked exercise/ }))
    expect(onOpenTask).toHaveBeenCalledWith(31)
    expect(vi.mocked(globalThis.fetch).mock.calls.filter(([url]) => String(url).includes('before_id=21'))).toHaveLength(1)
  })
})
