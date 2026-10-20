import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowUpRight, BookOpen, BrainCircuit, CheckCircle2, CircleDollarSign, ClipboardList, Dumbbell, Globe2, LayoutDashboard, LogOut, Menu, Plus, RefreshCw, Send, Settings, ShieldCheck, Target, X } from 'lucide-react'
import './styles.css'
import './theme.css'
import './task-manager.css'
import { AgentAuditPanel } from './AgentAuditPanel'
import { AIUsagePanel, AccountSettingsPanel, AppearanceSettingsPanel, applyAppearance, DataBackupPanel, PrivacySettingsPanel, SystemInfoPanel } from './SettingsPanels'
import { TasksWorkspace } from './TasksWorkspace'
import { ChatWorkspace, type ChatDomain } from './ChatWorkspace'
import { DailyReviewWorkspace } from './DailyReviewWorkspace'
import { KnowledgeWorkspace, type KnowledgeDraftIntent } from './KnowledgeWorkspace'
import { apiConnectionFailureMessage, apiFailureMessage } from './apiErrors'

type Task = { id: number; task_code?: string; title: string; module: string; status: string; priority: number; acceptance_criteria?: string; evidence_count: number; start_date?: string | null; due_date?: string | null }
type Dashboard = { today_date: string; task_counts: Record<string, number>; due_today_count: number; today_tasks: Task[]; completed_today_count: number; completed_today_tasks: Task[]; overdue_count: number; overdue_tasks: Task[]; unscheduled_count: number; unscheduled_tasks: Task[]; today_review: any; recent_tasks: Task[]; study_count: number; workout_count: number; review_count: number }
type AIProvider = { provider: string; base_url: string; model: string; enabled: boolean; configured: boolean; credential_source: string; key_hint: string | null; status: string; last_error?: string | null; total_prompt_tokens: number; total_completion_tokens: number }
type AgentProposal = { id: number; proposal_type: string; status: string; title: string }
type Goal = { id: number; domain: string; title: string; priority: number; enabled: boolean; notes?: string | null }
type ChatConversation = { id: string; title: string; domain: string; is_pinned: boolean; archived: boolean; deleted: boolean; updated_at?: string; last_message_preview?: string }
const modules = [
  { key: 'bilingual', label: '双语学习', icon: BookOpen, hint: '英语 · Deutsch' },
  { key: 'global', label: '全球信息', icon: Globe2, hint: '研究 · 来源' },
  { key: 'health', label: '健康管理', icon: Dumbbell, hint: '训练 · 恢复' },
  { key: 'stem', label: '理工学习', icon: BrainCircuit, hint: '知识 · 实验' },
  { key: 'projects', label: '市场项目', icon: Target, hint: '交付 · 反馈' },
  { key: 'finance', label: '基础财务', icon: CircleDollarSign, hint: '记录 · 成本' },
  { key: 'reviews', label: '每日复盘', icon: ClipboardList, hint: '证据 · 下一步' },
]

async function api(path: string, options: RequestInit = {}) {
  const csrf = document.cookie.split('; ').find((item) => item.startsWith('personal_os_csrf='))?.split('=')[1]
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  let response: Response
  try {
    response = await fetch(path, { ...options, headers, credentials: 'include' })
  } catch {
    throw new Error(apiConnectionFailureMessage())
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const message = apiFailureMessage(response.status, payload)
    const detail = (payload as { detail?: { request_id?: string } } | null)?.detail
    const requestId = response.headers.get('X-Request-ID') || detail?.request_id
    throw new Error(requestId ? `${message} (request_id: ${requestId})` : message)
  }
  return response.json()
}

