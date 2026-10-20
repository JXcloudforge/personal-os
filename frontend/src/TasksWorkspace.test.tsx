import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TasksWorkspace } from './TasksWorkspace'

describe('AI-first task manager workspace', () => {
  beforeEach(() => {
    document.cookie = 'personal_os_csrf=test-csrf; path=/'
    vi.stubGlobal('crypto', { randomUUID: () => 'a9cc660f-972d-4f7d-951d-4633a3377011' })
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/tasks?')) return response([{ id: 42, title: 'Python Agent 最小练习', module: 'ai_engineering', status: 'TODO', priority: 2, start_date: '2026-10-13', due_date: '2026-10-13', is_pinned: false, archived: false, deleted: false, evidence_count: 0 }])
      if (url === '/api/task-manager/pending-confirmations') return response([])
      if (url.includes('/api/agent/chat/conversations')) return response({ enabled: true, conversations: [], messages: [] })
      if (url === '/api/dashboard') return response({ today_date: '2026-10-13' })
      if (url.startsWith('/api/agent/proposals')) return response([])
      if (url === '/api/task-manager/chat' && init.method === 'POST') return response({ status: 'no_write', write_authorized: false, content: '本轮未获任务写入授权。', operation_id: 'a9cc660f-972d-4f7d-951d-4633a3377011', conversation_id: null })
      return response({ detail: 'unexpected request' })
    }) as typeof fetch
  })

  it('shows a compact timeline and submits natural-language instructions to the dedicated endpoint', async () => {
    render(<TasksWorkspace onOpenConversation={() => {}} onOpenReview={() => {}} />)
    expect(await screen.findByText('Python Agent 最小练习')).toBeTruthy()
    expect(screen.getByText('2026-10-13')).toBeTruthy()
    expect(screen.getAllByText('ai_engineering', { exact: true }).length).toBeGreaterThan(0)
    expect(screen.getByText('预计分钟').closest('details')?.hasAttribute('open')).toBe(false)
    fireEvent.change(screen.getByRole('textbox', { name: 'AI 任务管理员输入' }), { target: { value: '建议一下我今天先做什么' } })
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    expect(await screen.findByText('本轮未获任务写入授权。')).toBeTruthy()
    await waitFor(() => expect(vi.mocked(globalThis.fetch).mock.calls.some(([url, init]) => String(url) === '/api/task-manager/chat' && init?.method === 'POST')).toBe(true))
    const request = vi.mocked(globalThis.fetch).mock.calls.find(([url, init]) => String(url) === '/api/task-manager/chat' && init?.method === 'POST')
    expect(JSON.parse(String(request?.[1]?.body)).mode).toBe('create')
  })

  it('creates directly only in create mode and refreshes the database-backed timeline', async () => {
    let created = false
    const task = { id: 77, title: '完成德语 Agent 开发', module: 'ai_engineering', status: 'TODO', priority: 2, start_date: '2026-10-13', due_date: '2026-10-13', is_pinned: false, archived: false, deleted: false, evidence_count: 0 }
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/tasks?')) return response(created ? [task] : [])
      if (url === '/api/task-manager/pending-confirmations') return response([])
      if (url.startsWith('/api/agent/chat/conversations?')) return response({ enabled: true, conversations: [{ id: 'c1111111-1111-4111-8111-111111111111', title: '任务管理员', domain: 'tasks', updated_at: '2026-10-13' }] })
      if (url === '/api/agent/chat/conversations/c1111111-1111-4111-8111-111111111111') return response({ messages: created ? [{ id: 2, role: 'assistant', content: '已创建任务 #77 完成德语 Agent 开发', created_at: '2026-10-13' }] : [] })
      if (url === '/api/dashboard') return response({ today_date: '2026-10-13' })
      if (url.startsWith('/api/agent/proposals')) return response([])
      if (url === '/api/task-manager/chat' && init.method === 'POST') {
        expect(JSON.parse(String(init.body)).mode).toBe('create')
        created = true
        return response({ status: 'completed', conversation_id: 'c1111111-1111-4111-8111-111111111111', operation_id: 'op-create', tasks: [task], actions: [{ kind: 'create', task_id: task.id }], content: '已创建任务 #77。' })
      }
      return response({ detail: `unexpected request ${url}` })
    }) as typeof fetch
    render(<TasksWorkspace onOpenConversation={() => {}} onOpenReview={() => {}} />)
    const input = await screen.findByRole('textbox', { name: 'AI 任务管理员输入' })
    fireEvent.change(input, { target: { value: '创建任务：完成德语 Agent 开发' } })
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    expect(await screen.findByText(task.title)).toBeTruthy()
    expect(screen.getByText(/已创建 1 项任务：#77 完成德语 Agent 开发/)).toBeTruthy()
    expect(created).toBe(true)
  })

  it('assigns a new operation ID to every new submission and resolves failed operation before safe retry', async () => {
    let idCounter = 0
    vi.stubGlobal('crypto', { randomUUID: () => `00000000-0000-4000-8000-${String(++idCounter).padStart(12, '0')}` })
    let postCount = 0
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/tasks?')) return response([])
      if (url === '/api/task-manager/pending-confirmations') return response([])
      if (url.startsWith('/api/agent/chat/conversations?')) return response({ enabled: true, conversations: [] })
      if (url === '/api/dashboard') return response({ today_date: '2026-10-13' })
      if (url.startsWith('/api/agent/proposals')) return response([])
      if (url === '/api/task-manager/chat' && init.method === 'POST') {
        postCount++
        return postCount === 1 ? response({ detail: { code: 'MODEL_TIMEOUT', message: 'timeout' } }, 504) : response({ status: 'no_write', mode: 'create', actions: [], tasks: [], content: '没有创建任务。', operation_id: JSON.parse(String(init.body)).operation_id, conversation_id: null })
      }
      if (url.startsWith('/api/task-manager/operations/')) return response({ status: 'failed', failure_code: 'MODEL_TIMEOUT', tasks: [], actions: [] })
      return response({ detail: `unexpected request ${url}` }, 404)
    }) as typeof fetch
    render(<TasksWorkspace onOpenConversation={() => {}} onOpenReview={() => {}} />)
    const input = await screen.findByRole('textbox', { name: 'AI 任务管理员输入' })
    fireEvent.change(input, { target: { value: '创建一项测试任务' } })
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    expect(await screen.findByText(/模型请求超时/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    await screen.findByText('本次没有写入任务。')
    const requests = vi.mocked(globalThis.fetch).mock.calls.filter(([url, init]) => String(url) === '/api/task-manager/chat' && init?.method === 'POST')
    expect(requests).toHaveLength(2)
    expect(JSON.parse(String(requests[0][1]?.body)).operation_id).not.toBe(JSON.parse(String(requests[1][1]?.body)).operation_id)
  })

  it('shows a task-count validation error instead of a generic success or internal message', async () => {
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/tasks?')) return response([])
      if (url === '/api/task-manager/pending-confirmations') return response([])
      if (url.startsWith('/api/agent/chat/conversations?')) return response({ enabled: true, conversations: [] })
      if (url === '/api/dashboard') return response({ today_date: '2026-10-13' })
      if (url.startsWith('/api/agent/proposals')) return response([])
      if (url === '/api/task-manager/chat' && init.method === 'POST') return response({ detail: { code: 'TASK_COUNT_MISMATCH', message: 'Required task tool failed' } }, 502)
      if (url.startsWith('/api/task-manager/operations/')) return response({ status: 'failed', failure_code: 'TASK_COUNT_MISMATCH', tasks: [], actions: [] })
      return response({ detail: `unexpected request ${url}` }, 404)
    }) as typeof fetch
    render(<TasksWorkspace onOpenConversation={() => {}} onOpenReview={() => {}} />)
    fireEvent.change(await screen.findByRole('textbox', { name: 'AI 任务管理员输入' }), { target: { value: '创建两个任务：甲；乙' } })
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    expect(await screen.findByText(/任务数量与原指令不一致/)).toBeTruthy()
  })

  it('opens trashed task details through the explicit deleted-record route and confirms permanent cleanup', async () => {
    const task = { id: 91, title: 'Old task', module: 'projects', status: 'TODO', priority: 2, start_date: '2026-10-13', due_date: '2026-10-13', expected_duration_days: 1, is_pinned: false, archived: false, deleted: true, evidence_count: 0 }
    let purged = false
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(window, 'prompt').mockReturnValue(task.title)
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/tasks?')) return response(url.includes('include_deleted=true') && !purged ? [task] : [])
      if (url === `/api/tasks/${task.id}?include_deleted=true`) return response({ ...task, description: '', acceptance_criteria: '', evidence: [], history: [], date_history: [], links: [] })
      if (url === `/api/tasks/${task.id}/permanent-delete-preview`) return response({ task_id: task.id, title: task.title, can_purge: true, blockers: [], cleanup: { evidence: 0, status_history: 0, date_history: 0, conversation_links: 0 } })
      if (url === `/api/tasks/${task.id}/permanent-delete` && init.method === 'POST') { purged = true; return response({ task_id: task.id, purged: true, idempotent_replay: false }) }
      if (url === '/api/task-manager/pending-confirmations') return response([])
      if (url.startsWith('/api/agent/chat/conversations')) return response({ enabled: true, conversations: [], messages: [] })
      if (url === '/api/dashboard') return response({ today_date: '2026-10-13' })
      if (url.startsWith('/api/agent/proposals')) return response([])
      return response({ detail: 'unexpected request' }, 404)
    }) as typeof fetch

    render(<TasksWorkspace onOpenConversation={() => {}} onOpenReview={() => {}} />)
    const timeline = screen.getByRole('heading', { name: '任务时间线' }).closest('.task-browser')
    fireEvent.click(within(timeline as HTMLElement).getAllByRole('button', { name: '最近删除' })[0])
    fireEvent.click(await screen.findByText('Old task'))
    await waitFor(() => expect(vi.mocked(globalThis.fetch).mock.calls.some(([url]) => String(url) === `/api/tasks/${task.id}?include_deleted=true`)).toBe(true))
    expect(await screen.findByText(/此任务已软删除/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: `彻底删除 ${task.title}` }))
    expect(await screen.findByText(/任务已从当前数据库彻底删除/)).toBeTruthy()
    expect(purged).toBe(true)
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(window.prompt).toHaveBeenCalledTimes(1)
  })
})
