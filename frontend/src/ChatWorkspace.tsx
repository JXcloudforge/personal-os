import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Archive, ArrowDownToLine, BookOpen, ChevronLeft, ChevronRight, Copy, Pin, Plus, RotateCcw, Search, Send, Sparkles, Trash2, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { agentToolStatusLabel } from './agentToolLabels'

export type ChatDomain = 'general' | 'bilingual' | 'global' | 'health' | 'stem' | 'projects' | 'finance' | 'reviews'
type KnowledgeSource = { id: number; title: string; summary?: string | null; domain: ChatDomain; excerpt?: string; sources?: { id: number; evidence_kind: string; url?: string | null; title?: string | null; fetch_status: string }[] }
type Message = { id?: number; role: string; content: string; status?: string; run_id?: number | null; created_at?: string; question?: string | null; run?: { id: number; provider: string; model?: string | null; status: string }; tools?: { name: string; status: string; error_code?: string | null; duration_ms?: number | null }[]; sources?: { url: string; title?: string | null; published_date?: string | null; retrieved_at?: string | null; evidence_level: string; fetch_status: string; fetch_error_code?: string | null }[]; knowledge_sources?: KnowledgeSource[]; knowledge_status?: string; source_note?: string | null }
type Conversation = { id: string; title: string; domain: ChatDomain; is_pinned: boolean; archived: boolean; deleted: boolean; updated_at?: string; last_message_preview?: string }
type Tool = { name: string; status: string; summary?: Record<string, unknown>; error?: string }
type LinkedTask = { id: number; title: string; status: string; module: string; message_id?: number | null }
export type KnowledgeDraftIntent = { content: string; domain: string; conversationId?: string | null; messageId?: number | null; selectedText?: string | null; openRefinement?: boolean }

function saveKnowledgeDraft(onSave: ((draft: KnowledgeDraftIntent) => void) | undefined, message: Message, domain: ChatDomain, conversationId: string | null, openRefinement = false) {
  const selectedText = window.getSelection()?.toString().trim() || null
  onSave?.({ content: message.content, domain, conversationId, messageId: message.id ?? null, selectedText, openRefinement })
}

const domains: { key: ChatDomain; label: string }[] = [
  { key: 'general', label: '通用' }, { key: 'bilingual', label: '双语学习' }, { key: 'global', label: '全球信息' },
  { key: 'health', label: '健康管理' }, { key: 'stem', label: '理工学习' }, { key: 'projects', label: '市场项目' },
  { key: 'finance', label: '基础财务' }, { key: 'reviews', label: '每日复盘' },
]

async function request(path: string, options: RequestInit = {}) {
  const csrf = document.cookie.split('; ').find((item) => item.startsWith('personal_os_csrf='))?.split('=')[1]
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  const response = await fetch(path, { ...options, headers, credentials: 'include' })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = payload.detail
    const message = response.status === 401 ? '登录已过期，请重新登录。' : response.status === 403 ? '操作未获授权或安全校验已过期，请刷新页面后重试。' : typeof detail === 'string' ? detail : detail?.message || detail?.code || (response.status === 404 ? '会话不存在或无权访问。' : `请求失败 (${response.status})`)
    throw new Error(message)
  }
  return response
}