function Login({ onLogin }: { onLogin: () => void }) {
  const [setup, setSetup] = useState<boolean | null>(null)
  const [setupMode, setSetupMode] = useState('web_local_only')
  const [registering, setRegistering] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [invitationCode, setInvitationCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { fetch('/api/auth/status').then((r) => r.json()).then((data) => { setSetup(data.setup_required); setSetupMode(data.setup_method || 'web_local_only') }) }, [])
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setError(''); setSubmitting(true)
    try {
      if (registering) {
        if (password !== passwordConfirm) { setError('两次输入的密码不一致。'); return }
        if (password.length < 12 || [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9\s]/].filter((pattern) => pattern.test(password)).length < 3) { setError('密码至少 12 位，并包含至少三种字符类型。'); return }
        const normalizedUsername = username.trim().toLowerCase()
        if (!/^[a-z0-9_.-]{3,80}$/.test(normalizedUsername)) { setError('用户名需为 3–80 位字母、数字、点、下划线或连字符。'); return }
        let challengeResponse: Response
        try { challengeResponse = await fetch('/api/auth/registration/challenge', { credentials: 'include', cache: 'no-store' }) }
        catch { throw new Error('网络暂时不可用。请稍后重新打开注册页面获取新的安全挑战。') }
        if (!challengeResponse.ok) {
          const status = challengeResponse.status
          throw new Error(status === 429 ? '注册请求过于频繁，请稍后再试。' : status === 503 ? '注册安全服务暂不可用，请稍后再试。' : `无法开始安全注册（HTTP ${status}），请刷新页面后重试。`)
        }
        const challengePayload = await challengeResponse.json() as { challenge?: string }
        if (!challengePayload.challenge) throw new Error('安全挑战无效，请刷新页面后重试。')
        let response: Response
        try {
          response = await fetch('/api/auth/register', {
            method: 'POST', credentials: 'include', cache: 'no-store',
            headers: { 'Content-Type': 'application/json', 'X-Registration-CSRF': challengePayload.challenge },
            body: JSON.stringify({ invitation_code: invitationCode, username: normalizedUsername, password }),
          })
        } catch {
          throw new Error('网络异常，注册结果暂时无法确认。请先尝试使用该用户名和密码登录；若未注册成功，请重新打开注册表单并获取新的安全挑战。')
        }
        if (!response.ok) {
          const status = response.status
          const message = status === 400 ? '注册无法完成。请检查邀请码与用户名，或联系邀请发放者。' : status === 403 ? '安全校验未通过，请重新获取挑战后再试。' : status === 422 ? '输入格式不符合要求，请检查用户名和密码规则。' : status === 429 ? '注册请求过于频繁，请稍后再试。' : status === 503 ? '注册安全服务暂不可用，请稍后再试。' : `注册请求失败（HTTP ${status}）。`
          throw new Error(message)
        }
        let currentUser: Response
        try { currentUser = await fetch('/api/auth/me', { credentials: 'include', cache: 'no-store' }) }
        catch { throw new Error('账户已创建，但登录状态暂时无法确认。请尝试登录确认结果。') }
        if (!currentUser.ok) throw new Error('账户已创建，但登录状态暂时无法确认。请尝试登录确认结果。')
        const sessionUser = await currentUser.json() as { username?: string }
        if (sessionUser.username !== normalizedUsername) throw new Error('账户已创建，但登录状态暂时无法确认。请尝试登录确认结果。')
        setInvitationCode(''); setPassword(''); setPasswordConfirm('')
        onLogin()
      } else {
        await api(setup ? '/api/auth/setup' : '/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })
        onLogin()
      }
    } catch (err) { setError(err instanceof Error ? err.message : registering ? '注册失败，请稍后再试。' : '登录失败') }
    finally { setSubmitting(false) }
  }
  if (setup === null) return <main className="gate"><div className="gate-card"><RefreshCw className="spin" /> 正在检查本地状态…</div></main>
  if (setup && setupMode === 'local_cli') return <main className="gate"><div className="gate-card"><div className="brand-mark">PO</div><p className="eyebrow">PRIVATE INITIALIZATION</p><h1>需要本机初始化管理员</h1><p className="muted">生产服务不开放网页注册。请在服务器本机终端运行：</p><pre className="cli-command">cd "&lt;PERSONAL_OS_ROOT&gt;\backend"{`\n`}.venv\Scripts\python.exe -m scripts.initialize_admin</pre><p className="muted">密码通过安全提示输入，不会显示或写入命令行参数。</p><div className="privacy-note"><ShieldCheck size={16} /> 单用户 · 私有部署</div></div></main>
  return <main className="gate"><div className="gate-card"><div className="brand-mark">PO</div><p className="eyebrow">PRIVATE OPERATIONS DESK</p><h1>{registering ? '使用邀请码注册' : setup ? '建立你的本地系统' : '欢迎回来'}</h1><p className="muted">{registering ? '邀请码由本机运维人员发放；注册信息仅提交给当前 Personal OS 服务。' : setup ? '首次初始化只在本机创建一个用户，不存在公开默认密码。' : '你的任务、学习和恢复记录留在自己的数据库里。'}</p><form onSubmit={submit}>
    {registering && <label>邀请码<input required minLength={20} maxLength={120} autoComplete="off" value={invitationCode} onChange={(e) => setInvitationCode(e.target.value)} /></label>}
    <label>用户名<input required minLength={3} maxLength={80} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} /></label>
    <label>密码<input required minLength={registering ? 12 : 10} type="password" autoComplete={registering ? 'new-password' : 'current-password'} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
    {registering && <label>确认密码<input required minLength={12} type="password" autoComplete="new-password" value={passwordConfirm} onChange={(e) => setPasswordConfirm(e.target.value)} /></label>}
    {error && <div className="error" role="alert">{error}</div>}
    <button className="primary wide" disabled={submitting}>{submitting ? '正在提交…' : registering ? '创建账户' : setup ? '创建本地账户' : '进入 Personal OS'} {!submitting && <ArrowUpRight size={16} />}</button>
  </form>
  {!setup && <button className="text-btn" type="button" disabled={submitting} onClick={() => { setRegistering((value) => !value); setError(''); setInvitationCode(''); setPassword(''); setPasswordConfirm('') }}>{registering ? '返回登录' : '邀请码注册'}</button>}
  <div className="privacy-note"><ShieldCheck size={16} /> 仅本地服务 · 无公开默认密码</div></div></main>
}

