import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Archive, Check, Plus, RotateCcw, Search, Trash2 } from 'lucide-react'
import './task-manager.css'

type Task = { id: number; title: string; description?: string | null; module: string; status: string; priority: number; estimated_minutes?: number | null; expected_duration_days?: number | null; start_date?: string | null; due_date?: string | null; acceptance_criteria?: string | null; is_pinned: boolean; archived: boolean; deleted: boolean; evidence_count: number }
type TaskDetail = Task & { evidence: Array<{ id: number; content: string; verification_level: string; source?: string | null; created_at: string }>; history: Array<{ id: number; from_status?: string | null; to_status: string; reason?: string | null; created_at: string }>; date_history: Array<{ id: number; old_start_date?: string | null; new_start_date?: string | null; old_due_date?: string | null; new_due_date?: string | null; reason: string; changed_at: string }>; links: Array<{ id: number; conversation_id: string; conversation_title: string; domain: string; message_id?: number | null; message_preview?: string | null }> }
const statuses = ['TODO', 'DOING', 'BLOCKED', 'VERIFY', 'DONE', 'CANCELLED']
const domains = ['general', 'bilingual', 'global', 'health', 'stem', 'projects', 'finance', 'reviews', 'ai_engineering', 'system']
function taskOperationFailure(code: string) {
  const messages: Record<string, string> = {
    OPERATION_TIMED_OUT_NO_WRITE: '操作超时且数据库中未发现任务写入。原输入已保留；确认状态后可重新提交。',
    MODEL_TIMEOUT: '模型请求超时；没有确认到任务写入。请稍后重试。',
    MODEL_REQUEST_FAILED: '模型服务请求失败；没有确认到任务写入。请检查 Provider 状态后重试。',
    REQUIRED_TASK_TOOL_MISSING: '模型未完成必要的任务操作；没有写入任务。可以调整指令后重试。',
    TASK_TOOL_FAILED: '任务读取或操作未通过校验；没有确认写入。请检查目标或日期后重试。',
    TASK_PARSE_FAILED: '模型未能整理出有效任务字段；没有写入。请补充任务名称或重试。',
    TASK_DOMAIN_INVALID: '任务领域不在允许的选项中；没有写入。请重试，系统也会根据任务内容推断领域。',
    TASK_DATE_RANGE_INVALID: '开始日期、截止日期或持续天数不一致；没有写入。请修正日期后重试。',
    TASK_VALIDATION_FAILED: '任务未通过业务校验；没有写入。请检查任务内容后重试。',
    COMMIT_FAILED: '数据库事务未能提交；系统已回滚，任务未确认创建。请先检查操作状态，再决定是否重试。',
    TASK_COUNT_MISMATCH: '模型拆分出的任务数量与原指令不一致；已拒绝整批写入。请简化指令后重试。',
    INVALID_TOOL_ARGUMENTS: '模型生成的任务字段未通过校验；没有写入。请补充任务内容后重试。',
  }
  return messages[code] || (code ? `操作未完成（${code}）；请检查状态后再重试。` : '操作未完成；请检查状态后再重试。')
}
function taskOperationSuccess(result: any) {
  const createdIds = new Set((result.actions || []).filter((action: any) => action.kind === 'create').map((action: any) => action.task_id))
  const created = (result.tasks || []).filter((task: Task) => createdIds.has(task.id))
  if (created.length) return `已创建 ${created.length} 项任务：${created.map((task: Task) => `#${task.id} ${task.title}`).join('；')}`
  const updatedIds = new Set((result.actions || []).filter((action: any) => action.kind === 'update').map((action: any) => action.task_id))
  const updated = (result.tasks || []).filter((task: Task) => updatedIds.has(task.id))
  if (updated.length) return `已修改 ${updated.length} 项任务：${updated.map((task: Task) => `#${task.id} ${task.title}`).join('；')}`
  if ((result.actions || []).some((action: any) => action.kind === 'duplicate')) return '现有任务已覆盖本次内容；没有创建重复任务。'
  return result.status === 'completed' ? '操作已完成；任务列表已核对数据库状态。' : '操作已完成。'
}

async function api(path: string, options: RequestInit = {}) {
  const csrf = document.cookie.split('; ').find((item) => item.startsWith('personal_os_csrf='))?.split('=')[1]
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  const response = await fetch(path, { ...options, headers, credentials: 'include' })
  const payload = response.status === 204 ? null : await response.json().catch(() => null)
  if (!response.ok) {
    const code = payload?.detail?.code
    const known: Record<string, string> = {
      TASK_NOT_FOUND: '任务不存在、已彻底删除或无权访问。', TASK_PURGED: '该任务已彻底删除，不能恢复。',
      TASK_PURGE_BLOCKED: '此任务仍被每日结算等历史记录引用，不能清理。', TASK_MUST_BE_TRASHED: '请先将任务移入最近删除，再执行彻底删除。',
      CONFIRM_TITLE_MISMATCH: '确认标题不匹配；未执行删除。',
    }
    const message = response.status === 401 ? '登录已过期，请重新登录。' : response.status === 403 ? '操作未获授权或安全校验已过期，请刷新页面后重试。' : known[code] || (typeof payload?.detail === 'string' ? payload.detail : payload?.detail?.message || code || (response.status === 404 ? '请求的记录不存在或不可访问。' : `请求失败 (${response.status})`))
    throw new Error(message)
  }
  return payload
}