export function ChatWorkspace({ initialDomain = 'general', initialConversationId = null, initialMessageId = null, refreshSignal = 0, onProposalChange, onOpenTask, onOpenTasks, onOpenKnowledge, onSaveKnowledge, onBack }: { initialDomain?: ChatDomain; initialConversationId?: string | null; initialMessageId?: number | null; refreshSignal?: number; onProposalChange?: () => void; onOpenTask?: (id: number) => void; onOpenTasks?: () => void; onOpenKnowledge?: (id: number, domain: string) => void; onSaveKnowledge?: (draft: KnowledgeDraftIntent) => void; onBack: () => void }) {
  const [domain, setDomain] = useState<ChatDomain>(initialDomain)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(initialConversationId)
  const [messages, setMessages] = useState<Message[]>([])
  const [enabled, setEnabled] = useState(true)
  const [search, setSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [showTrash, setShowTrash] = useState(false)
  const [listOpen, setListOpen] = useState(() => window.innerWidth > 850 && localStorage.getItem('personal-os-chat-sidebar') !== 'collapsed')
  const [hasMore, setHasMore] = useState(false)
  const [beforeId, setBeforeId] = useState<number | null>(null)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [tools, setTools] = useState<Tool[]>([])
  const [busy, setBusy] = useState(false)
  const [personal, setPersonal] = useState(false)
  const [web, setWeb] = useState(false)
  const [knowledgeEnabled, setKnowledgeEnabled] = useState(false)
  const [knowledgeScope, setKnowledgeScope] = useState<ChatDomain | 'all'>(initialDomain)
  const [linkedTasks, setLinkedTasks] = useState<LinkedTask[]>([])
  const [taskOptions, setTaskOptions] = useState<LinkedTask[]>([])
  const [taskLinkId, setTaskLinkId] = useState('')
  const [taskDraftTitle, setTaskDraftTitle] = useState('')
  const [taskDraftCriteria, setTaskDraftCriteria] = useState('')
  const [taskProposal, setTaskProposal] = useState<any>(null)
  const [taskPanelOpen, setTaskPanelOpen] = useState(false)
  const [copyFeedback, setCopyFeedback] = useState<Record<string, string>>({})
  const inFlight = useRef(false)
  const messageScroll = useRef<HTMLDivElement>(null)
  const prependPosition = useRef<{ height: number; top: number } | null>(null)
  const active = conversations.find((item) => item.id === activeId)

  useEffect(() => { localStorage.setItem('personal-os-chat-sidebar', listOpen ? 'expanded' : 'collapsed') }, [listOpen])
  useLayoutEffect(() => {
    const previous = prependPosition.current
    if (previous && messageScroll.current) {
      messageScroll.current.scrollTop = previous.top + messageScroll.current.scrollHeight - previous.height
      prependPosition.current = null
    }
  }, [messages])

  async function openSession(id: string) {
    const detail = await (await request(`/api/agent/chat/conversations/${id}`)).json()
    if (initialMessageId && !detail.messages?.some((item: Message) => item.id === initialMessageId)) {
      let cursor = detail.next_before_id as number | null
      let more = Boolean(detail.has_more)
      for (let pageNumber = 0; more && cursor && pageNumber < 50; pageNumber += 1) {
        const older = await (await request(`/api/agent/chat/conversations/${id}?before_id=${cursor}`)).json()
        detail.messages = [...(older.messages || []), ...(detail.messages || [])]
        cursor = older.next_before_id ?? null
        more = Boolean(older.has_more)
        if (older.messages?.some((item: Message) => item.id === initialMessageId)) break
      }
      detail.next_before_id = cursor
      detail.has_more = more
    }
    setActiveId(id)
    setMessages(detail.messages || [])
    setHasMore(Boolean(detail.has_more))
    setBeforeId(detail.next_before_id ?? null)
    setLinkedTasks(detail.linked_tasks || [])
    setError('')
    setTools([])
    if (initialMessageId) window.setTimeout(() => document.querySelector(`[data-message-id="${initialMessageId}"]`)?.scrollIntoView({ block: 'center' }), 0)
    else window.setTimeout(() => { if (messageScroll.current) messageScroll.current.scrollTop = messageScroll.current.scrollHeight }, 0)
  }

  async function loadOlder() {
    if (!activeId || !hasMore || beforeId == null || loadingOlder) return
    setLoadingOlder(true)
    try {
      const view = messageScroll.current
      if (view) prependPosition.current = { height: view.scrollHeight, top: view.scrollTop }
      const page = await (await request(`/api/agent/chat/conversations/${activeId}?before_id=${beforeId}`)).json()
      setMessages((current) => {
        const known = new Set(current.map((item) => item.id))
        return [...(page.messages || []).filter((item: Message) => !known.has(item.id)), ...current]
      })
      setHasMore(Boolean(page.has_more)); setBeforeId(page.next_before_id ?? null)
    } catch (cause) { prependPosition.current = null; setError(cause instanceof Error ? cause.message : '较早消息读取失败') }
    finally { setLoadingOlder(false) }
  }

  useEffect(() => {
    let alive = true
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ domain, include_archived: String(showArchived && !showTrash), include_deleted: String(showTrash), limit: '100' })
      if (search.trim()) params.set('q', search.trim())
      void request(`/api/agent/chat/conversations?${params}`).then((response) => response.json()).then(async (data) => {
        if (!alive) return
        setEnabled(Boolean(data.enabled))
        const items: Conversation[] = data.conversations || []
        setConversations(items)
        if (!data.enabled) return
        const selected = items.find((item) => item.id === (activeId || initialConversationId))
        if (selected && !showTrash) await openSession(selected.id)
        else if (!activeId && !showTrash && items[0]) await openSession(items[0].id)
        else if (showTrash && selected) setActiveId(selected.id)
      }).catch((cause) => { if (alive) setError(cause instanceof Error ? cause.message : '会话列表读取失败') })
    }, 160)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [domain, search, showArchived, showTrash, refreshSignal])

  useEffect(() => { void request('/api/tasks').then((response) => response.json()).then(setTaskOptions).catch(() => setTaskOptions([])) }, [refreshSignal])

  async function refresh() {
    try {
      const params = new URLSearchParams({ domain, include_archived: String(showArchived && !showTrash), include_deleted: String(showTrash), limit: '100' })
      if (search.trim()) params.set('q', search.trim())
      const data = await (await request(`/api/agent/chat/conversations?${params}`)).json()
      setEnabled(Boolean(data.enabled)); setConversations(data.conversations || [])
      if (activeId && !showTrash) await openSession(activeId)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '刷新会话失败') }
  }

  async function newSession() {
    setError(''); setTools([]); setMessages([]); setLinkedTasks([]); setTaskProposal(null)
    if (!enabled) { setActiveId(crypto.randomUUID()); return }
    try {
      const created = await (await request('/api/agent/chat/conversations', { method: 'POST', body: JSON.stringify({ domain }) })).json()
      setActiveId(created.id); setConversations((items) => [created, ...items.filter((item) => item.id !== created.id)])
    } catch (cause) { setError(cause instanceof Error ? cause.message : '新建会话失败') }
  }

  async function refreshLinkedTasks() {
    if (!activeId || !enabled) return
    try { const data = await (await request(`/api/agent/chat/conversations/${activeId}/tasks`)).json(); setLinkedTasks(data) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '关联任务读取失败') }
  }
  async function copyMessage(message: Message, messageKey: string) {
    if (!message.content.trim()) {
      setCopyFeedback((current) => ({ ...current, [messageKey]: '消息内容为空，无法复制。' }))
      return
    }
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard API unavailable')
      await navigator.clipboard.writeText(message.content)
      setCopyFeedback((current) => ({ ...current, [messageKey]: '复制成功。' }))
    } catch {
      setCopyFeedback((current) => ({ ...current, [messageKey]: '复制失败，请检查浏览器剪贴板权限。' }))
    }
  }
  async function linkExistingTask() {
    if (!activeId || !taskLinkId) return
    try { await request(`/api/agent/chat/conversations/${activeId}/tasks/${taskLinkId}`, { method: 'POST', body: JSON.stringify({}) }); setTaskLinkId(''); await refreshLinkedTasks() }
    catch (cause) { setError(cause instanceof Error ? cause.message : '关联任务失败') }
  }
  async function createManualLinkedTask(event: React.FormEvent) {
    event.preventDefault(); if (!activeId || !taskDraftTitle.trim() || !taskDraftCriteria.trim()) return
    try {
      const task = await (await request('/api/tasks', { method: 'POST', body: JSON.stringify({ title: taskDraftTitle.trim(), description: '', module: domain, priority: 2, acceptance_criteria: taskDraftCriteria.trim() }) })).json()
      await request(`/api/tasks/${task.id}/links`, { method: 'POST', body: JSON.stringify({ conversation_id: activeId }) })
      setTaskDraftTitle(''); setTaskDraftCriteria(''); await refreshLinkedTasks(); onProposalChange?.()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '创建并关联任务失败') }
  }
  async function createAiTaskProposal(message: Message) {
    if (!message.id || !activeId) return
    const title = window.prompt('草案任务标题', message.content.replace(/\s+/g, ' ').slice(0, 100))
    if (!title?.trim()) return
    const description = window.prompt('具体行动（不会自动写入正式任务）', message.content.slice(0, 500))
    if (!description?.trim()) return
    const acceptance = window.prompt('验收条件', '记录可复核的实际执行结果。')
    if (!acceptance?.trim()) return
    const duration = Number(window.prompt('预计分钟', '30'))
    if (!Number.isInteger(duration) || duration < 1 || duration > 1440) { setError('预计分钟需为 1–1440 的整数。'); return }
    try {
      const proposal = await (await request(`/api/agent/chat/messages/${message.id}/task-proposal`, { method: 'POST', body: JSON.stringify({ title: title.trim(), description: description.trim(), module: domain, estimated_minutes: duration, acceptance_criteria: acceptance.trim() }) })).json()
      if (!proposal.id || proposal.status !== 'PENDING') throw new Error('服务端未返回真实待审批提案记录。')
      setTaskProposal(proposal); onProposalChange?.()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '任务提案未创建') }
  }
  async function createKnowledgeTaskProposal(message: Message) {
    const cardIds = [...new Set((message.knowledge_sources || []).map((source) => source.id))]
    if (!cardIds.length || !enabled) return
    setBusy(true); setError('')
    try {
      const proposal = await (await request('/api/task-manager/chat', { method: 'POST', body: JSON.stringify({
        message: '根据刚才检索到的个人知识制定具体的学习或项目任务草案。请把日期留空，除非本次请求明确给出日期。草案必须注明知识卡片来源，不要创建正式任务。',
        operation_id: crypto.randomUUID(), mode: 'auto', knowledge_card_ids: cardIds, knowledge_consent_granted: true,
      }) })).json()
      if (proposal.status !== 'awaiting_user_confirmation' || !proposal.candidate_tasks?.length || !proposal.operation_id) throw new Error(proposal.error_code || '没有生成可编辑的任务草案；未创建正式任务。')
      setTaskProposal({ ...proposal, kind: 'knowledge', candidate_tasks: proposal.candidate_tasks.map((item: any) => ({ ...item, start_date: '', due_date: '' })), confirm_operation_id: crypto.randomUUID(), knowledge_sources: message.knowledge_sources })
      setTaskPanelOpen(true)
      onProposalChange?.()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '知识任务草案生成失败') }
    finally { setBusy(false) }
  }
  function editKnowledgeCandidate(index: number, field: string, value: string | number | null) {
    setTaskProposal((current: any) => current ? ({ ...current, candidate_tasks: current.candidate_tasks.map((item: any, itemIndex: number) => itemIndex === index ? { ...item, [field]: value } : item) }) : current)
  }
  async function confirmKnowledgeTaskProposal() {
    if (!taskProposal || taskProposal.status !== 'awaiting_user_confirmation') return
    setBusy(true); setError('')
    try {
      const body = { message: '确认', operation_id: taskProposal.confirm_operation_id, conversation_id: taskProposal.conversation_id, mode: 'auto', candidate_tasks: taskProposal.candidate_tasks.map((item: any) => ({ ...item, start_date: item.start_date || null, due_date: item.due_date || null })) }
      const result = await (await request('/api/task-manager/chat', { method: 'POST', body: JSON.stringify(body) })).json()
      if (result.status !== 'completed') throw new Error(result.error_code || '任务确认未完成。')
      setTaskProposal({ ...taskProposal, ...result, status: 'completed' }); onProposalChange?.()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '任务确认失败；可安全重试。') }
    finally { setBusy(false) }
  }
  async function mutate(id: string, patch: Record<string, unknown>) {
    try {
      const updated = await (await request(`/api/agent/chat/conversations/${id}`, { method: 'PATCH', body: JSON.stringify(patch) })).json()
      setConversations((items) => items.map((item) => item.id === id ? { ...item, ...updated } : item))
    } catch (cause) { setError(cause instanceof Error ? cause.message : '更新会话失败') }
  }

  async function trash(id: string) {
    if (!window.confirm('将此会话移入“最近删除”？聊天内容会保留以供恢复；关联任务、知识记录、Agent 审计和备份不会被删除。')) return
    try {
      await request(`/api/agent/chat/conversations/${id}`, { method: 'DELETE' })
      setConversations((items) => items.filter((item) => item.id !== id))
      if (activeId === id) { setActiveId(null); setMessages([]) }
    } catch (cause) { setError(cause instanceof Error ? cause.message : '删除会话失败') }
  }

  async function restore(id: string) {
    try {
      await request(`/api/agent/chat/conversations/${id}/restore`, { method: 'POST', body: '{}' })
      setConversations((items) => items.filter((item) => item.id !== id))
      setShowTrash(false); setActiveId(null); setMessages([])
    } catch (cause) { setError(cause instanceof Error ? cause.message : '恢复会话失败') }
  }

  async function permanentlyDelete(id: string) {
    const conversation = conversations.find((item) => item.id === id)
    if (!conversation?.deleted) return
    if (!window.confirm(`永久清除“${conversation.title}”的会话正文和消息？关联任务、其他会话、提案审计与历史备份不会删除。`)) return
    const typed = window.prompt(`输入会话标题以确认永久清除：${conversation.title}`)
    if (typed !== conversation.title) return
    try {
      await request(`/api/agent/chat/conversations/${id}/permanent-delete`, { method: 'POST', body: JSON.stringify({ confirm_title: typed }) })
      setConversations((items) => items.filter((item) => item.id !== id))
      if (activeId === id) { setActiveId(null); setMessages([]) }
      setError('会话正文已从当前数据库永久清除；独立备份可能仍保留删除前内容。')
    } catch (cause) { setError(cause instanceof Error ? cause.message : '永久清除会话失败') }
  }

  async function exportSession(format: 'markdown' | 'json') {
    if (!activeId) return
    try {
      const response = await request(`/api/agent/chat/conversations/${activeId}/export?format=${format}`)
      const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement('a')
      anchor.href = url; anchor.download = `conversation-${activeId}.${format === 'json' ? 'json' : 'md'}`; anchor.click(); URL.revokeObjectURL(url)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '导出失败') }
  }

  async function send(text = draft) {
    if (inFlight.current || !text.trim() || (active && (active.deleted || active.archived))) return
    inFlight.current = true; setBusy(true); setError(''); setTools([])
    const content = text.trim(); let conversationId = activeId
    try {
      if (enabled && !conversationId) {
        const created = await (await request('/api/agent/chat/conversations', { method: 'POST', body: JSON.stringify({ domain }) })).json()
        conversationId = created.id; setActiveId(created.id); setConversations((items) => [created, ...items])
      }
      if (!enabled && !conversationId) { conversationId = crypto.randomUUID(); setActiveId(conversationId) }
      const result = await (await request('/api/agent/chat', { method: 'POST', body: JSON.stringify({ message: content, conversation_id: enabled ? conversationId : null, domain, history: messages.slice(-12), consent_granted: personal || web || knowledgeEnabled, include_personal_context: personal, enable_web_tools: web, use_knowledge_base: knowledgeEnabled, knowledge_domains: knowledgeScope === 'all' ? [] : [knowledgeScope] }) })).json()
      setTools(result.tools || [])
      if (enabled && conversationId) await openSession(conversationId)
      else setMessages((items) => [...items, { role: 'user', content, status: 'completed' }, { role: 'assistant', content: result.content, status: 'completed', run_id: result.run_id }])
      setDraft('')
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Agent 请求失败')
      if (enabled && conversationId) { try { await openSession(conversationId) } catch { /* preserve the request error */ } }
      else setMessages((items) => [...items, { role: 'user', content, status: 'failed' }])
    } finally { inFlight.current = false; setBusy(false) }
  }

  const selectedDomain = domains.find((item) => item.key === domain)?.label || '通用'
  return <div className="content chat-workspace-wrap"><div className={`chat-workspace ${listOpen ? '' : 'sidebar-collapsed'}`}>
    <div className="chat-workspace-mobile-head"><button className="secondary" onClick={() => setListOpen((value) => !value)}>{listOpen ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}{listOpen ? '收起会话' : '会话列表'}</button><button className="text-btn" onClick={onBack}>返回总览</button></div>
    <section className={`chat-workspace-sidebar ${listOpen ? 'is-open' : 'is-collapsed'}`}>
      <div className="chat-workspace-heading"><div><p className="eyebrow">PERSONAL OS / CHAT</p><h3>对话工作区</h3></div><button className="chat-action chat-desktop-collapse" onClick={() => setListOpen(false)} aria-label="折叠会话侧栏"><ChevronLeft size={16} /> 收起</button><button className="icon-btn chat-back" onClick={onBack} aria-label="返回总览"><X size={17} /></button></div>
      <div className="chat-domain-list" aria-label="聊天领域">{domains.map((item) => <button key={item.key} className={domain === item.key ? 'chat-domain active' : 'chat-domain'} onClick={() => { setDomain(item.key); setActiveId(null); setMessages([]); setShowTrash(false) }}>{item.label}</button>)}</div>
      <button className="primary chat-new" onClick={newSession} disabled={!enabled && busy}><Plus size={15} /> 新建对话</button>
      <label className="chat-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索标题和消息内容" /></label>
      <div className="chat-list-controls"><button className={showArchived ? 'chat-filter active' : 'chat-filter'} onClick={() => { setShowArchived((value) => !value); setShowTrash(false) }}><Archive size={14} />{showArchived ? '普通会话' : '已归档'}</button><button className={showTrash ? 'chat-filter active' : 'chat-filter'} onClick={() => { setShowTrash((value) => !value); setShowArchived(false); setActiveId(null); setMessages([]) }}><Trash2 size={14} />最近删除</button></div>
      <div className="chat-conversation-list">{conversations.map((conversation) => <article key={conversation.id} className={conversation.id === activeId ? 'chat-conversation active' : 'chat-conversation'}><button className="chat-conversation-open" onClick={() => showTrash ? setActiveId(conversation.id) : void openSession(conversation.id)}><strong>{conversation.is_pinned && <Pin size={12} />} {conversation.title || '新会话'}</strong><span>{conversation.last_message_preview || '尚无消息'}</span></button>{showTrash ? <div className="chat-trash-actions"><button className="chat-action" title="恢复会话" aria-label={`恢复 ${conversation.title}`} onClick={() => void restore(conversation.id)}><RotateCcw size={14} />恢复</button><button className="chat-action danger" title="永久清除会话" aria-label={`永久清除 ${conversation.title}`} onClick={() => void permanentlyDelete(conversation.id)}><Trash2 size={14} />清除</button></div> : null}</article>)}{conversations.length === 0 && <p className="empty chat-empty">{showTrash ? '最近删除为空' : '此领域暂无会话'}</p>}</div>
      {!enabled && <p className="chat-privacy-hint">聊天保存已关闭：当前对话仅留在此页面内存中，刷新或换设备后不可恢复。</p>}
    </section>
    <section className="chat-workspace-main">
      <div className="chat-main-heading"><div className="chat-heading-title">{!listOpen && <button className="chat-action chat-desktop-expand" onClick={() => setListOpen(true)} aria-label="展开会话侧栏"><ChevronRight size={16} /> 会话</button>}<div><p className="eyebrow">{selectedDomain}</p><h2>{active?.title || '新对话'}</h2></div></div>{active && !active.deleted && <div className="chat-actions"><button className="chat-action" title="重命名" aria-label="重命名会话" onClick={() => { const title = window.prompt('输入新的会话标题', active.title); if (title?.trim()) void mutate(active.id, { title: title.trim() }) }}>改名</button><button className="chat-action" title={active.is_pinned ? '取消置顶' : '置顶'} aria-label={active.is_pinned ? '取消置顶' : '置顶会话'} onClick={() => void mutate(active.id, { is_pinned: !active.is_pinned })}><Pin size={15} /></button><button className="chat-action" title={active.archived ? '恢复会话至活跃列表' : '归档会话（仅隐藏）'} aria-label={active.archived ? '恢复归档' : '归档会话'} onClick={() => void mutate(active.id, { archived: !active.archived })}><Archive size={15} /></button><button className="chat-action" title="导出 Markdown" aria-label="导出 Markdown" onClick={() => void exportSession('markdown')}><ArrowDownToLine size={15} /> MD</button><button className="chat-action" title="导出 JSON" aria-label="导出 JSON" onClick={() => void exportSession('json')}><ArrowDownToLine size={15} /> JSON</button><button className="chat-action danger" title="移入最近删除" aria-label="删除会话" onClick={() => void trash(active.id)}><Trash2 size={15} /></button></div>}</div>
      <details className="chat-export-scope"><summary>会话与导出说明</summary><p>归档会话仅从活跃列表隐藏，不等于知识收录；结构化知识卡片将在 v0.4 建设。最近删除可恢复；关联任务、Agent 审计及备份不受影响。导出包含完整消息、Run ID 与来源，不包含其他业务记录。</p></details>
      {enabled && activeId && <details className="chat-linked-tasks" open={taskPanelOpen} onToggle={(event) => setTaskPanelOpen((event.currentTarget as HTMLDetailsElement).open)}><summary>关联任务 · {linkedTasks.length}</summary><div className="chat-linked-task-list">{linkedTasks.map((task) => <button className="chat-linked-task-link" key={task.id} onClick={() => onOpenTask?.(task.id)}><strong>#{task.id} {task.title}</strong><span>{task.module} · {task.status}{task.message_id ? ` · 消息 #${task.message_id}` : ''}</span></button>)}{linkedTasks.length === 0 && <small>尚无关联任务。勾选个人数据授权后，Agent 可调用当前会话任务工具核对。</small>}</div><div className="chat-link-task-controls"><select aria-label="关联已有任务" value={taskLinkId} onChange={(event) => setTaskLinkId(event.target.value)}><option value="">选择已有任务…</option>{taskOptions.filter((task) => !linkedTasks.some((linked) => linked.id === task.id)).map((task) => <option key={task.id} value={task.id}>{task.title} · {task.status}</option>)}</select><button className="secondary" disabled={!taskLinkId} onClick={() => void linkExistingTask()}>关联</button></div><form className="chat-manual-task" onSubmit={createManualLinkedTask}><input aria-label="手动任务名称" value={taskDraftTitle} onChange={(event) => setTaskDraftTitle(event.target.value)} placeholder="直接创建任务（用户主动授权）" /><input aria-label="手动任务验收标准" value={taskDraftCriteria} onChange={(event) => setTaskDraftCriteria(event.target.value)} placeholder="验收条件" /><button className="secondary"><Plus size={14} />创建并关联</button></form>{taskProposal?.kind === 'knowledge' ? <section className="chat-task-proposal" aria-label="知识辅助任务草案"><strong>知识辅助任务草案 · {taskProposal.status === 'awaiting_user_confirmation' ? '尚未创建' : '已确认'}</strong><small>操作 #{taskProposal.operation_id} · 关联知识：{(taskProposal.knowledge_sources || []).map((item: KnowledgeSource) => `#${item.id} ${item.title}`).join('、')}</small>{taskProposal.status === 'awaiting_user_confirmation' ? <><p>请检查任务目标、说明和日期。未填写日期会保持未排期。</p>{taskProposal.candidate_tasks.map((item: any, index: number) => <fieldset className="knowledge-task-candidate" key={index}><legend>任务 {index + 1}</legend><label>目标<input value={item.title} onChange={(event) => editKnowledgeCandidate(index, 'title', event.target.value)} /></label><label>说明<textarea value={item.description || ''} onChange={(event) => editKnowledgeCandidate(index, 'description', event.target.value)} /></label><label>领域<select value={item.module || 'general'} onChange={(event) => editKnowledgeCandidate(index, 'module', event.target.value)}>{domains.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}</select></label><label>预计分钟<input type="number" min="1" max="1440" value={item.estimated_minutes ?? ''} onChange={(event) => editKnowledgeCandidate(index, 'estimated_minutes', event.target.value === '' ? null : Number(event.target.value))} /></label><label>开始日期<input type="date" value={item.start_date || ''} onChange={(event) => editKnowledgeCandidate(index, 'start_date', event.target.value)} /></label><label>结束日期<input type="date" value={item.due_date || ''} onChange={(event) => editKnowledgeCandidate(index, 'due_date', event.target.value)} /></label><label>验收条件<textarea value={item.acceptance_criteria || ''} onChange={(event) => editKnowledgeCandidate(index, 'acceptance_criteria', event.target.value)} /></label></fieldset>)}<button className="primary" disabled={busy} onClick={() => void confirmKnowledgeTaskProposal()}>{busy ? '正在确认…' : '确认并创建真实任务'}</button></> : <><p>已提交到任务服务。</p>{taskProposal.created_tasks?.map((task: any) => <button className="chat-linked-task-link" key={task.id} onClick={() => onOpenTask?.(task.id)}>打开任务 #{task.id} · {task.title}</button>)}</>}</section> : taskProposal && <div className="chat-task-proposal"><strong>{taskProposal.title} · {taskProposal.status}</strong><small>草案 ID #{taskProposal.id} · 来源消息 # {taskProposal.items?.[0]?.source_message_id || 'UNKNOWN'}</small>{taskProposal.status === 'PENDING' && <button className="secondary" onClick={onOpenTasks}>前往任务工作区审批</button>}</div>}</details>}
      <div className="chat-message-scroll" ref={messageScroll}>{hasMore && <button className="chat-action chat-load-older" disabled={loadingOlder} onClick={() => void loadOlder()}>{loadingOlder ? '正在读取…' : '加载更早消息'}</button>}{messages.map((message, index) => { const messageKey = String(message.id ?? `${message.role}-${index}`); return <article data-message-id={message.id} key={messageKey} className={`chat-message ${message.role === 'user' ? 'from-user' : 'from-agent'} ${message.status === 'failed' ? 'is-failed' : ''}`}><header>{message.role === 'user' ? '你' : 'Personal Agent'}<time>{message.created_at ? new Date(message.created_at).toLocaleString() : ''}</time></header><div className="chat-message-content">{message.role === 'assistant' ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> }}>{message.content}</ReactMarkdown> : message.content}</div>{message.role === 'assistant' && message.knowledge_status && <section className="chat-knowledge-citations" aria-label="知识库引用"><strong>我的知识库 · {message.knowledge_status === 'empty' ? '无检索结果' : message.knowledge_status === 'completed' ? '已检索' : '未启用'}</strong>{message.knowledge_status === 'empty' && <p>未找到符合范围的知识卡片。</p>}{message.knowledge_sources?.map((source) => <button className="chat-knowledge-source" key={source.id} onClick={() => onOpenKnowledge?.(source.id, source.domain)}><strong>知识卡片 #{source.id} · {source.title}</strong>{source.summary && <span>{source.summary}</span>}</button>)}{message.knowledge_sources?.length ? <small>知识卡片是参考数据；AI 的归纳和推断仍需核对。</small> : null}</section>}{message.status === 'failed' && <footer>发送失败，尚无模型回复。<button className="text-btn" disabled={busy} onClick={() => void send(message.content)}>安全重试</button></footer>}<div className="chat-message-actions"><button className="chat-action" type="button" aria-label={`复制${message.role === 'user' ? '用户' : 'AI'}消息`} onClick={() => void copyMessage(message, messageKey)}><Copy size={15} />复制</button>{copyFeedback[messageKey] && <small role="status" aria-live="polite">{copyFeedback[messageKey]}</small>}{message.role === 'assistant' && <><button className="knowledge-save-action" onMouseDown={(event) => event.preventDefault()} onClick={() => saveKnowledgeDraft(onSaveKnowledge, message, domain, activeId)}><BookOpen size={16} />保存为知识</button><button className="knowledge-refine-action" onMouseDown={(event) => event.preventDefault()} onClick={() => saveKnowledgeDraft(onSaveKnowledge, message, domain, activeId, true)}><Sparkles size={16} />AI 提炼</button>{message.run_id && <><small>Agent Run #{message.run_id}</small>{message.id && <button className="text-btn chat-propose-action" onClick={() => void createAiTaskProposal(message)}>从此回复创建待审批任务</button>}</>}</>}</div>{message.role === 'assistant' && message.knowledge_sources?.length ? <button className="text-btn chat-propose-action" disabled={busy || !enabled} onClick={() => void createKnowledgeTaskProposal(message)}>根据这些知识制定任务</button> : null}{message.role === 'assistant' && <details className="chat-provenance"><summary>来源与执行信息</summary>{message.question && <p><strong>原始提问：</strong>{message.question}</p>}{message.run ? <p><strong>Agent Run #{message.run.id}：</strong>{message.run.provider}/{message.run.model || 'UNKNOWN'} · {message.run.status}</p> : <p>没有关联可核验的 Agent Run。</p>}{message.tools?.length ? <div><strong>实际工具执行</strong>{message.tools.map((tool, toolIndex) => <small key={`${tool.name}-${toolIndex}`}>{tool.name} · {tool.status}{tool.error_code ? ` · ${tool.error_code}` : ''}{tool.duration_ms != null ? ` · ${tool.duration_ms} ms` : ''}</small>)}</div> : null}{message.sources?.length ? <div><strong>关联来源</strong>{message.sources.map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}<small>{source.evidence_level === 'fetched_body' ? '已抓取正文' : source.evidence_level === 'search_summary_only_fetch_failed' ? `仅搜索摘要 · 抓取失败${source.fetch_error_code ? ` (${source.fetch_error_code})` : ''}` : '仅搜索摘要，未确认读取正文'} · {source.retrieved_at || '取得时间 UNKNOWN'}</small></a>)}</div> : <p>{message.source_note || '本次回答没有关联外部核验证据。'}</p>}</details>}</article> })}{messages.length === 0 && <div className="empty chat-start">从一个具体问题开始。每轮个人数据或联网读取都需要单独授权。</div>}</div>
      {error && <div className="notice error-notice chat-error"><strong>未完成：</strong>{error}</div>}
      {tools.length > 0 && <div className="tool-log"><strong>本轮工具记录</strong>{tools.map((tool, index) => <div key={`${tool.name}-${index}`}>{tool.name}：{agentToolStatusLabel(tool.name, tool.status, tool.summary)}{Array.isArray(tool.summary?.sources) ? (tool.summary.sources as { title: string; url: string }[]).map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer">{source.title}</a>) : null}</div>)}</div>}
      <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); void send() }}><textarea value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={`在「${selectedDomain}」继续对话…`} rows={3} disabled={Boolean(active?.deleted || active?.archived)} /><div className="consent-row"><label className="consent"><input type="checkbox" checked={personal} onChange={(event) => setPersonal(event.target.checked)} />授权读取目标、任务、复盘和学习错误</label><label className="consent"><input type="checkbox" checked={web} onChange={(event) => setWeb(event.target.checked)} />授权联网搜索和读取公开网页</label><label className="consent"><input type="checkbox" checked={knowledgeEnabled} onChange={(event) => setKnowledgeEnabled(event.target.checked)} />使用我的知识库</label>{knowledgeEnabled && <label className="consent">知识范围<select aria-label="知识范围" value={knowledgeScope} onChange={(event) => setKnowledgeScope(event.target.value as ChatDomain | 'all')}><option value="all">全部分类</option>{domains.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}</select></label>}{knowledgeEnabled && <small>本轮只检索所选范围；卡片内容会作为不可信参考数据发送至已配置 Provider。</small>}</div><button className="primary" disabled={busy || !draft.trim() || Boolean(active?.deleted || active?.archived)}>{busy ? '正在执行…' : '发送'} <Send size={15} /></button>{active?.archived && <small>该会话已归档；先恢复归档再继续发送。</small>}</form>
    </section>
  </div></div>
}