export function App() {
  const [logged, setLogged] = useState(false)
  const [view, setView] = useState('dashboard')
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [mobileNav, setMobileNav] = useState(false)
  const [taskTitle, setTaskTitle] = useState('')
  const [aiPrompt, setAiPrompt] = useState('')
  const [aiReply, setAiReply] = useState('')
  const [notice, setNotice] = useState('')
  const [proposals, setProposals] = useState<AgentProposal[]>([])
  const [recentChats, setRecentChats] = useState<ChatConversation[]>([])
  const [chatIntent, setChatIntent] = useState<{ domain: string; id: string | null; messageId: number | null }>({ domain: 'general', id: null, messageId: null })
  const [chatRefreshSignal, setChatRefreshSignal] = useState(0)
  const [taskIntentId, setTaskIntentId] = useState<number | null>(null)
  const [knowledgeDomain, setKnowledgeDomain] = useState('general')
  const [knowledgeIntent, setKnowledgeIntent] = useState<KnowledgeDraftIntent | null>(null)
  const [knowledgeCardId, setKnowledgeCardId] = useState<number | null>(null)
  const [appVersion, setAppVersion] = useState('')
  async function load() { try { const [d, t, p, c] = await Promise.all([api('/api/dashboard'), api('/api/tasks'), api('/api/agent/proposals'), api('/api/agent/chat/conversations?limit=5')]); setDashboard(d); setTasks(t); setProposals(p); setRecentChats(c.conversations || []) } catch { setLogged(false) } }
  function openChat(domain: string = 'general', id: string | null = null, messageId: number | null = null) { setChatIntent({ domain, id, messageId }); setView('chat'); setMobileNav(false) }
  useEffect(() => {
    const root = document.documentElement
    if (!logged) {
      try {
        const cached = JSON.parse(localStorage.getItem('personal-os-theme-cache') || '{}')
        root.dataset.theme = cached.theme === 'cyberpunk-night' ? 'cyberpunk-night' : 'classic'
        root.style.setProperty('--theme-background-darkness', `${Number.isInteger(cached.background_darkness) ? cached.background_darkness : 48}%`)
        root.style.setProperty('--theme-background-blur', `${Number.isInteger(cached.background_blur) ? cached.background_blur : 3}px`)
        root.style.setProperty('--theme-background-opacity', `${Number.isInteger(cached.background_opacity) ? cached.background_opacity : 48}%`)
      } catch { root.dataset.theme = 'classic' }
      root.style.removeProperty('--theme-background-art')
      return
    }
    void load()
    void api('/api/system/info').then((info) => setAppVersion(info.app_version || '')).catch(() => setAppVersion(''))
    void api('/api/account/appearance').then(applyAppearance).catch(() => { root.dataset.theme = 'classic' })
  }, [logged])
  async function addTask(e: React.FormEvent) { e.preventDefault(); if (!taskTitle.trim()) return; await api('/api/tasks', { method: 'POST', body: JSON.stringify({ title: taskTitle, module: 'system', acceptance_criteria: '保留可复核证据' }) }); setTaskTitle(''); setNotice('任务已写入数据库'); load() }
  async function askAI(e: React.FormEvent) { e.preventDefault(); setAiReply('正在请求…'); try { const data = await api('/api/ai/chat', { method: 'POST', body: JSON.stringify({ prompt: aiPrompt, purpose: 'assistant', consent_scope: 'user_prompt_only', consent_granted: true }) }); setAiReply(data.configured ? data.content : data.message) } catch (err) { setAiReply(err instanceof Error ? err.message : 'AI 请求失败') } }
  if (!logged) return <Login onLogin={() => setLogged(true)} />
  return <div className="shell">
    <aside className={mobileNav ? 'sidebar open' : 'sidebar'}><div className="sidebar-head"><div className="brand-mark small">PO</div><div><strong>Personal OS</strong><span>{appVersion ? `v${appVersion} · ` : ''}local first</span></div><button className="icon-btn close-mobile" onClick={() => setMobileNav(false)}><X size={19} /></button></div>
      <nav><button className={view === 'dashboard' ? 'nav-item active' : 'nav-item'} onClick={() => { setView('dashboard'); setMobileNav(false); void load() }}><LayoutDashboard size={18} /> 总览</button><button className={view === 'chat' ? 'nav-item active' : 'nav-item'} onClick={() => openChat()}><BrainCircuit size={18} /> AI 工作区</button><button className={view === 'tasks' ? 'nav-item active' : 'nav-item'} onClick={() => { setView('tasks'); setMobileNav(false) }}><ClipboardList size={18} /> 统一任务</button><button className={view === 'knowledge' ? 'nav-item active' : 'nav-item'} onClick={() => { setKnowledgeIntent(null); setKnowledgeCardId(null); setKnowledgeDomain('general'); setView('knowledge'); setMobileNav(false) }}><BookOpen size={18} /> 知识收集箱</button><p className="nav-label">七个工作面</p>{modules.map(({ key, label, icon: Icon, hint }) => <button key={key} className={view === key ? 'nav-item active' : 'nav-item'} onClick={() => { setView(key); setMobileNav(false) }}><Icon size={18} /><span>{label}</span><small>{hint}</small></button>)}</nav>
      <div className="sidebar-foot"><button className={view === 'settings' ? 'nav-item active' : 'nav-item'} onClick={() => { setView('settings'); setMobileNav(false) }}><Settings size={18} /> 设置</button><button className="nav-item" onClick={async () => { await api('/api/auth/logout', { method: 'POST' }); setLogged(false); setRecentChats([]) }}><LogOut size={18} /> 退出</button></div>
    </aside>
    <main className={view === 'chat' ? 'main chat-main' : 'main'}><header className="topbar"><button className="icon-btn menu-mobile" onClick={() => setMobileNav(true)}><Menu /></button><div><p className="eyebrow">{new Intl.DateTimeFormat('zh-CN', { dateStyle: 'full' }).format(new Date())}</p><h2>{view === 'dashboard' ? '今天，把重要的事变得可验证。' : view === 'settings' ? '系统设置' : view === 'chat' ? 'AI 对话工作区' : view === 'knowledge' ? '知识收集箱' : modules.find((m) => m.key === view)?.label || '统一任务'}</h2></div><div className="top-actions"><span className="status-dot"><span /> local database</span><button className="icon-btn" onClick={() => { if (view === 'chat') setChatRefreshSignal((value) => value + 1); else void load() }} aria-label="刷新"><RefreshCw size={18} /></button></div></header>
      {notice && <div className="notice">{notice}</div>}
      {view === 'dashboard' ? <DashboardView dashboard={dashboard} tasks={tasks} proposals={proposals} recentChats={recentChats} onTasks={() => { setTaskIntentId(null); setView('tasks') }} onOpenTask={(id) => { setTaskIntentId(id); setView('tasks') }} onReview={() => setView('reviews')} onChat={openChat} /> : view === 'chat' ? <ChatWorkspace key={`${chatIntent.domain}:${chatIntent.id || 'new'}`} initialDomain={chatIntent.domain as ChatDomain} initialConversationId={chatIntent.id} initialMessageId={chatIntent.messageId} refreshSignal={chatRefreshSignal} onProposalChange={() => void load()} onOpenTasks={() => setView('tasks')} onOpenTask={(id) => { setTaskIntentId(id); setView('tasks') }} onOpenKnowledge={(id, domain) => { setKnowledgeCardId(id); setKnowledgeIntent(null); setKnowledgeDomain(domain || 'general'); setView('knowledge') }} onSaveKnowledge={(draft) => { setKnowledgeIntent(draft); setKnowledgeDomain(draft.domain); setKnowledgeCardId(null); setView('knowledge') }} onBack={() => { setView('dashboard'); void load() }} /> : view === 'tasks' ? <TasksWorkspace initialTaskId={taskIntentId} onOpenConversation={openChat} onOpenReview={() => setView('reviews')} onOpenKnowledge={(id, domain) => { setKnowledgeCardId(id); setKnowledgeIntent(null); setKnowledgeDomain(domain || 'general'); setView('knowledge') }} /> : view === 'knowledge' ? <KnowledgeWorkspace key={`${knowledgeDomain}:${knowledgeCardId || 'new'}:${knowledgeIntent?.messageId || ''}`} initialDomain={knowledgeDomain} initialCardId={knowledgeCardId} draftFromMessage={knowledgeIntent} onOpenTask={(id) => { setTaskIntentId(id); setView('tasks') }} onReturnToChat={() => openChat(knowledgeIntent?.domain || 'general', knowledgeIntent?.conversationId || null, knowledgeIntent?.messageId || null)} onBack={() => { setView('dashboard'); void load() }} /> : view === 'reviews' ? <DailyReviewWorkspace onSaveKnowledge={(draft) => { setKnowledgeIntent(draft); setKnowledgeDomain(draft.domain); setKnowledgeCardId(null); setView('knowledge') }} /> : view === 'settings' ? <SettingsView request={api} onHistoryCleared={() => setRecentChats([])} /> : <ModuleView view={view} aiPrompt={aiPrompt} setAiPrompt={setAiPrompt} aiReply={aiReply} askAI={askAI} onOpenKnowledge={(domain) => { setKnowledgeIntent(null); setKnowledgeCardId(null); setKnowledgeDomain(domain); setView('knowledge') }} />}
    </main>
  </div>
}

