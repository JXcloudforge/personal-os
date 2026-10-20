import { useEffect, useMemo, useState } from 'react'
import { Archive, ArrowDownToLine, ArrowUpRight, BookOpen, Check, ChevronDown, Clock3, Plus, RotateCcw, Search, Sparkles, Tag, Trash2, X } from 'lucide-react'
import './knowledge.css'

export type KnowledgeDraftIntent = { content: string; domain: string; conversationId?: string | null; messageId?: number | null; selectedText?: string | null; openRefinement?: boolean }
type Source = { id: number; evidence_kind: string; conversation_id?: string | null; message_id?: number | null; agent_run_id?: number | null; url?: string | null; title?: string | null; fetch_status: string; fetch_error_code?: string | null; retrieved_at?: string | null }
type Card = { id: number; title: string; summary?: string | null; content_markdown: string; domain: string; knowledge_type: string; review_status: string; is_pinned: boolean; archived: boolean; deleted: boolean; tags: string[]; sources: Source[]; task_ids: number[]; history?: Array<{ event: string; created_at: string }>; origin_note?: string | null }
type Task = { id: number; title: string; module: string; status: string }
type RefinementCandidate = { worth_saving: boolean; title: string; summary: string; content_markdown: string; limitations: string[] }

const domains = [
  ['general', '通用'], ['bilingual', '双语学习'], ['global', '全球信息'], ['health', '健康管理'],
  ['stem', '理工学习'], ['projects', '市场项目'], ['finance', '基础财务'], ['reviews', '每日复盘'],
]
const types = [['Concept', '概念'], ['Procedure', '操作方法'], ['Practice', '实践经验'], ['Question', '待解决问题'], ['Insight', '个人总结']]
const evidenceLabels: Record<string, string> = { USER_NOTE: '用户笔记', MODEL_GENERATED: 'AI 回复', SEARCH_SNIPPET: '搜索摘要', SOURCE_FETCHED: '来源正文已抓取', USER_VERIFIED: '用户核验', PRACTICE_VERIFIED: '实践核验' }
const fetchLabels: Record<string, string> = { no_external_source: '无外部来源', model_message: '关联原始 AI 消息', search_summary_only: '仅搜索摘要，未抓取正文', search_summary_only_fetch_failed: '仅搜索摘要，正文抓取失败', fetched_body: '已抓取该页面正文（不代表结论已核实）' }

async function api(path: string, options: RequestInit = {}) {
  const csrf = document.cookie.split('; ').find((item) => item.startsWith('personal_os_csrf='))?.split('=')[1]
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  const response = await fetch(path, { ...options, headers, credentials: 'include' })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = payload.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message || detail?.code || `请求失败 (${response.status})`)
  }
  return response.status === 204 ? null : response.json()
}