export function TasksWorkspace({ onOpenConversation, onOpenReview, onOpenKnowledge, initialTaskId = null }: { onOpenConversation: (domain: string, id: string, messageId?: number | null) => void; onOpenReview: () => void; onOpenKnowledge?: (id: number, domain?: string) => void; initialTaskId?: number | null }) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [linkedKnowledge, setLinkedKnowledge] = useState<Array<{ id: number; title: string; domain: string; review_status: string }>>([])
  const [query, setQuery] = useState('')
  const [module, setModule] = useState('')
  const [status, setStatus] = useState('')
  const [timeline, setTimeline] = useState('')
  const [archived, setArchived] = useState(false)
  const [trash, setTrash] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [domain, setDomain] = useState('system')
  const [priority, setPriority] = useState(2)
  const [minutes, setMinutes] = useState('')
  const [criteria, setCriteria] = useState('')
  const [today, setToday] = useState('')
  const [startDate, setStartDate] = useState('')
  const [dueDate, setDueDate] = useState('')
  const [durationDays, setDurationDays] = useState('1')
  const [proposals, setProposals] = useState<any[]>([])
  const [proposalItems, setProposalItems] = useState<Record<number, any[]>>({})
  const [evidence, setEvidence] = useState('')
  const [linkConversationId, setLinkConversationId] = useState('')
  const [linkMessage, setLinkMessage] = useState('')
  const [conversations, setConversations] = useState<any[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState(false)
  const [assistantDraft, setAssistantDraft] = useState('')
  const [assistantBusy, setAssistantBusy] = useState(false)
  const [taskMode, setTaskMode] = useState<'create' | 'plan' | 'modify'>('create')
  const [operationUncertain, setOperationUncertain] = useState(false)
  const [taskConversations, setTaskConversations] = useState<any[]>([])
  const [taskConversationId, setTaskConversationId] = useState('')
  const [assistantMessages, setAssistantMessages] = useState<any[]>([])
  const [lastOperation, setLastOperation] = useState<string | null>(null)
  const [pendingConfirmations, setPendingConfirmations] = useState<any[]>([])
  const [taskConversationTrash, setTaskConversationTrash] = useState(false)
  const [proposalTrash, setProposalTrash] = useState(false)
  const [proposalStatusFilter, setProposalStatusFilter] = useState('')
  const assistantBusyRef = useRef(false)
  const submissionOperation = useRef<{ message: string; conversationId: string | null; mode: string; id: string } | null>(null)

  async function refreshTaskConversations(includeDeleted = taskConversationTrash) {
    const value = await api(`/api/agent/chat/conversations?domain=tasks&include_deleted=${includeDeleted}&limit=100`)
    setTaskConversations(value.conversations || [])
    return value.conversations || []
  }
  async function refreshProposals(includeDeleted = proposalTrash, status = proposalStatusFilter) {
    const params = new URLSearchParams({ include_deleted: String(includeDeleted) })
    if (status) params.set('status', status)
    const value = await api(`/api/agent/proposals?${params}`)
    setProposals(value); setProposalItems(Object.fromEntries(value.map((proposal: any) => [proposal.id, proposal.items || []])))
  }

  async function load() {
    try {
      const params = new URLSearchParams({ include_archived: String(archived && !trash), include_deleted: String(trash) })
      if (query.trim()) params.set('q', query.trim())
      if (module) params.set('module', module)
      if (status && !trash && !archived) params.set('status', status)
      if (timeline && !trash && !archived) params.set('timeline', timeline)
      const result = await api(`/api/tasks?${params}`)
      setTasks(result)
      if (selectedId && result.some((item: Task) => item.id === selectedId)) await selectTask(selectedId, trash)
      else if (selectedId) { setSelectedId(null); setDetail(null) }
      setError(false)
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : '任务列表读取失败'); setError(true) }
  }
  async function selectTask(id: number, includeDeleted = trash) {
    try {
      const item = await api(`/api/tasks/${id}${includeDeleted ? '?include_deleted=true' : ''}`)
      setTrash(Boolean(item.deleted)); setSelectedId(id); setDetail(item); setTitle(item.title); setDescription(item.description || ''); setDomain(item.module); setPriority(item.priority); setMinutes(item.estimated_minutes == null ? '' : String(item.estimated_minutes)); setCriteria(item.acceptance_criteria || ''); setStartDate(item.start_date || ''); setDueDate(item.due_date || ''); setDurationDays(item.expected_duration_days == null ? '' : String(item.expected_duration_days))
    }
    catch (cause) { setMessage(cause instanceof Error ? cause.message : '任务详情读取失败'); setError(true) }
  }
  useEffect(() => { void load() }, [query, module, status, archived, trash, timeline])
  useEffect(() => { if (initialTaskId != null) void selectTask(initialTaskId) }, [initialTaskId])
  useEffect(() => { if (!selectedId) { setLinkedKnowledge([]); return } void api(`/api/knowledge?task_id=${selectedId}&limit=100`).then((value) => setLinkedKnowledge(Array.isArray(value) ? value : [])).catch(() => setLinkedKnowledge([])) }, [selectedId])
  useEffect(() => { void api('/api/agent/chat/conversations?limit=100').then((value) => setConversations(value.conversations || [])).catch(() => setConversations([])); void api('/api/agent/chat/conversations?domain=tasks&limit=100').then((value) => { const items = value.conversations || []; setTaskConversations(items); if (items[0]) { setTaskConversationId(items[0].id); void loadTaskConversation(items[0].id) } }).catch(() => {}); void api('/api/task-manager/pending-confirmations').then(setPendingConfirmations).catch(() => setPendingConfirmations([])); void api('/api/dashboard').then((value) => { setToday(value.today_date); setStartDate(value.today_date); setDueDate(value.today_date); setDurationDays('1') }).catch(() => {}); void refreshProposals(false, '').catch(() => setProposals([])) }, [])

  async function loadTaskConversation(id: string) { if (id !== taskConversationId) { submissionOperation.current = null; setOperationUncertain(false) } if (taskConversationTrash) { setTaskConversationId(id); setAssistantMessages([]); return } try { const value = await api(`/api/agent/chat/conversations/${id}`); setAssistantMessages(value.messages || []); setTaskConversationId(id) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '任务指令历史读取失败'); setError(true) } }
  async function newTaskConversation() { try { const value = await api('/api/agent/chat/conversations', { method: 'POST', body: JSON.stringify({ domain: 'tasks' }) }); submissionOperation.current = null; setOperationUncertain(false); setTaskConversationTrash(false); setTaskConversations((previous) => [value, ...previous]); setTaskConversationId(value.id); setAssistantMessages([]); setLastOperation(null) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '新建任务会话失败'); setError(true) } }
  async function renameTaskConversation() { const current = taskConversations.find((item) => item.id === taskConversationId); if (!current || current.deleted) return; const title = window.prompt('新的任务管理会话标题', current.title); if (!title?.trim()) return; try { await api(`/api/agent/chat/conversations/${current.id}`, { method: 'PATCH', body: JSON.stringify({ title: title.trim() }) }); await refreshTaskConversations(false); setMessage('任务管理会话已重命名。'); setError(false) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '重命名失败'); setError(true) } }
  async function trashTaskConversation() { const current = taskConversations.find((item) => item.id === taskConversationId); if (!current || current.deleted || !window.confirm(`将“${current.title}”移入最近删除？内容可恢复；正式任务不删除。`)) return; try { await api(`/api/agent/chat/conversations/${current.id}`, { method: 'DELETE' }); const remaining = await refreshTaskConversations(false); setTaskConversationId(remaining[0]?.id || ''); setAssistantMessages(remaining[0] ? (await api(`/api/agent/chat/conversations/${remaining[0].id}`)).messages || [] : []); setMessage('会话已移入最近删除，可恢复。'); setError(false) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '删除会话失败'); setError(true) } }
  async function restoreTaskConversation() { const current = taskConversations.find((item) => item.id === taskConversationId); if (!current?.deleted) return; try { await api(`/api/agent/chat/conversations/${current.id}/restore`, { method: 'POST', body: '{}' }); const detail = await api(`/api/agent/chat/conversations/${current.id}`); setTaskConversationTrash(false); await refreshTaskConversations(false); setTaskConversationId(current.id); setAssistantMessages(detail.messages || []); setMessage('会话及消息已恢复。'); setError(false) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '恢复会话失败'); setError(true) } }
  async function permanentlyDeleteTaskConversation() { const current = taskConversations.find((item) => item.id === taskConversationId); if (!current?.deleted) return; if (!window.confirm(`不可逆清除“${current.title}”的会话正文和消息；关联任务、任务证据及安全审计元数据保留。独立历史备份不变。继续？`)) return; const typed = window.prompt(`输入会话标题以确认永久清除：${current.title}`); if (typed !== current.title) return; try { await api(`/api/agent/chat/conversations/${current.id}/permanent-delete`, { method: 'POST', body: JSON.stringify({ confirm_title: typed }) }); const remaining = await refreshTaskConversations(true); setTaskConversationId(remaining[0]?.id || ''); setAssistantMessages([]); setMessage('会话正文已永久清除；历史备份可能仍保留删除前内容。'); setError(false) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '永久删除失败'); setError(true) } }
  async function submitTaskInstruction(event: React.FormEvent) {
    event.preventDefault(); if (!assistantDraft.trim() || assistantBusyRef.current) return
    if (operationUncertain && submissionOperation.current) { setMessage('先检查上一条操作状态；系统不会重复提交仍在处理的操作。'); setError(true); return }
    assistantBusyRef.current = true
    const submittedText = assistantDraft.trim()
    if (!submissionOperation.current || submissionOperation.current.message !== submittedText || submissionOperation.current.conversationId !== (taskConversationId || null) || submissionOperation.current.mode !== taskMode) submissionOperation.current = { message: submittedText, conversationId: taskConversationId || null, mode: taskMode, id: crypto.randomUUID() }
    setAssistantBusy(true); setError(false); setMessage('正在处理任务指令…')
    try {
      const result = await api('/api/task-manager/chat', { method: 'POST', body: JSON.stringify({ message: submittedText, conversation_id: taskConversationId || null, operation_id: submissionOperation.current.id, mode: taskMode }) })
      if (result.processing || result.status === 'started') { setOperationUncertain(true); setMessage('任务仍在处理中；不会重复提交。可检查状态。'); setError(false); return }
      if (result.status === 'failed') { submissionOperation.current = null; setOperationUncertain(false); setMessage(taskOperationFailure(result.failure_code)); setError(true); return }
      setLastOperation(result.status === 'completed' && result.actions?.some((action: any) => action.kind === 'create' || action.kind === 'update') ? result.operation_id : null)
      setMessage(result.status === 'awaiting_user_confirmation' ? '任务预览已保存，尚未写入正式任务；请检查预览后确认。' : result.status === 'awaiting_confirmation' ? '删除操作等待你二次确认；任务尚未删除。' : result.status === 'completed' ? taskOperationSuccess(result) : taskMode === 'plan' ? '建议已生成；没有写入正式任务。' : '本次没有写入任务。')
      if (result.conversation_id) { await refreshTaskConversations(false); await loadTaskConversation(result.conversation_id) }
      else setAssistantMessages((previous) => [...previous, { id: `local-user-${Date.now()}`, role: 'user', content: submittedText, created_at: new Date().toISOString() }, { id: `local-assistant-${Date.now()}`, role: 'assistant', content: result.content || '请求已处理。', created_at: new Date().toISOString() }].slice(-8))
      if (result.confirmation_required) setPendingConfirmations(await api('/api/task-manager/pending-confirmations'))
      await load()
      setAssistantDraft(''); submissionOperation.current = null; setOperationUncertain(false)
    } catch (cause) {
      const original = cause instanceof Error ? cause.message : '任务指令失败。'
      const operationId = submissionOperation.current?.id
      if (operationId) {
        try {
          const state = await api(`/api/task-manager/operations/${operationId}`)
          if (state.status === 'completed' || state.status === 'applied') {
            setLastOperation(state.operation_id); setMessage(taskOperationSuccess(state)); setError(false); await load(); setAssistantDraft(''); submissionOperation.current = null; setOperationUncertain(false); return
          }
          if (state.status === 'failed') { submissionOperation.current = null; setOperationUncertain(false); setMessage(taskOperationFailure(state.failure_code)); setError(true); return }
          if (state.status === 'started') { setOperationUncertain(true); setMessage('服务仍在处理或正在核对提交状态；尚未再次提交。请稍后检查操作状态。'); setError(false); return }
          submissionOperation.current = null; setOperationUncertain(false); setMessage('操作已结束但没有正式任务写入。原指令保留，可修改后重新提交。'); setError(true); return
        } catch { submissionOperation.current = null; setOperationUncertain(false) }
      }
      setMessage(taskOperationFailure(original)); setError(true)
    }
    finally { assistantBusyRef.current = false; setAssistantBusy(false) }
  }
  async function checkTaskOperation() {
    const operationId = submissionOperation.current?.id
    if (!operationId || assistantBusyRef.current) return
    try {
      const state = await api(`/api/task-manager/operations/${operationId}`)
      if (state.status === 'started') { setMessage('操作仍在处理中；未重复提交。'); setError(false); return }
      if (state.status === 'failed') { setMessage(taskOperationFailure(state.failure_code)); setError(true); submissionOperation.current = null; setOperationUncertain(false); return }
      if (state.status === 'completed' || state.status === 'applied') { setLastOperation(state.operation_id); setMessage(taskOperationSuccess(state)); setError(false); setAssistantDraft(''); submissionOperation.current = null; setOperationUncertain(false); await load(); return }
      setMessage('操作已结束且没有写入正式任务；可调整指令后重新提交。'); setError(false); submissionOperation.current = null; setOperationUncertain(false)
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : '状态读取失败；未重复提交。'); setError(true) }
  }
  async function undoLastTaskOperation() { if (!lastOperation) return; try { await api(`/api/task-manager/operations/${lastOperation}/undo`, { method: 'POST', body: '{}' }); setMessage('本次任务操作已撤销；创建的任务移入最近删除，修改已恢复并保留历史。'); setLastOperation(null); await load() } catch (cause) { setMessage(cause instanceof Error ? cause.message : '撤销失败'); setError(true) } }
  async function confirmPendingDelete(operation: any) { const task = operation.tasks?.[0]; if (!window.confirm(`确认将任务“${task?.title || '该任务'}”移入最近删除？执行证据和历史会保留。`)) return; try { await api(`/api/task-manager/operations/${operation.operation_id}/confirm`, { method: 'POST', body: '{}' }); setPendingConfirmations(await api('/api/task-manager/pending-confirmations')); setMessage('任务已移入最近删除；历史和关联记录保留，可在任务回收区恢复。'); await load() } catch (cause) { setMessage(cause instanceof Error ? cause.message : '确认删除失败'); setError(true) } }

  function setDuration(value: string) { setDurationDays(value); const days = Number(value); if (startDate && Number.isInteger(days) && days > 0) { const start = new Date(`${startDate}T00:00:00Z`); start.setUTCDate(start.getUTCDate() + days - 1); setDueDate(start.toISOString().slice(0, 10)) } }
  function setStart(value: string) { setStartDate(value); const days = Number(durationDays); if (value && Number.isInteger(days) && days > 0) { const start = new Date(`${value}T00:00:00Z`); start.setUTCDate(start.getUTCDate() + days - 1); setDueDate(start.toISOString().slice(0, 10)) } }
  function setDue(value: string) { setDueDate(value); if (startDate && value) { const days = (Date.parse(`${value}T00:00:00Z`) - Date.parse(`${startDate}T00:00:00Z`)) / 86400000 + 1; if (Number.isInteger(days) && days > 0) setDurationDays(String(days)) } }

  async function createTask(event: React.FormEvent) {
    event.preventDefault()
    if (!title.trim()) return
    try {
      const created = await api('/api/tasks', { method: 'POST', body: JSON.stringify({ title: title.trim(), description: description || null, module: domain, priority, estimated_minutes: minutes ? Number(minutes) : null, start_date: startDate || today, due_date: dueDate || startDate || today, expected_duration_days: Number(durationDays) || 1, acceptance_criteria: criteria || null }) })
      setMessage('任务已保存到统一任务系统。'); setError(false); setTitle(''); setDescription(''); setCriteria(''); await load(); await selectTask(created.id)
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : '任务创建失败'); setError(true) }
  }
  async function saveTask(event: React.FormEvent) {
    event.preventDefault(); if (!detail) return
    try {
      await api(`/api/tasks/${detail.id}`, { method: 'PATCH', body: JSON.stringify({ title, description: description || null, module: domain, priority, estimated_minutes: minutes ? Number(minutes) : null, ...(startDate || dueDate ? { start_date: startDate || null, due_date: dueDate || null, ...(durationDays ? { expected_duration_days: Number(durationDays) } : {}) } : {}), acceptance_criteria: criteria || null }) })
      setMessage('任务详情已保存。'); setError(false); await load()
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : '任务保存失败'); setError(true) }
  }
  async function changeTaskStatus(next: string) {
    if (!detail) return
    try { await api(`/api/tasks/${detail.id}`, { method: 'PATCH', body: JSON.stringify({ status: next }) }); setMessage(`状态已更新为 ${next}。`); setError(false); await load() }
    catch (cause) { setMessage(cause instanceof Error ? cause.message : '状态更新失败'); setError(true) }
  }
  async function addEvidence(event: React.FormEvent) {
    event.preventDefault(); if (!detail || !evidence.trim()) return
    try { await api(`/api/tasks/${detail.id}/evidence`, { method: 'POST', body: JSON.stringify({ content: evidence, evidence_type: 'user_report', verification_level: 'E1_SELF_REPORTED' }) }); setEvidence(''); setMessage('执行证据已记录；证据本身不会自动把任务标记为 DONE。'); setError(false); await load() }
    catch (cause) { setMessage(cause instanceof Error ? cause.message : '证据保存失败'); setError(true) }
  }
  async function submitLinkConversation(event: React.FormEvent) {
    event.preventDefault(); if (!detail || !linkConversationId) return
    try { await api(`/api/tasks/${detail.id}/links`, { method: 'POST', body: JSON.stringify({ conversation_id: linkConversationId, message_id: linkMessage ? Number(linkMessage) : null }) }); setLinkMessage(''); setMessage('会话关联已保存（重复关联会被合并）。'); setError(false); await selectTask(detail.id) }
    catch (cause) { setMessage(cause instanceof Error ? cause.message : '会话关联失败'); setError(true) }
  }
  async function togglePin() { if (!detail) return; await api(`/api/tasks/${detail.id}`, { method: 'PATCH', body: JSON.stringify({ is_pinned: !detail.is_pinned }) }); await load() }
  async function toggleArchive() { if (!detail) return; await api(`/api/tasks/${detail.id}`, { method: 'PATCH', body: JSON.stringify({ archived: !detail.archived }) }); setSelectedId(null); setDetail(null); await load() }
  async function removeTask() { if (!detail || !window.confirm('将任务移入最近删除？关联会话、历史证据和审计记录会保留。')) return; try { await api(`/api/tasks/${detail.id}`, { method: 'DELETE' }); setSelectedId(null); setDetail(null); setMessage('任务已移入最近删除，可恢复；不会级联删除会话、证据或历史。'); await load() } catch (cause) { setMessage(cause instanceof Error ? cause.message : '删除失败'); setError(true) } }
  async function restoreTask(id: number) { try { await api(`/api/tasks/${id}/restore`, { method: 'POST', body: '{}' }); setTrash(false); setSelectedId(null); setDetail(null); setMessage('任务已恢复。'); setError(false); await load() } catch (cause) { setMessage(cause instanceof Error ? cause.message : '恢复失败'); setError(true) } }
  async function permanentlyDeleteTask(id: number) {
    try {
      const preview = await api(`/api/tasks/${id}/permanent-delete-preview`)
      if (preview.purged) { setMessage('该任务已彻底删除。'); setError(false); await load(); return }
      if (!preview.can_purge) { setMessage((preview.blockers || []).map((item: any) => item.message).join(' ') || preview.message || '任务暂时不能彻底删除。'); setError(true); return }
      if (!window.confirm(`彻底删除任务“${preview.title}”？此操作不可恢复。将清理任务证据、状态/日期历史和会话关联；独立会话、提案、其他任务及历史备份不变。`)) return
      const typed = window.prompt(`输入任务名称以确认永久删除：${preview.title}`)
      if (typed !== preview.title) return
      const result = await api(`/api/tasks/${id}/permanent-delete`, { method: 'POST', body: JSON.stringify({ confirm_title: typed }) })
      if (!result.purged) throw new Error('服务端未确认任务已彻底删除。')
      setSelectedId(null); setDetail(null); setTasks((items) => items.filter((item) => item.id !== id))
      setMessage(result.idempotent_replay ? '该任务此前已彻底删除。' : '任务已从当前数据库彻底删除；独立备份可能仍保留删除前内容。')
      setError(false); await load()
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : '永久删除失败'); setError(true) }
  }
  async function decideProposal(id: number, action: 'approve' | 'reject') { try { await api(`/api/agent/proposals/${id}`, { method: 'PATCH', body: JSON.stringify({ action }) }); setMessage(action === 'approve' ? '草案已审批并按业务规则写入。' : '草案已拒绝，正式任务未变化。'); const [pending] = await Promise.all([api('/api/agent/proposals'), load()]); setProposals(pending) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '草案操作失败'); setError(true) } }
  async function saveProposalEdit(id: number, action: 'edit' | 'approve') { try { await api(`/api/agent/proposals/${id}`, { method: 'PATCH', body: JSON.stringify({ action, items: proposalItems[id] }) }); setMessage(action === 'edit' ? '草案编辑已保存，尚未创建正式任务。' : '编辑后的草案已批准并写入正式任务。'); const pending = await api('/api/agent/proposals'); setProposals(pending); setProposalItems(Object.fromEntries(pending.map((proposal: any) => [proposal.id, proposal.items || []]))); await load() } catch (cause) { setMessage(cause instanceof Error ? cause.message : '草案编辑失败'); setError(true) } }
  async function trashProposal(proposal: any) { if (!window.confirm(proposal.status === 'PENDING' ? '取消并拒绝此待处理提案，再移入最近删除？不会创建正式任务。' : '将此提案移入最近删除？已批准的正式任务不会删除。')) return; try { await api(`/api/agent/proposals/${proposal.id}`, { method: 'DELETE' }); await refreshProposals(proposalTrash, proposalStatusFilter); setMessage('提案已移入最近删除；待处理项已取消，不会写入任务。'); setError(false) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '移入最近删除失败'); setError(true) } }
  async function restoreProposal(proposal: any) { try { await api(`/api/agent/proposals/${proposal.id}/restore`, { method: 'POST', body: '{}' }); await refreshProposals(true, proposalStatusFilter); setMessage('提案已恢复。'); setError(false) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '恢复提案失败'); setError(true) } }
  async function permanentlyDeleteProposal(proposal: any) { if (!proposal.deleted || proposal.purged) return; if (!window.confirm(`清除提案“${proposal.title}”的正文、条目和说明？已批准任务不受影响；独立历史备份不变。`)) return; const typed = window.prompt(`输入提案标题以确认清除：${proposal.title}`); if (typed !== proposal.title) return; try { await api(`/api/agent/proposals/${proposal.id}/permanent-delete`, { method: 'POST', body: JSON.stringify({ confirm_title: typed }) }); await refreshProposals(true, proposalStatusFilter); setMessage('提案正文已从当前数据库清除；独立备份可能仍保留删除前内容。'); setError(false) } catch (cause) { setMessage(cause instanceof Error ? cause.message : '永久清除失败'); setError(true) } }

  return <div className={`content task-workspace task-workspace-ai-first${detail ? ' has-selected-task' : ''}`}><section className="panel task-ai-manager"><div className="panel-head"><div><p className="eyebrow">AI TASK MANAGER</p><h3>用一句话管理任务</h3><p className="muted">创建模式提交即授权写入；查看与规划只读；修改模式只处理现有任务。新任务保持 TODO。</p></div><div className="task-header-actions"><button className="secondary" onClick={onOpenReview}>每日结算</button><button className="secondary" onClick={() => void newTaskConversation()}>新建指令会话</button></div></div>
    <div className="task-mode-switch" role="group" aria-label="AI 任务操作模式">{([['create','创建任务'],['plan','查看与规划'],['modify','修改任务']] as const).map(([value,label]) => <button type="button" key={value} className={taskMode === value ? 'chat-filter active' : 'chat-filter'} aria-label={`${label}模式`} aria-pressed={taskMode === value} onClick={() => { setTaskMode(value); if (submissionOperation.current?.mode !== value) { submissionOperation.current = null; setOperationUncertain(false) } }}>{label}</button>)}</div>
    <div className="task-manager-history-picker"><select aria-label="任务管理员历史会话" value={taskConversationId} onChange={(event) => void loadTaskConversation(event.target.value)}><option value="">临时会话（若关闭聊天保存，不跨设备保留）</option>{taskConversations.map((item) => <option key={item.id} value={item.id}>{item.title || '任务管理员'}{item.deleted ? ' · 最近删除' : ''} · {new Date(item.updated_at).toLocaleDateString()}</option>)}</select><button className="chat-action" disabled={!taskConversationId || taskConversationTrash} onClick={() => void renameTaskConversation()}>重命名</button>{taskConversationTrash ? <><button className="chat-action" disabled={!taskConversationId} onClick={() => void restoreTaskConversation()}>恢复</button><button className="chat-action danger" disabled={!taskConversationId} onClick={() => void permanentlyDeleteTaskConversation()}>彻底删除</button></> : <button className="chat-action danger" disabled={!taskConversationId} onClick={() => void trashTaskConversation()}>移入最近删除</button>}<button className={taskConversationTrash ? 'chat-filter active' : 'chat-filter'} onClick={() => { const next = !taskConversationTrash; setTaskConversationTrash(next); setAssistantMessages([]); setTaskConversationId(''); void refreshTaskConversations(next) }}>{taskConversationTrash ? '返回会话' : '最近删除'}</button></div>
    {taskConversationTrash && <p className="muted">最近删除中的会话正文不可打开；可恢复，或输入标题确认永久清除。永久清除不影响正式任务，但历史备份可能保留删除前内容。</p>}
    {assistantMessages.length > 0 && <div className="task-manager-transcript" aria-live="polite">{assistantMessages.slice(-8).map((item) => <article key={item.id} className={`task-manager-message ${item.role}`}><small>{item.role === 'user' ? '你' : 'Personal OS'} · {new Date(item.created_at).toLocaleString()}</small><p>{item.content}</p></article>)}</div>}
    {pendingConfirmations.map((operation) => <div className="notice info-notice task-delete-confirm" key={operation.operation_id}><span>等待二次确认：{operation.tasks?.[0]?.title || '任务删除请求'}</span><button className="secondary" onClick={() => void confirmPendingDelete(operation)}>确认移入最近删除</button></div>)}
    <form className="task-manager-form" onSubmit={submitTaskInstruction}><textarea aria-label="AI 任务管理员输入" value={assistantDraft} onChange={(event) => setAssistantDraft(event.target.value)} placeholder={taskMode === 'create' ? '例如：创建两个任务：1. 软件工程学习实战并修改德语 Agent；2. 个人网站开发维护。' : taskMode === 'plan' ? '例如：读取我今天的任务，建议先后顺序和下一步；此模式不会写入。' : '例如：把“个人网站开发维护”的预计时间改为 45 分钟。'} rows={2} maxLength={4000} /><button className="primary" disabled={assistantBusy || !assistantDraft.trim() || taskConversationTrash || operationUncertain}>{assistantBusy ? '处理中…' : taskMode === 'create' ? '创建任务' : taskMode === 'plan' ? '生成建议' : '修改任务'}</button>{operationUncertain && <button type="button" className="secondary" onClick={() => void checkTaskOperation()}>检查操作状态</button>}</form>
    {lastOperation && <button className="text-btn" onClick={() => void undoLastTaskOperation()}>撤销上次任务操作</button>}
  </section><section className="panel task-browser"><div className="panel-head"><div><p className="eyebrow">ONE TASK SYSTEM</p><h3>任务时间线</h3><p className="muted">时间 · 具体任务 · 所属领域；其他字段收在详情中。</p></div><button className="secondary" onClick={() => void load()}>刷新</button></div>
    <label className="task-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索任务标题、描述、验收条件" /></label>
    <div className="task-filter-row"><select aria-label="时间线筛选" value={timeline} disabled={archived || trash} onChange={(event) => setTimeline(event.target.value)}><option value="">全部日期</option><option value="today">今日</option><option value="future">未来</option><option value="overdue">已逾期</option></select><select aria-label="领域筛选" value={module} onChange={(event) => setModule(event.target.value)}><option value="">全部领域</option>{domains.map((item) => <option key={item} value={item}>{item}</option>)}</select><select aria-label="状态筛选" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option>{statuses.map((item) => <option key={item}>{item}</option>)}</select><button className={archived ? 'chat-filter active' : 'chat-filter'} onClick={() => { setArchived((value) => !value); setTrash(false); setTimeline('') }}><Archive size={14} />{archived ? '返回任务' : '已归档'}</button><button className={trash ? 'chat-filter active' : 'chat-filter'} onClick={() => { setTrash((value) => !value); setArchived(false); setTimeline(''); setSelectedId(null); setDetail(null) }}><Trash2 size={14} />最近删除</button></div>
    <div className="task-workspace-list task-timeline">{tasks.map((task) => <article className={task.id === selectedId ? 'task-workspace-row active' : 'task-workspace-row'} key={task.id}><button onClick={() => void selectTask(task.id, trash)}><span className="task-row-date">{task.start_date || '日期未知'}{task.due_date && task.due_date !== task.start_date ? ` → ${task.due_date}` : ''}</span><strong>{task.is_pinned ? '★ ' : ''}{task.title}</strong><span className="task-row-meta">{task.module} <i className={`task-status-pill ${task.status.toLowerCase()}`}>{task.status}</i></span></button>{trash ? <div className="task-header-actions"><button className="chat-action" aria-label={`恢复 ${task.title}`} onClick={() => void restoreTask(task.id)}><RotateCcw size={15} />恢复</button><button className="chat-action danger" aria-label={`彻底删除 ${task.title}`} onClick={() => void permanentlyDeleteTask(task.id)}><Trash2 size={15} />彻底删除</button></div> : null}</article>)}{tasks.length === 0 && <p className="empty">{trash ? '最近删除为空' : '没有匹配的任务。'}</p>}</div>
    <details className="task-subsection proposal-inbox"><summary>{proposalTrash ? '最近删除的提案' : '历史提案'} · {proposals.length} 项（待处理 {proposals.filter((proposal) => proposal.status === 'PENDING').length}）</summary><div className="task-filter-row"><select aria-label="提案状态筛选" value={proposalStatusFilter} onChange={(event) => { setProposalStatusFilter(event.target.value); void refreshProposals(proposalTrash, event.target.value) }}><option value="">全部状态</option>{['PENDING','APPROVED','REJECTED','PURGED'].map((value) => <option key={value}>{value}</option>)}</select><button className="chat-filter" onClick={() => { const next = !proposalTrash; setProposalTrash(next); void refreshProposals(next, proposalStatusFilter) }}>{proposalTrash ? '返回提案' : '最近删除'}</button></div>{proposals.length === 0 && <p className="empty">此列表为空。</p>}{proposals.map((proposal) => <article key={proposal.id} className="task-history-row"><strong>{proposal.title || `${proposal.proposal_type} 草案`} · #{proposal.id}</strong><small>状态：{proposal.status} · {proposal.purged ? '正文已永久清除；独立备份可能仍保留旧内容。' : '正式任务与提案生命周期相互独立。'}</small>{Array.from(new Set<number>((proposal.items || []).flatMap((item: any) => item.knowledge_card_ids || []))).map((id) => <button className="chat-knowledge-source" key={id} onClick={() => onOpenKnowledge?.(id)}><strong>知识来源卡片 #{id}</strong><span>打开已引用的知识卡片</span></button>)}{(proposalItems[proposal.id] || []).map((item: any, index: number) => <div className="proposal-item" key={`${proposal.id}-${index}`}>{proposal.status === 'PENDING' && !proposal.deleted ? <><label>任务名称<input value={item.title || ''} onChange={(event) => setProposalItems((previous) => ({ ...previous, [proposal.id]: (previous[proposal.id] || []).map((value, i) => i === index ? { ...value, title: event.target.value } : value) }))} /></label><label>具体行动<textarea rows={2} value={item.action || ''} onChange={(event) => setProposalItems((previous) => ({ ...previous, [proposal.id]: (previous[proposal.id] || []).map((value, i) => i === index ? { ...value, action: event.target.value } : value) }))} /></label></> : <strong>{item.title || '未命名任务'} · {item.domain || 'UNKNOWN'}</strong>}<small>{item.domain || 'UNKNOWN'} · {item.estimated_minutes || 'UNKNOWN'} 分钟 · 验收：{item.acceptance_criteria || 'UNKNOWN'}</small></div>)}<div className="task-header-actions">{proposal.deleted ? <><button className="secondary" onClick={() => void restoreProposal(proposal)} disabled={proposal.purged}>恢复</button><button className="text-btn danger-btn" onClick={() => void permanentlyDeleteProposal(proposal)} disabled={proposal.purged || proposal.status === 'PENDING'}>永久清除正文</button></> : <><button className="text-btn danger-btn" onClick={() => void trashProposal(proposal)}>移入最近删除</button>{proposal.status === 'PENDING' && <><button className="secondary" onClick={() => void saveProposalEdit(proposal.id, 'edit')}>保存草案编辑</button><button className="primary" onClick={() => void saveProposalEdit(proposal.id, 'approve')}>批准并写入</button><button className="text-btn danger-btn" onClick={() => void decideProposal(proposal.id, 'reject')}>拒绝</button></>}</>}</div></article>)}</details>
    <details className="task-create-drawer"><summary><Plus size={15} /> 新建任务</summary><form className="task-detail-form" onSubmit={createTask}><label>名称<input required maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>描述<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label><div className="task-form-grid"><label>领域<select value={domain} onChange={(event) => setDomain(event.target.value)}>{domains.map((item) => <option key={item}>{item}</option>)}</select></label><label>优先级<select value={priority} onChange={(event) => setPriority(Number(event.target.value))}><option value={1}>高</option><option value={2}>普通</option><option value={3}>低</option></select></label><label>预计分钟<input type="number" min="1" max="1440" value={minutes} onChange={(event) => setMinutes(event.target.value)} /></label></div><DateFields startDate={startDate || today} dueDate={dueDate || startDate || today} durationDays={durationDays || '1'} setStart={setStart} setDue={setDue} setDuration={setDuration} /><label>验收条件<input value={criteria} onChange={(event) => setCriteria(event.target.value)} /></label><button className="primary"><Plus size={14} /> 保存任务</button></form></details>
  </section>
  <section className="panel task-detail-panel">{detail && trash ? <><div className="panel-head"><div><p className="eyebrow">最近删除 · TASK #{detail.id}</p><h3>{detail.title}</h3><p className="muted">此任务已软删除；可恢复，或在依赖预检通过并二次确认后彻底删除。独立会话、提案和历史备份不会随任务删除。</p></div><div className="task-header-actions"><button className="secondary" onClick={() => void restoreTask(detail.id)}>恢复任务</button><button className="chat-action danger" onClick={() => void permanentlyDeleteTask(detail.id)}>彻底删除</button></div></div></> : detail ? <><div className="panel-head"><div><p className="eyebrow">TASK #{detail.id} · {detail.status}</p><h3>{detail.title}</h3><p className="muted">{detail.start_date || '日期未知'}{detail.due_date ? ` → ${detail.due_date}` : ''} · {detail.module}</p></div><div className="task-header-actions"><button className="chat-action" title="置顶任务" aria-label="置顶任务" onClick={() => void togglePin()}>{detail.is_pinned ? '取消置顶' : '置顶'}</button><button className="chat-action" onClick={() => void toggleArchive()}><Archive size={14} />{detail.archived ? '恢复' : '归档'}</button><button className="chat-action danger" aria-label="删除任务" onClick={() => void removeTask()}><Trash2 size={14} /></button></div></div><details className="task-detail-disclosure"><summary>展开详情、状态、证据与历史</summary>
    <form className="task-detail-form" onSubmit={saveTask}><label>任务名称<input required maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>描述<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label><div className="task-form-grid"><label>领域<select value={domain} onChange={(event) => setDomain(event.target.value)}>{domains.map((item) => <option key={item}>{item}</option>)}</select></label><label>优先级<select value={priority} onChange={(event) => setPriority(Number(event.target.value))}><option value={1}>高</option><option value={2}>普通</option><option value={3}>低</option></select></label><label>预计分钟<input type="number" min="1" max="1440" value={minutes} onChange={(event) => setMinutes(event.target.value)} /></label></div><DateFields startDate={startDate} dueDate={dueDate} durationDays={durationDays} setStart={setStart} setDue={setDue} setDuration={setDuration} /><label>验收条件<input value={criteria} onChange={(event) => setCriteria(event.target.value)} /></label><button className="primary">保存编辑</button></form>
    <label className="task-status-picker">状态<select value={detail.status} onChange={(event) => void changeTaskStatus(event.target.value)}>{statuses.map((item) => <option key={item}>{item}</option>)}</select><small>DONE 仍需先添加证据；不能由此快捷控件绕过后端校验。</small></label>
    <section className="task-subsection"><h4>执行证据 · {detail.evidence.length}</h4><form className="task-evidence-form" onSubmit={addEvidence}><textarea value={evidence} onChange={(event) => setEvidence(event.target.value)} placeholder="记录实际执行结果或证据" rows={2} /><button className="secondary"><Check size={14} /> 添加自报证据</button></form>{detail.evidence.map((item) => <article className="task-evidence-row" key={item.id}><p>{item.content}</p><small>{item.verification_level} · {item.created_at}{item.source ? ` · ${item.source}` : ''}</small></article>)}</section>
    <section className="task-subsection"><h4>关联会话与消息 · {detail.links.length}</h4>{detail.links.map((link) => <article className="task-link-row" key={link.id}><button className="text-btn" onClick={() => onOpenConversation(link.domain, link.conversation_id, link.message_id)}>{link.conversation_title} · {link.domain}{link.message_id ? ` · 消息 #{link.message_id}` : ''}<ArrowUpRight size={14} /></button>{link.message_preview && <small>{link.message_preview}</small>}</article>)}<form className="task-link-form" onSubmit={submitLinkConversation}><select aria-label="选择来源会话" value={linkConversationId} onChange={(event) => setLinkConversationId(event.target.value)}><option value="">选择会话</option>{conversations.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.domain}</option>)}</select><input aria-label="消息 ID（可选）" type="number" min="1" value={linkMessage} onChange={(event) => setLinkMessage(event.target.value)} placeholder="消息 ID（可选）" /><button className="secondary">关联</button></form></section>
    <section className="task-subsection"><h4>关联知识 · {linkedKnowledge.length}</h4>{linkedKnowledge.map((card) => <button className="text-btn" key={card.id} onClick={() => onOpenKnowledge?.(card.id, card.domain)}>{card.title} · {card.domain} <ArrowUpRight size={14} /></button>)}{linkedKnowledge.length === 0 && <small>暂无关联知识卡片。</small>}</section>
    <section className="task-subsection"><h4>状态历史</h4>{detail.history.map((item) => <div className="task-history-row" key={item.id}><strong>{item.from_status || '新建'} → {item.to_status}</strong><small>{item.reason || 'UNKNOWN'} · {item.created_at}</small></div>)}</section>
    <section className="task-subsection"><h4>日期调整历史</h4>{(detail.date_history || []).map((item) => <div className="task-history-row" key={item.id}><strong>{item.old_start_date || 'UNKNOWN'} → {item.new_start_date || 'UNKNOWN'} · 截止 {item.old_due_date || 'UNKNOWN'} → {item.new_due_date || 'UNKNOWN'}</strong><small>{item.reason} · {item.changed_at}</small></div>)}{(detail.date_history || []).length === 0 && <small>尚无日期调整记录。</small>}</section>
  </details></> : <div className="empty task-detail-empty">选择一项任务查看细节，或新建一项具有明确验收条件的任务。</div>}</section>
  {message && <div className={error ? 'notice error-notice task-flash' : 'notice task-flash'}>{message}</div>}
  </div>
}

function DateFields({ startDate, dueDate, durationDays, setStart, setDue, setDuration }: { startDate: string; dueDate: string; durationDays: string; setStart: (value: string) => void; setDue: (value: string) => void; setDuration: (value: string) => void }) {
  return <div className="task-form-grid task-date-fields"><label>开始日期<input type="date" value={startDate} onChange={(event) => setStart(event.target.value)} /></label><label>截止日期<input type="date" value={dueDate} onChange={(event) => setDue(event.target.value)} /></label><label>预计持续天数<input type="number" min="1" max="3650" value={durationDays} onChange={(event) => setDuration(event.target.value)} /></label></div>
}