function DashboardView({ dashboard, tasks, proposals, recentChats, onTasks, onOpenTask, onReview, onChat }: { dashboard: Dashboard | null; tasks: Task[]; proposals: AgentProposal[]; recentChats: ChatConversation[]; onTasks: () => void; onOpenTask: (id: number) => void; onReview: () => void; onChat: (domain: string, id?: string | null) => void }) {
  if (!dashboard) return <div className="loading"><RefreshCw className="spin" /> 正在读取数据库…</div>
  const pendingTasks = tasks.filter((task) => !['DONE', 'CANCELLED'].includes(task.status)).slice(0, 5)
  const pendingProposals = proposals.filter((proposal) => proposal.status === 'PENDING')
  const todayTasks = dashboard.today_tasks || [], completedToday = dashboard.completed_today_tasks || [], overdueTasks = dashboard.overdue_tasks || []
  return <div className="content dashboard-compact"><section className="dashboard-status-strip"><strong>{dashboard.today_date}</strong><span>今日任务 {dashboard.due_today_count} 项</span><span>今日完成 {dashboard.completed_today_count || 0} 项</span><span>已逾期 {dashboard.overdue_count} 项</span><button className="text-btn" onClick={onTasks}>AI 任务管理器 <ArrowUpRight size={14} /></button>{pendingProposals.length > 0 && <button className="text-btn" onClick={onTasks}>{pendingProposals.length} 项任务草案待处理</button>}</section>
    <section className="panel dashboard-today-tasks"><div className="panel-head"><div><p className="eyebrow">TODAY · {dashboard.today_date}</p><h3>今日任务</h3></div><button className="secondary" onClick={onReview}>每日结算</button></div>{todayTasks.map((task) => <button className="recent-chat-row" key={task.id} onClick={() => onOpenTask(task.id)}><span><strong>{task.title}</strong><small>{task.start_date}{task.due_date && task.due_date !== task.start_date ? ` → ${task.due_date}` : ''} · {task.module}</small></span><span className="tag">{task.status}</span></button>)}{completedToday.map((task) => <button className="recent-chat-row completed-today-row" key={`done-${task.id}`} onClick={() => onOpenTask(task.id)}><span><strong>{task.title}</strong><small>今日完成 · {task.module}</small></span><span className="tag">DONE</span></button>)}{todayTasks.length === 0 && completedToday.length === 0 && <div className="empty">今天没有排期任务。<button className="text-btn" onClick={onTasks}>创建或查看任务</button></div>}<div className="dashboard-task-footer"><span>{dashboard.unscheduled_count || 0} 项未排期</span><button className="text-btn" onClick={onTasks}>查看未排期与全部任务</button></div></section>
    {overdueTasks.length > 0 && <section className="panel dashboard-overdue"><div className="panel-head"><div><p className="eyebrow">OVERDUE</p><h3>逾期任务 · {dashboard.overdue_count}</h3></div><button className="text-btn" onClick={onTasks}>调整日期</button></div>{overdueTasks.slice(0, 5).map((task) => <button className="recent-chat-row" key={task.id} onClick={() => onOpenTask(task.id)}><span><strong>{task.title}</strong><small>截止 {task.due_date} · {task.module}</small></span><span className="tag">{task.status}</span></button>)}</section>}
    <section className="panel daily-review-card"><div><p className="eyebrow">DAILY REVIEW</p><h3>每日复盘</h3>{dashboard.today_review ? <p>{dashboard.today_review.actual_summary || '已保存结算记录'} · {dashboard.today_review.task_entries?.length || 0} 项任务记录</p> : <p className="muted">暂无可复盘记录</p>}</div><button className="secondary" onClick={onReview}>{dashboard.today_review ? '打开复盘' : '记录今日结算'} <ArrowUpRight size={15} /></button></section><section className="panel recent-chat-panel"><div className="panel-head"><div><p className="eyebrow">RECENT CONVERSATIONS</p><h3>最近对话</h3></div><button className="primary" onClick={() => onChat('general', null)}><Plus size={15} /> 新建聊天</button></div>{recentChats.slice(0, 5).map((conversation) => <button className="recent-chat-row" key={conversation.id} onClick={() => onChat(conversation.domain, conversation.id)}><span><strong>{conversation.title || '新会话'}</strong><small>{modules.find((item) => item.key === conversation.domain)?.label || '通用'} · {conversation.last_message_preview || '尚无消息'}</small></span><ArrowUpRight size={15} /></button>)}{recentChats.length === 0 && <div className="empty">还没有已保存对话。新建一段聊天即可开始。</div>}</section><section className="panel"><div className="panel-head"><div><p className="eyebrow">NEEDS ATTENTION</p><h3>待处理事项</h3></div><button className="text-btn" onClick={onTasks}>全部任务 <ArrowUpRight size={15} /></button></div>{pendingTasks.map((task) => <button key={task.id} className="recent-chat-row" onClick={() => onOpenTask(task.id)}><span><strong>{task.title}</strong><small>{task.module} · {task.status}</small></span><span className="tag">{task.status}</span></button>)}{pendingTasks.length === 0 && pendingProposals.length === 0 && <div className="empty">当前没有待处理项目。</div>}</section></div>
}

