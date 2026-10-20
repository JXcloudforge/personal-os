import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChatWorkspace } from './ChatWorkspace'

describe('chat trash lifecycle UI', () => {
  it('copies the original text for the selected user or assistant message only', async () => {
    const conversationId = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/agent/chat/conversations?')) return response({ enabled: true, conversations: [{ id: conversationId, title: 'Copy test', domain: 'general', is_pinned: false, archived: false, deleted: false }] })
      if (url === `/api/agent/chat/conversations/${conversationId}`) return response({ messages: [
        { id: 101, role: 'user', content: 'Keep **my exact question**.' },
        { id: 102, role: 'assistant', content: 'Keep `the raw markdown`.' },
      ], has_more: false, linked_tasks: [] })
      if (url === '/api/tasks') return response([])
      return response({ detail: 'unexpected request' })
    }) as typeof fetch

    render(<ChatWorkspace initialConversationId={conversationId} onBack={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: '复制用户消息' }))
    await waitFor(() => expect(writeText).toHaveBeenNthCalledWith(1, 'Keep **my exact question**.'))
    fireEvent.click(screen.getByRole('button', { name: '复制AI消息' }))
    await waitFor(() => expect(writeText).toHaveBeenNthCalledWith(2, 'Keep `the raw markdown`.'))
    expect(writeText).toHaveBeenCalledTimes(2)
    expect(screen.getAllByRole('status').map((status) => status.textContent)).toEqual(['复制成功。', '复制成功。'])
  })

  it('reports empty messages and clipboard permission failures without claiming success', async () => {
    const conversationId = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
    const writeText = vi.fn().mockRejectedValue(new DOMException('permission denied', 'NotAllowedError'))
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/agent/chat/conversations?')) return response({ enabled: true, conversations: [{ id: conversationId, title: 'Copy failures', domain: 'general', is_pinned: false, archived: false, deleted: false }] })
      if (url === `/api/agent/chat/conversations/${conversationId}`) return response({ messages: [
        { id: 103, role: 'user', content: '   ' },
        { id: 104, role: 'assistant', content: 'Private text' },
      ], has_more: false, linked_tasks: [] })
      if (url === '/api/tasks') return response([])
      return response({ detail: 'unexpected request' })
    }) as typeof fetch

    render(<ChatWorkspace initialConversationId={conversationId} onBack={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: '复制用户消息' }))
    expect(await screen.findByText('消息内容为空，无法复制。')).toBeTruthy()
    expect(writeText).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '复制AI消息' }))
    expect(await screen.findByText('复制失败，请检查浏览器剪贴板权限。')).toBeTruthy()
    expect(writeText).toHaveBeenCalledWith('Private text')
    expect(screen.queryByText('复制成功。')).toBeNull()
  })

  it('keeps knowledge retrieval off by default and shows real source citations after opt-in', async () => {
    const conversationId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    document.cookie = 'personal_os_csrf=test-csrf; path=/'
    const sent: any[] = []
    let persisted: any[] = []
    const onOpenKnowledge = vi.fn()
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/agent/chat/conversations?')) return response({ enabled: true, conversations: [{ id: conversationId, title: 'Notes', domain: 'stem', is_pinned: false, archived: false, deleted: false }] })
      if (url === `/api/agent/chat/conversations/${conversationId}`) return response({ messages: persisted, has_more: false, linked_tasks: [] })
      if (url === '/api/tasks') return response([])
      if (url === '/api/agent/chat' && init.method === 'POST') {
        const body = JSON.parse(String(init.body)); sent.push(body)
        if (body.use_knowledge_base) persisted = [{ id: 22, role: 'assistant', content: 'Answer grounded in the note.', status: 'completed', knowledge_status: 'completed', knowledge_sources: [{ id: 712, title: 'Circuit note', summary: 'Verified circuit basics', domain: 'stem', sources: [] }] }]
        return response({ content: 'Answer grounded in the note.', knowledge_status: body.use_knowledge_base ? 'completed' : 'disabled', knowledge_sources: body.use_knowledge_base ? [{ id: 712, title: 'Circuit note', summary: 'Verified circuit basics', domain: 'stem', sources: [] }] : [], conversation_id: conversationId, user_message_id: sent.length * 2, assistant_message_id: sent.length * 2 + 1 })
      }
      return response({ detail: 'unexpected request' })
    }) as typeof fetch
    render(<ChatWorkspace initialDomain="stem" initialConversationId={conversationId} onOpenKnowledge={onOpenKnowledge} onBack={() => {}} />)
    const knowledgeToggle = await screen.findByRole('checkbox', { name: '使用我的知识库' })
    expect((knowledgeToggle as HTMLInputElement).checked).toBe(false)
    fireEvent.change(screen.getByPlaceholderText(/继续对话/), { target: { value: 'What do my notes say?' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    await waitFor(() => expect(sent.length).toBe(1))
    expect(sent[0].use_knowledge_base).toBe(false)
    fireEvent.click(knowledgeToggle)
    fireEvent.change(screen.getByPlaceholderText(/继续对话/), { target: { value: 'Explain my circuit note' } })
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    await waitFor(() => expect(sent.length).toBe(2))
    expect(sent[1].use_knowledge_base).toBe(true)
    expect(sent[1].consent_granted).toBe(true)
    fireEvent.click(await screen.findByRole('button', { name: /知识卡片 #712 · Circuit note/ }))
    expect(onOpenKnowledge).toHaveBeenCalledWith(712, 'stem')
  })

  it('passes the exact persisted assistant message to the knowledge draft action', async () => {
    const conversationId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    document.cookie = 'personal_os_csrf=test-csrf; path=/'
    const onSaveKnowledge = vi.fn()
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input)
      const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/agent/chat/conversations?')) return response({ enabled: true, conversations: [{ id: conversationId, title: 'Tool calling', domain: 'global', is_pinned: false, archived: false, deleted: false }] })
      if (url === `/api/agent/chat/conversations/${conversationId}`) return response({ messages: [{ id: 91, role: 'assistant', content: 'The backend validates and executes the requested function.', status: 'completed', run_id: 13 }], has_more: false, linked_tasks: [] })
      if (url === '/api/tasks') return response([])
      return response({ detail: 'unexpected request' })
    }) as typeof fetch
    render(<ChatWorkspace initialDomain="global" initialConversationId={conversationId} onSaveKnowledge={onSaveKnowledge} onBack={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: '保存为知识' }))
    expect(onSaveKnowledge).toHaveBeenCalledWith({ content: 'The backend validates and executes the requested function.', domain: 'global', conversationId, messageId: 91, selectedText: null, openRefinement: false })
    expect(screen.getByRole('button', { name: 'AI 提炼' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'AI 提炼' }))
    expect(onSaveKnowledge).toHaveBeenLastCalledWith({ content: 'The backend validates and executes the requested function.', domain: 'global', conversationId, messageId: 91, selectedText: null, openRefinement: true })
  })

  it('offers confirmed permanent cleanup for a deleted conversation without touching linked records', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    const conversation = { id, title: 'Old research', domain: 'general', is_pinned: false, archived: false, deleted: true, last_message_preview: 'saved message preview' }
    let purged = false
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(window, 'prompt').mockReturnValue(conversation.title)
    document.cookie = 'personal_os_csrf=test-csrf; path=/'
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input)
      const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/agent/chat/conversations?')) return response({ enabled: true, conversations: url.includes('include_deleted=true') && !purged ? [conversation] : [] })
      if (url === '/api/tasks') return response([])
      if (url === `/api/agent/chat/conversations/${id}/permanent-delete` && init.method === 'POST') { purged = true; return response({ id, purged: true, formal_tasks_preserved: true }) }
      return response({ detail: { code: 'UNEXPECTED_TEST_REQUEST' } }, 404)
    }) as typeof fetch

    render(<ChatWorkspace onBack={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '最近删除' }))
    fireEvent.click(await screen.findByRole('button', { name: `永久清除 ${conversation.title}` }))
    expect(await screen.findByText(/会话正文已从当前数据库永久清除/)).toBeTruthy()
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(window.prompt).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(vi.mocked(globalThis.fetch).mock.calls.some(([url, init]) => String(url).endsWith('/permanent-delete') && init?.method === 'POST')).toBe(true))
    expect(purged).toBe(true)
  })
})