export function KnowledgeWorkspace({ initialDomain = 'general', initialCardId = null, draftFromMessage = null, onOpenTask, onReturnToChat, onBack }: { initialDomain?: string; initialCardId?: number | null; draftFromMessage?: KnowledgeDraftIntent | null; onOpenTask: (id: number) => void; onReturnToChat?: () => void; onBack: () => void }) {
  const [cards, setCards] = useState<Card[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selected, setSelected] = useState<Card | null>(null)
  const [domain, setDomain] = useState(initialDomain)
  const [query, setQuery] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [reviewFilter, setReviewFilter] = useState('')
  const [archived, setArchived] = useState(false)
  const [trash, setTrash] = useState(false)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [tasks, setTasks] = useState<Task[]>([])
  const [taskId, setTaskId] = useState('')
  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [content, setContent] = useState('')
  const [kind, setKind] = useState('Concept')
  const [reviewStatus, setReviewStatus] = useState('DRAFT')
  const [tags, setTags] = useState('')
  const [draftOrigin, setDraftOrigin] = useState<KnowledgeDraftIntent | null>(null)
  const [refinementSource, setRefinementSource] = useState<'message' | 'selection'>('message')
  const [candidate, setCandidate] = useState<RefinementCandidate | null>(null)
  const [refining, setRefining] = useState(false)
  const [showDetails, setShowDetails] = useState(true)
  const domainLabel = useMemo(() => domains.find(([key]) => key === domain)?.[1] || '通用', [domain])

  async function loadList() {
    try {
      const params = new URLSearchParams({ include_archived: String(archived && !trash), include_deleted: String(trash), limit: '100' })
      if (domain) params.set('domain', domain)
      if (query.trim()) params.set('q', query.trim())
      if (tagFilter.trim()) params.set('tag', tagFilter.trim())
      if (typeFilter) params.set('knowledge_type', typeFilter)
      if (reviewFilter) params.set('review_status', reviewFilter)
      const rows: Card[] = await api(`/api/knowledge?${params}`)
      setCards(rows)
      if (selectedId && !trash) {
        const current = rows.find((card) => card.id === selectedId)
        if (current) setSelected(current)
      }
      setError('')
    } catch (cause) { setError(cause instanceof Error ? cause.message : '知识列表读取失败') }
  }

  useEffect(() => { void loadList() }, [domain, query, tagFilter, typeFilter, reviewFilter, archived, trash])
  useEffect(() => {
    void api('/api/tasks').then(setTasks).catch(() => setTasks([]))
  }, [])

  useEffect(() => {
    if (draftFromMessage) {
      const heading = draftFromMessage.content.split('\n').map((line) => line.replace(/^#+\s*/, '').trim()).find(Boolean) || '来自 AI 对话的知识草稿'
      setDomain(draftFromMessage.domain)
      setTitle(heading.slice(0, 200))
      setContent(draftFromMessage.content)
      setSummary('')
      setKind('Concept')
      setTags('')
      setDraftOrigin(draftFromMessage)
      setRefinementSource(draftFromMessage.selectedText ? 'selection' : 'message')
      setCandidate(null)
      setSelectedId(null); setSelected(null); setEditing(true)
      setNotice(draftFromMessage.messageId ? `草稿已带入消息 #${draftFromMessage.messageId}；来源将从该消息实际关联的 Run 审计记录读取。${draftFromMessage.openRefinement ? ' AI 提炼需由你在下方按钮主动触发。' : ''}` : '当前聊天未持久化；点击保存后将建立独立知识记录，不会回写聊天。')
    }
  }, [draftFromMessage])

  useEffect(() => {
    if (initialCardId) void openCard(initialCardId)
  }, [initialCardId])

  async function openCard(id: number) {
    try {
      const card: Card = await api(`/api/knowledge/${id}${trash ? '?include_deleted=true' : ''}`)
      setCandidate(null)
      setSelected(card); setSelectedId(id); setDraftOrigin(null); setEditing(false)
      setTitle(card.title); setSummary(card.summary || ''); setContent(card.content_markdown); setKind(card.knowledge_type); setReviewStatus(card.review_status); setTags(card.tags.join(', ')); setError('')
    } catch (cause) { setError(cause instanceof Error ? cause.message : '知识详情读取失败') }
  }

  function startNew() {
    setSelected(null); setSelectedId(null); setDraftOrigin(null); setCandidate(null); setTitle(''); setSummary(''); setContent(''); setKind('Concept'); setReviewStatus('DRAFT'); setTags(''); setEditing(true); setNotice('手动知识卡片会直接保存到本地数据库，不会发送给 AI。')
  }

  async function saveCard(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true); setError(''); setNotice('')
    try {
      const body: Record<string, unknown> = { title: title.trim(), summary: summary.trim() || null, content_markdown: content, domain, knowledge_type: kind, review_status: reviewStatus, tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean) }
      if (!selected && draftOrigin?.messageId && draftOrigin.conversationId) { body.conversation_id = draftOrigin.conversationId; body.message_id = draftOrigin.messageId }
      const result: Card = selected ? await api(`/api/knowledge/${selected.id}`, { method: 'PATCH', body: JSON.stringify(body) }) : await api('/api/knowledge', { method: 'POST', body: JSON.stringify(body) })
      setSelected(result); setSelectedId(result.id); setDraftOrigin(null); setEditing(false); setNotice(`知识卡片 #${result.id} 已保存。原聊天与来源记录未被修改。`); await loadList()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '保存知识失败') }
    finally { setBusy(false) }
  }

  async function refineDraft() {
    if (!draftOrigin?.messageId || !draftOrigin.conversationId) {
      setError('AI 提炼需要一条已保存的 AI 回复；你仍可直接编辑并保存当前草稿。')
      return
    }
    const selected = refinementSource === 'selection' ? draftOrigin.selectedText?.trim() : undefined
    if (refinementSource === 'selection' && !selected) {
      setError('没有捕获到选中文本，请返回聊天选择文字后再次打开，或选择整条回复。')
      return
    }
    const confirmed = window.confirm('将只把当前 AI 回复' + (selected ? '中你选中的片段' : '') + '发送给已配置的 DeepSeek，用于生成标题、摘要和正文。不会发送其他聊天历史，也不会联网或自动保存。请先确认内容不含不应外发的隐私、健康/财务细节或凭证。继续？')
    if (!confirmed) return
    setRefining(true); setError(''); setNotice('正在进行一次 AI 提炼请求…')
    try {
      const result = await api('/api/knowledge/refine-from-message', { method: 'POST', body: JSON.stringify({ conversation_id: draftOrigin.conversationId, message_id: draftOrigin.messageId, ...(selected ? { selected_text: selected } : {}), sensitive_data_consent: true }) })
      setCandidate(result.candidate)
      setNotice(`提炼候选已返回（Run #${result.run_id}；Token ${result.usage?.input_tokens ?? 'UNKNOWN'} 输入 / ${result.usage?.output_tokens ?? 'UNKNOWN'} 输出）。候选尚未保存或审阅。`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'AI 提炼失败；原草稿保持不变。'); setNotice('') }
    finally { setRefining(false) }
  }

  function acceptCandidate() {
    if (!candidate) return
    setTitle(candidate.title)
    setSummary(candidate.summary)
    const limitations = candidate.limitations.length ? `\n\n## 限制与待核验\n\n${candidate.limitations.map((item) => `- ${item}`).join('\n')}` : ''
    setContent(`${candidate.content_markdown}${limitations}`)
    setCandidate(null)
    setNotice('已将提炼内容填入编辑草稿；领域、标签和审阅状态未改变。请核对后再点击“保存为知识”。')
  }

  async function mutateCard(path: string, method: string, body?: Record<string, unknown>) {
    if (!selected) return
    try {
      const result = await api(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) })
      if (method === 'DELETE') { setSelected(null); setSelectedId(null); setNotice('已移入最近删除；卡片可恢复。') }
      else if (result?.id) { setSelected(result); setNotice('知识记录已更新。') }
      await loadList()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '知识操作失败') }
  }

  async function permanentlyDelete() {
    if (!selected || !selected.deleted) return
    try {
      const preview = await api(`/api/knowledge/${selected.id}/permanent-delete-preview`)
      const confirmation = window.confirm(`彻底清理“${preview.title}”？仅清空这张知识卡片正文、标签及其关联，不删除原任务/聊天/来源。历史备份可能仍保留旧内容。`)
      if (!confirmation) return
      const typed = window.prompt(`输入标题确认永久清理：${preview.title}`)
      if (typed !== preview.title) return
      await api(`/api/knowledge/${selected.id}/permanent-delete`, { method: 'POST', body: JSON.stringify({ confirm_title: typed }) })
      setSelected(null); setSelectedId(null); setNotice('知识正文已清理；原任务、聊天与历史备份未删除。'); await loadList()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '彻底清理失败') }
  }

  async function addTaskLink() {
    if (!selected || !taskId) return
    try { await api(`/api/knowledge/${selected.id}/tasks/${taskId}`, { method: 'POST', body: '{}' }); setTaskId(''); await openCard(selected.id); await loadList() }
    catch (cause) { setError(cause instanceof Error ? cause.message : '关联任务失败') }
  }

  async function removeTaskLink(id: number) {
    if (!selected) return
    try { await api(`/api/knowledge/${selected.id}/tasks/${id}`, { method: 'DELETE' }); await openCard(selected.id); await loadList() }
    catch (cause) { setError(cause instanceof Error ? cause.message : '解除任务关联失败') }
  }

  async function download(format: 'markdown' | 'json') {
    if (!selected) return
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('personal_os_csrf='))?.split('=')[1]
    try {
      const response = await fetch(`/api/knowledge/${selected.id}/export?format=${format}`, { credentials: 'include', headers: csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {} })
      if (!response.ok) throw new Error('导出失败')
      const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement('a')
      anchor.href = url; anchor.download = `knowledge-${selected.id}.${format === 'json' ? 'json' : 'md'}`; anchor.click(); URL.revokeObjectURL(url)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '导出失败') }
  }

  const titleForDomain = (key: string) => domains.find(([id]) => id === key)?.[1] || key
  return <div className="content knowledge-workspace">
    <div className="knowledge-heading"><div><p className="eyebrow">KNOWLEDGE / LOCAL INBOX</p><h3>{domain === 'general' ? '统一知识收集箱' : `${domainLabel}知识收集箱`}</h3><p>一套本地知识服务，按领域分类；只收录你明确保存的内容。</p></div><div className="knowledge-heading-actions">{onReturnToChat && <button className="secondary" onClick={onReturnToChat}>返回原对话</button>}<button className="secondary" onClick={onBack}>返回总览</button></div></div>
    {notice && <div className="notice inline-notice">{notice}</div>}{error && <div className="notice error-notice">{error}</div>}
    <div className="knowledge-layout">
      <section className="panel knowledge-list-panel">
        <div className="knowledge-list-toolbar"><label className="knowledge-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题与正文" /></label><button className="primary" onClick={startNew} disabled={trash}><Plus size={15} /> 新增知识</button></div>
        <div className="knowledge-filters"><select aria-label="知识领域" value={domain} onChange={(event) => setDomain(event.target.value)}>{domains.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><select aria-label="知识类型" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="">所有类型</option>{types.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><select aria-label="审核状态" value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value)}><option value="">所有审核状态</option><option value="DRAFT">草稿</option><option value="REVIEWED">已审阅</option></select><label className="tag-filter"><Tag size={13} /><input value={tagFilter} onChange={(event) => setTagFilter(event.target.value)} placeholder="标签" /></label></div>
        <div className="knowledge-list-actions"><button className={archived ? 'chat-filter active' : 'chat-filter'} onClick={() => { setArchived((value) => !value); setTrash(false); setSelected(null); setSelectedId(null) }}><Archive size={14} />{archived ? '退出归档' : '归档'}</button><button className={trash ? 'chat-filter active' : 'chat-filter'} onClick={() => { setTrash((value) => !value); setArchived(false); setSelected(null); setSelectedId(null) }}><Trash2 size={14} />{trash ? '返回收件箱' : '最近删除'}</button><span>{cards.length} 条</span></div>
        <div className="knowledge-card-list">{cards.map((card) => <button key={card.id} className={`knowledge-card-row ${selectedId === card.id ? 'active' : ''}`} onClick={() => void openCard(card.id)}><span className="knowledge-card-row-top"><strong>{card.is_pinned && '★ '}{card.title}</strong><span className="tag">{card.review_status === 'REVIEWED' ? '已审阅' : '草稿'}</span></span><span className="knowledge-card-summary">{card.summary || card.content_markdown.slice(0, 130)}</span><span className="knowledge-card-meta">{titleForDomain(card.domain)} · {types.find(([key]) => key === card.knowledge_type)?.[1] || card.knowledge_type}{card.sources.length ? ` · ${card.sources.some((source) => source.fetch_status === 'fetched_body') ? '含已抓取来源' : '仅摘要/来源记录'}` : ' · 无外部来源'}</span>{card.tags.length > 0 && <span className="knowledge-tags">{card.tags.slice(0, 4).map((tag) => <small key={tag}>#{tag}</small>)}</span>}</button>)}{cards.length === 0 && <div className="empty">{trash ? '最近删除为空。' : '此筛选下暂无知识。保存一条对话回复或手动新增。'}</div>}</div>
      </section>
      <section className="panel knowledge-detail-panel">
        {editing && draftOrigin?.messageId && <section className="knowledge-refine-box">
          <div className="knowledge-refine-heading"><div><strong><Sparkles size={16} /> AI 知识提炼</strong><p>仅在点击按钮后，将当前已保存的 AI 回复发送给 DeepSeek；不会发送聊天历史、不会联网，也不会自动保存。</p></div></div>
          <div className="knowledge-refine-controls">
            <label><input type="radio" name="refinement-source" checked={refinementSource === 'message'} onChange={() => setRefinementSource('message')} />整条 AI 回复</label>
            <label><input type="radio" name="refinement-source" checked={refinementSource === 'selection'} disabled={!draftOrigin.selectedText} onChange={() => setRefinementSource('selection')} />选中的片段{draftOrigin.selectedText ? `（${draftOrigin.selectedText.length} 字）` : '（未选择）'}</label>
            <button type="button" className="secondary" disabled={refining || (refinementSource === 'selection' && !draftOrigin.selectedText)} onClick={() => void refineDraft()}><Sparkles size={15} />{refining ? '正在提炼…' : 'AI 提炼标题、摘要和正文'}</button>
          </div>
          {candidate && <div className="knowledge-refine-candidate"><div className="knowledge-refine-candidate-head"><strong>{candidate.worth_saving ? '提炼候选 · 尚未保存或审阅' : '模型判断：没有明显值得独立保存的知识点 · 尚未保存'}</strong><div><button type="button" className="secondary" onClick={acceptCandidate}>采用候选到草稿</button><button type="button" className="text-btn" onClick={() => { setCandidate(null); setNotice('已放弃提炼结果，原编辑草稿保留。') }}>放弃候选</button></div></div><p><b>标题：</b>{candidate.title}</p><p><b>摘要：</b>{candidate.summary}</p><div className="knowledge-refine-preview"><pre>{candidate.content_markdown}{candidate.limitations.length ? `\n\n## 限制与待核验\n\n${candidate.limitations.map((item) => `- ${item}`).join('\n')}` : ''}</pre></div><details><summary>对照原始回复</summary><pre className="knowledge-refine-original">{draftOrigin.content}</pre></details></div>}
        </section>}
        {editing ? <form className="knowledge-editor" onSubmit={saveCard}><div className="panel-head"><div><p className="eyebrow">{selected ? `KNOWLEDGE #${selected.id}` : 'NEW KNOWLEDGE'}</p><h3>{selected ? '编辑知识卡片' : '整理为知识卡片'}</h3></div><button type="button" className="icon-btn" aria-label="关闭编辑" onClick={() => { setEditing(false); if (!selected) { setDraftOrigin(null); onReturnToChat?.() } }}><X size={17} /></button></div>{draftOrigin && <div className="knowledge-origin-note"><BookOpen size={15} /><span>{draftOrigin.messageId ? `源消息 #${draftOrigin.messageId} · 系统将从该消息对应的真实 Run 审计中读取来源。` : '聊天未保存；本次操作会明确新建独立知识记录。'}</span></div>}<label>标题<input required maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>简短摘要<textarea rows={2} maxLength={1200} value={summary} onChange={(event) => setSummary(event.target.value)} /></label><div className="knowledge-form-grid"><label>领域<select value={domain} onChange={(event) => setDomain(event.target.value)}>{domains.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>知识类型<select value={kind} onChange={(event) => setKind(event.target.value)}>{types.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>审阅状态<select value={reviewStatus} onChange={(event) => setReviewStatus(event.target.value)}><option value="DRAFT">草稿</option><option value="REVIEWED">已审阅</option></select></label></div><label>标签（逗号分隔）<input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="例如 tool-calling, python" /></label><label>正文（Markdown）<textarea className="knowledge-content-input" rows={12} required value={content} onChange={(event) => setContent(event.target.value)} /></label><p className="knowledge-privacy-note">保存只写入 Personal OS 本地数据库，不会发送给 DeepSeek 或 Notion。</p><div className="knowledge-editor-actions"><button type="button" className="secondary" onClick={() => { setEditing(false); if (!selected) { setDraftOrigin(null); onReturnToChat?.() } }}>取消</button><button className="primary" disabled={busy || !title.trim() || !content.trim()}>{busy ? '正在保存…' : selected ? '保存修改' : '保存为知识'}</button></div></form> : selected ? <><div className="knowledge-detail-head"><div><p className="eyebrow">{titleForDomain(selected.domain)} · {selected.knowledge_type}</p><h3>{selected.title}</h3><p>{selected.summary || '暂无摘要'}</p></div>{!selected.deleted && <div className="knowledge-detail-actions"><button className="chat-action" title="编辑知识" onClick={() => setEditing(true)}>编辑</button><button className="chat-action" title={selected.is_pinned ? '取消置顶' : '置顶'} onClick={() => void mutateCard(`/api/knowledge/${selected.id}`, 'PATCH', { is_pinned: !selected.is_pinned })}>{selected.is_pinned ? '取消置顶' : '置顶'}</button><button className="chat-action" title={selected.archived ? '恢复活跃列表' : '归档'} onClick={() => void mutateCard(`/api/knowledge/${selected.id}`, 'PATCH', { archived: !selected.archived })}><Archive size={14} />{selected.archived ? '恢复' : '归档'}</button><button className="chat-action danger" title="移入最近删除" onClick={() => { if (window.confirm('将此知识卡片移入最近删除？原任务、会话、来源和历史备份不会删除。')) void mutateCard(`/api/knowledge/${selected.id}`, 'DELETE') }}><Trash2 size={14} /></button></div>}{selected.deleted && <div className="knowledge-detail-actions"><button className="secondary" onClick={() => void mutateCard(`/api/knowledge/${selected.id}/restore`, 'POST')}>恢复</button><button className="chat-action danger" onClick={() => void permanentlyDelete()}>彻底清理</button></div>}</div><div className="knowledge-detail-toolbar"><span className="tag">{selected.review_status === 'REVIEWED' ? '已审阅' : '草稿'}</span><span className="tag">{types.find(([key]) => key === selected.knowledge_type)?.[1] || selected.knowledge_type}</span><button className="text-btn" onClick={() => setShowDetails((value) => !value)}><ChevronDown size={14} />{showDetails ? '收起详情' : '展开详情'}</button><button className="text-btn" onClick={() => void download('markdown')}><ArrowDownToLine size={14} /> Markdown</button><button className="text-btn" onClick={() => void download('json')}><ArrowDownToLine size={14} /> JSON</button></div>{showDetails && <><div className="knowledge-content"><pre>{selected.content_markdown}</pre></div><section className="knowledge-source-section"><h4>来源与原始关联</h4>{selected.sources.length ? selected.sources.map((source) => <div key={source.id} className="knowledge-source-row"><span className="knowledge-source-kind">{evidenceLabels[source.evidence_kind] || source.evidence_kind}</span><span>{fetchLabels[source.fetch_status] || source.fetch_status}</span>{source.url && <a href={source.url} target="_blank" rel="noreferrer">{source.title || source.url} <ArrowUpRight size={13} /></a>}{source.conversation_id && <span>会话 · 消息 #{source.message_id ?? '未知'}{source.agent_run_id ? ` · Run #${source.agent_run_id}` : ''}</span>}</div>) : <p className="muted">{selected.origin_note || '无外部来源；内容未被自动核验。'}</p>}</section><section className="knowledge-task-section"><div className="knowledge-subhead"><h4>关联任务</h4><div><select aria-label="选择关联任务" value={taskId} onChange={(event) => setTaskId(event.target.value)}><option value="">选择现有任务…</option>{tasks.filter((task) => !selected.task_ids.includes(task.id)).map((task) => <option key={task.id} value={task.id}>#{task.id} {task.title}</option>)}</select><button className="secondary" disabled={!taskId} onClick={() => void addTaskLink()}>关联</button></div></div>{selected.task_ids.map((id) => { const task = tasks.find((item) => item.id === id); return <div className="knowledge-task-link" key={id}><button className="text-btn" onClick={() => onOpenTask(id)}>#{id} {task?.title || '打开任务'} · {task?.status || ''} <ArrowUpRight size={14} /></button><button className="text-btn danger-btn" aria-label={`解除任务 ${id}`} onClick={() => void removeTaskLink(id)}>解除</button></div>})}{selected.task_ids.length === 0 && <p className="muted">尚未关联任务。</p>}</section></>}</> : <div className="knowledge-empty-detail"><BookOpen size={26} /><h3>从知识卡片开始</h3><p>{draftOrigin ? '已准备好聊天摘录；编辑后再保存，系统会根据真实审计关联来源。' : '选择左侧知识，或创建一张经过你整理的卡片。'}</p>{!editing && <button className="secondary" onClick={startNew}><Plus size={14} /> 新增知识</button>}</div>}
      </section>
    </div>
  </div>
}