function TaskRow({ task }: { task: Task }) { return <div className="task-row"><div className={`task-status ${task.status.toLowerCase()}`}><CheckCircle2 size={15} /></div><div className="task-copy"><strong>{task.title}</strong><span>{task.module} · {task.evidence_count} 条证据</span></div><span className="tag">{task.status}</span></div> }
function TasksView({ tasks, taskTitle, setTaskTitle, addTask, reload }: { tasks: Task[]; taskTitle: string; setTaskTitle: (v: string) => void; addTask: (e: React.FormEvent) => void; reload: () => void }) { return <div className="content"><div className="section-intro"><div><p className="eyebrow">ONE TASK SYSTEM</p><h3>统一任务队列</h3><p className="muted">计划、执行、证据和验收集中在一个事实源。</p></div><button className="secondary" onClick={reload}><RefreshCw size={15} /> 刷新</button></div><form className="quick-add" onSubmit={addTask}><Plus size={18} /><input value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} placeholder="添加一个可验证的下一步…" /><button className="primary">加入队列</button></form><div className="panel task-list">{tasks.map((task) => <TaskRow key={task.id} task={task} />)}{tasks.length === 0 && <div className="empty">暂无任务。</div>}</div></div> }
function BaseSettingsView() {
  const [providers, setProviders] = useState<AIProvider[]>([])
  const [goals, setGoals] = useState<Goal[]>([])
  const [goalDomain, setGoalDomain] = useState('english')
  const [goalTitle, setGoalTitle] = useState('')
  const [goalMessage, setGoalMessage] = useState('')
  const [selected, setSelected] = useState('deepseek')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('deepseek-flash')
  const [message, setMessage] = useState('')
  const [messageKind, setMessageKind] = useState<'success' | 'error' | 'info'>('info')
  const [searchStatus, setSearchStatus] = useState<{ configured: boolean; enabled: boolean; status: string }>({ configured: false, enabled: false, status: 'not_configured' })
  const [searchKey, setSearchKey] = useState('')
  const [searchMessage, setSearchMessage] = useState('')
  async function loadProviders() { try { const [data, goalData, searchData] = await Promise.all([api('/api/settings/ai/providers'), api('/api/agent/goals'), api('/api/settings/search')]); setProviders(data); setGoals(goalData); setSearchStatus(searchData); const current = data.find((item: AIProvider) => item.provider === selected); if (current) setModel(current.model) } catch (err) { setMessage(err instanceof Error ? err.message : '设置读取失败') } }
  useEffect(() => { loadProviders() }, [])
  const current = providers.find((item) => item.provider === selected)
  function providerError(err: unknown, fallback: string) { const text = err instanceof Error ? err.message : fallback; if (/credential master key|credential service|master key is not configured/i.test(text)) return `后端凭证加密服务未初始化：${text}`; return text }
  async function save(e: React.FormEvent) { e.preventDefault(); try { const data = await api(`/api/settings/ai/providers/${selected}`, { method: 'PUT', body: JSON.stringify({ ...(apiKey ? { api_key: apiKey } : {}), model, enabled: true }) }); setApiKey(''); setMessage(data.configured ? `${data.provider} 凭证已保存并加密。` : '保存失败：后端未确认凭证已配置。'); setMessageKind(data.configured ? 'success' : 'error'); await loadProviders() } catch (err) { setMessage(providerError(err, '保存失败')); setMessageKind('error') } }
  async function test() { setMessage('正在进行一次最小额度模型连接测试…'); setMessageKind('info'); try { const data = await api(`/api/settings/ai/providers/${selected}/test`, { method: 'POST' }); const usage = data.usage || {}; setMessage(data.ok ? `${data.detail} · Token ${usage.prompt_tokens || 0} 输入 / ${usage.completion_tokens || 0} 输出` : `${data.status}: ${data.detail}`); setMessageKind(data.ok ? 'success' : 'error'); await loadProviders() } catch (err) { setMessage(providerError(err, '连接测试失败')); setMessageKind('error') } }
  async function remove() { try { await api(`/api/settings/ai/providers/${selected}/credential`, { method: 'DELETE' }); setApiKey(''); setMessage('凭证已删除；后续调用不会继续使用它。'); setMessageKind('success'); await loadProviders() } catch (err) { setMessage(err instanceof Error ? err.message : '删除失败'); setMessageKind('error') } }
  async function addGoal(e: React.FormEvent) { e.preventDefault(); if (!goalTitle.trim()) return; try { await api('/api/agent/goals', { method: 'POST', body: JSON.stringify({ domain: goalDomain, title: goalTitle, priority: 2 }) }); setGoalTitle(''); setGoalMessage('目标已保存，下一次计划生成会读取它。'); loadProviders() } catch (err) { setGoalMessage(err instanceof Error ? err.message : '目标保存失败') } }
  async function saveSearch(e: React.FormEvent) { e.preventDefault(); try { const result = await api('/api/settings/search/tavily', { method: 'PUT', body: JSON.stringify({ api_key: searchKey }) }); setSearchKey(''); setSearchStatus(result); setSearchMessage('Tavily 凭证已加密保存；搜索需在 Agent 对话中单独授权。') } catch (err) { setSearchMessage(err instanceof Error ? err.message : '保存搜索凭证失败') } }
  async function deleteSearch() { try { const result = await api('/api/settings/search/tavily', { method: 'DELETE' }); setSearchStatus(result); setSearchMessage('Tavily 凭证已删除。') } catch (err) { setSearchMessage(err instanceof Error ? err.message : '删除凭证失败') } }
  return <div className="content"><div className="section-intro"><div><p className="eyebrow">CONTROL ROOM / AI</p><h3>模型 Provider</h3><p className="muted">凭证只在后端加密保存，前端只能看到配置状态。</p></div></div><div className="settings-grid"><div className="panel settings-nav"><p className="eyebrow">PROVIDERS</p>{['deepseek', 'glm', 'custom'].map((name) => <button key={name} className={selected === name ? 'settings-tab active' : 'settings-tab'} onClick={() => { setSelected(name); setApiKey(''); const item = providers.find((provider) => provider.provider === name); if (item) setModel(item.model) }}>{name === 'deepseek' ? 'DeepSeek' : name === 'glm' ? 'GLM / 智谱' : '自定义兼容接口'}<span>{providers.find((provider) => provider.provider === name)?.configured ? '已配置' : '未配置'}</span></button>)}</div><div className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">{selected.toUpperCase()}</p><h3>{selected === 'deepseek' ? 'DeepSeek' : selected === 'glm' ? 'GLM / 智谱' : 'OpenAI-compatible'}</h3></div><span className="tag">{current?.status || 'not_configured'}</span></div><form onSubmit={save} className="settings-form"><label>API Key<input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={current?.configured ? '已保存，输入新值可替换' : '仅提交到后端加密存储'} autoComplete="new-password" /></label><label>模型名称<input value={model} onChange={(e) => setModel(e.target.value)} placeholder="例如 deepseek-flash" /></label><label>Base URL<input value={current?.base_url || ''} readOnly /></label><div className="settings-actions"><button className="primary">保存并启用</button><button className="secondary" type="button" onClick={test}>测试连接</button><button className="text-btn danger-btn" type="button" onClick={remove}>删除凭证</button></div></form>{message && <div className={`notice inline-notice ${messageKind === 'error' ? 'error-notice' : messageKind === 'success' ? 'success-notice' : 'info-notice'}`}>{message}</div>}<p className="settings-meta">来源：{current?.credential_source || 'none'} · Token：{current?.total_prompt_tokens || 0} 输入 / {current?.total_completion_tokens || 0} 输出</p></div></div><div className="panel settings-note"><ShieldCheck size={18} /><div><strong>隐私边界</strong><p className="muted">健康与财务数据不会自动发送给模型。没有配置凭证时，任务和记录功能仍可正常使用。</p></div></div><div className="panel goals-panel"><div className="panel-head"><div><p className="eyebrow">PERSONAL PRIORITIES</p><h3>AI 规划目标</h3><p className="muted">目标由你手动维护，不会自动导入旧 Markdown。</p></div><Target size={20} /></div><div className="goal-list">{goals.map((goal) => <div className="goal-row" key={goal.id}><span className="tag">P{goal.priority}</span><strong>{goal.domain}</strong><span>{goal.title}</span></div>)}{goals.length === 0 && <div className="empty">尚未建立目标；缺少目标时模型会明确保持 UNKNOWN。</div>}</div><form className="goal-form" onSubmit={addGoal}><input value={goalDomain} onChange={(e) => setGoalDomain(e.target.value)} placeholder="领域，如 english" /><input value={goalTitle} onChange={(e) => setGoalTitle(e.target.value)} placeholder="当前目标" /><button className="primary">保存目标</button></form>{goalMessage && <div className="notice inline-notice">{goalMessage}</div>}</div></div>
}
function SettingsView({ request, onHistoryCleared }: { request: typeof api; onHistoryCleared: () => void }) {
  const [section, setSection] = useState('account')
  const sections = [['account', '账号与安全'], ['appearance', '外观与主题'], ['ai', 'AI 模型与 API Key'], ['usage', 'AI 用量与成本'], ['search', '联网搜索'], ['privacy', '对话与隐私'], ['data', '数据与备份'], ['agent', 'Agent 运行记录'], ['system', '版本与工程记录']]
  return <div className="content settings-page"><div className="settings-categories" role="tablist" aria-label="设置分类">{sections.map(([key, label]) => <button key={key} role="tab" aria-selected={section === key} className={section === key ? 'settings-tab active' : 'settings-tab'} onClick={() => setSection(key)}>{label}</button>)}</div><div className="settings-section">{section === 'account' ? <AccountSettingsPanel request={request} /> : section === 'appearance' ? <AppearanceSettingsPanel request={request} /> : section === 'ai' ? <BaseSettingsView /> : section === 'usage' ? <AIUsagePanel request={request} /> : section === 'search' ? <SearchSettingsPanel /> : section === 'privacy' ? <PrivacySettingsPanel request={request} onHistoryCleared={onHistoryCleared} /> : section === 'data' ? <DataBackupPanel request={request} /> : section === 'agent' ? <AgentAuditPanel request={request} /> : <SystemInfoPanel request={request} />}</div></div>
}

