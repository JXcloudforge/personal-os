import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { KnowledgeWorkspace } from './KnowledgeWorkspace'

describe('Knowledge inbox interactions', () => {
  it('saves an editable card from an actual chat message and receives its persisted id', async () => {
    document.cookie = 'personal_os_csrf=test-csrf; path=/'
    const saved = { id: 73, title: 'Function Calling', summary: null, content_markdown: 'The backend executes tools.', domain: 'ai_engineering', knowledge_type: 'Concept', review_status: 'DRAFT', is_pinned: false, archived: false, deleted: false, tags: ['agents'], sources: [{ id: 1, evidence_kind: 'MODEL_GENERATED', conversation_id: 'conversation-1', message_id: 42, fetch_status: 'model_message' }], task_ids: [] }
    const calls: Array<{ url: string; init?: RequestInit }> = []
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input); calls.push({ url, init })
      const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/knowledge?')) return response(saved.id && calls.some((call) => call.url === '/api/knowledge' && call.init?.method === 'POST') ? [saved] : [])
      if (url === '/api/tasks') return response([])
      if (url === '/api/knowledge' && init.method === 'POST') return response(saved)
      return response({ detail: 'unexpected request' })
    }) as typeof fetch

    render(<KnowledgeWorkspace initialDomain="ai_engineering" draftFromMessage={{ content: '# Function Calling\n\nThe backend executes tools.', domain: 'ai_engineering', conversationId: 'conversation-1', messageId: 42 }} onOpenTask={() => {}} onBack={() => {}} />)
    expect(await screen.findByText(/源消息 #42/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('标签（逗号分隔）'), { target: { value: 'agents' } })
    fireEvent.click(screen.getByRole('button', { name: '保存为知识' }))
    expect(await screen.findByText(/知识卡片 #73 已保存/)).toBeTruthy()
    expect(calls.some((call) => call.url === '/api/knowledge/refine-from-message')).toBe(false)
    await waitFor(() => expect(calls.some((call) => call.url === '/api/knowledge' && call.init?.method === 'POST')).toBe(true))
    const payload = JSON.parse(String(calls.find((call) => call.url === '/api/knowledge' && call.init?.method === 'POST')?.init?.body))
    expect(payload).toMatchObject({ title: 'Function Calling', domain: 'ai_engineering', conversation_id: 'conversation-1', message_id: 42, tags: ['agents'] })
    expect(screen.getByText('Function Calling', { selector: '.knowledge-detail-head h3' })).toBeTruthy()
  })

  it('calls AI refinement only after an explicit click, keeps a candidate separate, then adopts it into the draft', async () => {
    document.cookie = 'personal_os_csrf=test-csrf; path=/'
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const saved = { id: 74, title: '提炼后的标题', summary: '提炼摘要', content_markdown: '结构化正文', domain: 'ai_engineering', knowledge_type: 'Concept', review_status: 'DRAFT', is_pinned: false, archived: false, deleted: false, tags: ['keep-me'], sources: [{ id: 2, evidence_kind: 'MODEL_GENERATED', conversation_id: 'conversation-1', message_id: 42, fetch_status: 'model_message' }], task_ids: [] }
    const calls: Array<{ url: string; init?: RequestInit }> = []
    globalThis.fetch = vi.fn(async (input, init = {}) => {
      const url = String(input); calls.push({ url, init })
      const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.startsWith('/api/knowledge?')) return response([])
      if (url === '/api/tasks') return response([])
      if (url === '/api/knowledge/refine-from-message') return response({ candidate: { worth_saving: true, title: '提炼后的标题', summary: '提炼摘要', content_markdown: '结构化正文', limitations: ['需复核来源'] }, run_id: 901, usage: { input_tokens: 55, output_tokens: 32, status: 'completed' } })
      if (url === '/api/knowledge' && init.method === 'POST') return response(saved)
      return response({ detail: 'unexpected request' })
    }) as typeof fetch

    render(<KnowledgeWorkspace initialDomain="stem" draftFromMessage={{ content: 'Original persisted assistant answer.', domain: 'stem', conversationId: 'conversation-1', messageId: 42, selectedText: 'persisted assistant answer', openRefinement: true }} onOpenTask={() => {}} onBack={() => {}} />)
    expect(calls.some((call) => call.url === '/api/knowledge/refine-from-message')).toBe(false)
    fireEvent.change(screen.getByLabelText('标签（逗号分隔）'), { target: { value: 'keep-me' } })
    fireEvent.change(screen.getByLabelText('审阅状态'), { target: { value: 'REVIEWED' } })
    fireEvent.click(await screen.findByRole('button', { name: /AI 提炼标题、摘要和正文/ }))
    await screen.findByRole('button', { name: '采用候选到草稿' })
    const refineCall = calls.find((call) => call.url === '/api/knowledge/refine-from-message')
    expect(refineCall).toBeTruthy()
    expect(JSON.parse(String(refineCall?.init?.body))).toMatchObject({ conversation_id: 'conversation-1', message_id: 42, selected_text: 'persisted assistant answer', sensitive_data_consent: true })
    await waitFor(() => expect((screen.getByLabelText('领域') as HTMLSelectElement).value).toBe('stem'))
    expect((screen.getByLabelText('审阅状态') as HTMLSelectElement).value).toBe('REVIEWED')
    expect((screen.getByLabelText('标签（逗号分隔）') as HTMLInputElement).value).toBe('keep-me')
    fireEvent.click(screen.getByRole('button', { name: '采用候选到草稿' }))
    expect((screen.getByLabelText('标题') as HTMLInputElement).value).toBe('提炼后的标题')
    expect((screen.getByLabelText('简短摘要') as HTMLTextAreaElement).value).toBe('提炼摘要')
    expect((screen.getByLabelText('正文（Markdown）') as HTMLTextAreaElement).value).toBe('结构化正文\n\n## 限制与待核验\n\n- 需复核来源')
    expect((screen.getByLabelText('领域') as HTMLSelectElement).value).toBe('stem')
    expect((screen.getByLabelText('审阅状态') as HTMLSelectElement).value).toBe('REVIEWED')
    expect((screen.getByLabelText('标签（逗号分隔）') as HTMLInputElement).value).toBe('keep-me')
    fireEvent.click(screen.getByRole('button', { name: '保存为知识' }))
    expect(await screen.findByText(/知识卡片 #74 已保存/)).toBeTruthy()
    const savePayload = JSON.parse(String(calls.find((call) => call.url === '/api/knowledge' && call.init?.method === 'POST')?.init?.body))
    expect(savePayload).toMatchObject({ title: '提炼后的标题', domain: 'stem', review_status: 'REVIEWED', tags: ['keep-me'], conversation_id: 'conversation-1', message_id: 42 })
  })
})