function SearchSettingsPanel() {
  const [state, setState] = useState({ configured: false, enabled: false, status: 'not_configured' })
  const [key, setKey] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => { api('/api/settings/search').then(setState).catch((err) => setMessage(err instanceof Error ? err.message : '读取失败')) }, [])
  async function save(e: React.FormEvent) { e.preventDefault(); try { const result = await api('/api/settings/search/tavily', { method: 'PUT', body: JSON.stringify({ api_key: key }) }); setState(result); setKey(''); setMessage('已加密保存。Agent 只有在勾选联网授权后才会调用搜索。') } catch (err) { setMessage(err instanceof Error ? err.message : '保存失败') } }
  async function remove() { try { const result = await api('/api/settings/search/tavily', { method: 'DELETE' }); setState(result); setKey(''); setMessage('凭证已删除。') } catch (err) { setMessage(err instanceof Error ? err.message : '删除失败') } }
  return <section className="panel settings-card search-settings"><div className="panel-head"><div><p className="eyebrow">SEARCH PROVIDER</p><h3>Tavily Web Search</h3><p className="muted">与语言模型凭证分离；密钥只在后端 Fernet 加密保存。</p></div><span className="tag">{state.status}</span></div><form className="settings-form" onSubmit={save}><label>Tavily API Key<input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={state.configured ? '已配置；输入新值可替换' : '粘贴 Tavily Key'} autoComplete="new-password" required /></label><div className="settings-actions"><button className="primary">保存并启用搜索</button>{state.configured && <button className="text-btn danger-btn" type="button" onClick={remove}>删除搜索凭证</button>}</div></form>{message && <div className="notice inline-notice info-notice">{message}</div>}<p className="settings-meta">搜索由用户主动授权触发；单次最多 5 条。页面抓取仅允许公网 HTTPS，并限制重定向与响应大小。</p></section>
}

function ModuleView({ view, aiPrompt, setAiPrompt, aiReply, askAI, onOpenKnowledge }: { view: string; aiPrompt: string; setAiPrompt: (v: string) => void; aiReply: string; askAI: (e: React.FormEvent) => void; onOpenKnowledge: (domain: string) => void }) { const selected = modules.find((m) => m.key === view) ?? modules[0]; const ModuleIcon = selected.icon; return <div className="content"><div className="module-hero"><div className="module-icon"><ModuleIcon size={25} /></div><div><p className="eyebrow">MODULE / {view.toUpperCase()}</p><h3>{selected.label}</h3><p>{selected.hint} · 基础数据界面正在构建中</p><button className="secondary" onClick={() => onOpenKnowledge(view)}>打开该领域知识收集箱</button></div></div><div className="coming panel"><div className="coming-number">0{modules.findIndex((m) => m.key === view) + 1}</div><div><h3>真实数据优先</h3><p>此模块入口已建立，后续功能会直接连接 SQLite，不使用虚构历史数据。</p><span className="tag">基础界面 · 待开发</span></div></div><div className="panel assistant-panel"><div className="panel-head"><div><p className="eyebrow">AUTHORIZED AI</p><h3>给这个模块一个问题</h3></div><BrainCircuit size={20} /></div><form onSubmit={askAI}><textarea value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)} placeholder="只会把你明确提交的这段文字发送给 AI" rows={4} /><label className="consent"><input type="checkbox" required /> 我确认只授权发送这段文字</label><button className="primary">请求 DeepSeek <Send size={15} /></button></form>{aiReply && <div className="ai-reply">{aiReply}</div>}</div></div> }

const root = document.getElementById('root')
if (root) createRoot(root).render(<React.StrictMode><App /></React.StrictMode>)
