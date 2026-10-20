import { useEffect, useRef, useState } from 'react'
import { BookOpen, Check, RefreshCw } from 'lucide-react'
import type { KnowledgeDraftIntent } from './KnowledgeWorkspace'

type Task = { id: number; title: string; status: string; due_date?: string | null; completed_at?: string | null }
type Entry = { task_id: number; report_category: 'completed_today' | 'in_progress' | 'blocked' | 'overdue'; actual_progress: string; evidence_note: string }
type SavedReview = { review_date: string; planned_summary?: string | null; actual_summary?: string | null; blockers?: string | null; next_actions?: string | null; task_entries?: Array<{ task_id: number; task_title: string; status_at_settlement: string; report_category: string; actual_progress?: string | null; evidence_note?: string | null }> }
type WeeklyReview = { week_start: string; week_end: string; days_with_reviews: number; task_entry_count: number; reported_categories: Record<string, number>; evidence_note_count: number; days: Array<{ review_date: string; reviewed: boolean; actual_summary?: string | null; blockers?: string | null; next_actions?: string | null; task_entries: NonNullable<SavedReview['task_entries']> }> }

function mondayOf(date: string) {
  const day = new Date(`${date}T12:00:00`)
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7))
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`
}

async function api(path: string, options: RequestInit = {}) {
  const csrf = document.cookie.split('; ').find((item) => item.startsWith('personal_os_csrf='))?.split('=')[1]
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  const response = await fetch(path, { ...options, headers, credentials: 'include' })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail?.message || body?.detail || `请求失败 (${response.status})`)
  return body
}

export function DailyReviewWorkspace({ onSaveKnowledge }: { onSaveKnowledge?: (draft: KnowledgeDraftIntent) => void } = {}) {
  const [section, setSection] = useState<'daily' | 'weekly'>('daily')
  const [today, setToday] = useState('')
  const [weekStart, setWeekStart] = useState('')
  const [weekly, setWeekly] = useState<WeeklyReview | null>(null)
  const [weeklyLoading, setWeeklyLoading] = useState(false)
  const [tasks, setTasks] = useState<Task[]>([])
  const [entries, setEntries] = useState<Entry[]>([])
  const [planned, setPlanned] = useState('')
  const [actual, setActual] = useState('')
  const [blockers, setBlockers] = useState('')
  const [next, setNext] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [savedReview, setSavedReview] = useState<SavedReview | null>(null)
  const readSequence = useRef(0)
  const dirtyFields = useRef(new Set<string>())
  const saving = useRef(false)
  const [isSaving, setIsSaving] = useState(false)

  async function load() {
    if (saving.current) return
    const sequence = ++readSequence.current
    if (!today) setLoading(true)
    try {
      const dashboard = await api('/api/dashboard')
      if (sequence !== readSequence.current) return
      const date = dashboard.today_date as string
      setToday(date)
      setWeekStart((current) => current || mondayOf(date))
      const [taskList, review] = await Promise.all([api('/api/tasks'), api(`/api/reviews/daily/${date}`)])
      if (sequence !== readSequence.current) return
      const active = (taskList as Task[]).filter((task) => !['CANCELLED'].includes(task.status) && (task.status !== 'DONE' || task.completed_at?.slice(0, 10) === date))
      setTasks(active)
      if (!dirtyFields.current.has('planned')) setPlanned(review.planned_summary || '')
      if (!dirtyFields.current.has('actual')) setActual(review.actual_summary || '')
      if (!dirtyFields.current.has('blockers')) setBlockers(review.blockers || '')
      if (!dirtyFields.current.has('next')) setNext(review.next_actions || '')
      if (!dirtyFields.current.has('entries')) setEntries((review.task_entries || []).map((item: any) => ({ task_id: item.task_id, report_category: item.report_category, actual_progress: item.actual_progress || '', evidence_note: item.evidence_note || '' })))
      setSavedReview(review)
      setError(false)
    } catch (cause) {
      if (sequence === readSequence.current) { setMessage(cause instanceof Error ? cause.message : '复盘读取失败'); setError(true) }
    } finally {
      if (sequence === readSequence.current) setLoading(false)
    }
  }
  useEffect(() => {
    void load()
    return () => { readSequence.current += 1 }
  }, [])
  async function loadWeekly(start = weekStart) {
    if (!start) return
    setWeeklyLoading(true)
    setMessage('')
    try {
      setWeekly(await api(`/api/reviews/weekly?week_start=${encodeURIComponent(start)}`))
      setError(false)
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : '周回顾读取失败'); setError(true) }
    finally { setWeeklyLoading(false) }
  }
  useEffect(() => { if (section === 'weekly' && weekStart) void loadWeekly(weekStart) }, [section, weekStart])
  function addTask(taskId: number) {
    if (entries.some((entry) => entry.task_id === taskId)) return
    dirtyFields.current.add('entries')
    const task = tasks.find((item) => item.id === taskId)
    const overdue = Boolean(task?.due_date && task.due_date <= today)
    setEntries((previous) => [...previous, { task_id: taskId, report_category: overdue ? 'overdue' : 'in_progress', actual_progress: '', evidence_note: '' }])
  }
  function updateEntry(index: number, patch: Partial<Entry>) { dirtyFields.current.add('entries'); setEntries((previous) => previous.map((entry, i) => i === index ? { ...entry, ...patch } : entry)) }
  async function save(event: React.FormEvent) {
    event.preventDefault(); setMessage('')
    if (saving.current) return
    saving.current = true
    setIsSaving(true)
    const saveSequence = ++readSequence.current
    try {
      const result = await api('/api/reviews/daily', { method: 'POST', body: JSON.stringify({ review_date: today, planned_summary: planned || null, actual_summary: actual || null, blockers: blockers || null, next_actions: next || null, task_entries: entries }) })
      if (saveSequence !== readSequence.current) return
      setSavedReview(result)
      dirtyFields.current.clear()
      setMessage(`已保存 ${result.review_date} 的复盘。任务状态未被自动更改。`); setError(false)
    } catch (cause) {
      if (saveSequence === readSequence.current) { setMessage(cause instanceof Error ? cause.message : '复盘保存失败'); setError(true) }
    } finally {
      saving.current = false
      if (saveSequence === readSequence.current) setIsSaving(false)
    }
  }
  if (loading) return <div className="loading"><RefreshCw className="spin" /> 正在读取真实任务与结算记录…</div>
  function saveAsKnowledgeDraft() {
    if (!savedReview || !onSaveKnowledge) return
    const parts = [`# ${savedReview.review_date} 每日复盘`]
    if (savedReview.actual_summary) parts.push(`## 实际完成\n${savedReview.actual_summary}`)
    if (savedReview.blockers) parts.push(`## 阻碍\n${savedReview.blockers}`)
    if (savedReview.next_actions) parts.push(`## 下一步\n${savedReview.next_actions}`)
    for (const entry of savedReview.task_entries || []) {
      const details = [entry.actual_progress && `进度：${entry.actual_progress}`, entry.evidence_note && `自报证据：${entry.evidence_note}`].filter(Boolean)
      if (details.length) parts.push(`## ${entry.task_title}（任务 #${entry.task_id}）\n${details.join('\n')}`)
    }
    onSaveKnowledge({ content: parts.join('\n\n'), domain: 'reviews' })
  }
  if (section === 'weekly') return <div className="content daily-review-workspace"><section className="panel"><div className="panel-head"><div><p className="eyebrow">WEEKLY REVIEW</p><h3>每周执行总结</h3><p className="muted">只汇总本周已保存的每日复盘和任务快照；进度分类由你填写，不代表系统独立验证。</p></div><button className="secondary" onClick={() => void loadWeekly()}>刷新</button></div><div className="daily-review-task-head"><label>周一<input aria-label="周起始日期" type="date" value={weekStart} onChange={(event) => setWeekStart(event.target.value)} /></label><button className="secondary" onClick={() => setSection('daily')}>返回每日复盘</button></div>{weeklyLoading ? <div className="loading"><RefreshCw className="spin" /> 正在汇总本周记录…</div> : weekly && <><div className="panel-head"><strong>{weekly.week_start} — {weekly.week_end}</strong><span>{weekly.days_with_reviews}/7 天有复盘 · {weekly.task_entry_count} 项任务记录 · {weekly.evidence_note_count} 条自报证据</span></div><div className="task-form-grid"><p>有进展：{weekly.reported_categories.completed_today || 0}</p><p>进行中：{weekly.reported_categories.in_progress || 0}</p><p>受阻：{weekly.reported_categories.blocked || 0}</p><p>逾期：{weekly.reported_categories.overdue || 0}</p></div>{weekly.days.map((day) => <article className="daily-review-task" key={day.review_date}><strong>{day.review_date}{day.reviewed ? '' : ' · 暂无复盘'}</strong>{day.actual_summary && <p>{day.actual_summary}</p>}{day.blockers && <p><b>阻碍：</b>{day.blockers}</p>}{day.next_actions && <p><b>下一步：</b>{day.next_actions}</p>}{day.task_entries.map((entry) => <small key={entry.task_id}>任务 #{entry.task_id} {entry.task_title} · {entry.report_category}{entry.evidence_note ? ` · ${entry.evidence_note}` : ''}</small>)}</article>)}</>}{message && <div className={error ? 'notice error-notice' : 'notice'}>{message}</div>}</section></div>
  return <div className="content daily-review-workspace"><section className="panel"><div className="panel-head"><div><p className="eyebrow">DAILY SETTLEMENT · {today}</p><h3>每日执行结算</h3><p className="muted">任务状态快照来自数据库；完成情况和证据由你填写。结算不会自动改变任务状态。</p></div><button className="secondary" onClick={() => void load()}>刷新</button></div>
    <div className="daily-review-task-head"><button className="secondary" onClick={() => setSection('weekly')}>查看每周总结</button>{savedReview && onSaveKnowledge && <button className="secondary" onClick={saveAsKnowledgeDraft}><BookOpen size={14} /> 从已保存复盘带入知识草稿</button>}</div>
    {savedReview && onSaveKnowledge && <p className="muted">草稿来自最近保存的复盘；进入知识工作区后可编辑，只有在那里点击保存才会创建知识卡。</p>}
    <form className="daily-review-form" onSubmit={save}><fieldset disabled={isSaving} style={{ display: 'contents' }}><label>今日原计划（可选）<textarea rows={2} value={planned} onChange={(event) => { dirtyFields.current.add('planned'); setPlanned(event.target.value) }} /></label><label>实际完成了什么<textarea rows={3} value={actual} onChange={(event) => { dirtyFields.current.add('actual'); setActual(event.target.value) }} placeholder="只记录真实执行，不确定的内容留空" /></label>
      <div className="daily-review-task-head"><h4>任务实际进度与证据</h4><select aria-label="添加任务到结算" value="" onChange={(event) => { if (event.target.value) addTask(Number(event.target.value)) }}><option value="">添加今日任务…</option>{tasks.filter((task) => !entries.some((entry) => entry.task_id === task.id)).map((task) => <option key={task.id} value={task.id}>{task.title} · {task.status}</option>)}</select></div>
      {entries.map((entry, index) => { const task = tasks.find((item) => item.id === entry.task_id); return <article className="daily-review-task" key={entry.task_id}><strong>{task?.title || `任务 #${entry.task_id}`}</strong><small>数据库状态：{task?.status || 'UNKNOWN'} · 结算不会自动标记 DONE</small><div className="task-form-grid"><label>今日归类<select value={entry.report_category} onChange={(event) => updateEntry(index, { report_category: event.target.value as Entry['report_category'] })}><option value="completed_today">今日有完成进展</option><option value="in_progress">仍在执行</option><option value="blocked">受阻</option><option value="overdue" disabled={!task?.due_date || task.due_date > today}>到期未完成</option></select></label><label>实际进度<input value={entry.actual_progress} onChange={(event) => updateEntry(index, { actual_progress: event.target.value })} /></label></div><label>执行证据（自报）<textarea rows={2} value={entry.evidence_note} onChange={(event) => updateEntry(index, { evidence_note: event.target.value })} /></label><button type="button" className="text-btn danger-btn" onClick={() => { dirtyFields.current.add('entries'); setEntries((previous) => previous.filter((_, i) => i !== index)) }}>从本次结算移除</button></article> })}
      {entries.length === 0 && <p className="empty">尚未选择任务；这里不会自动推断任务已完成。</p>}
      <label>阻碍<textarea rows={2} value={blockers} onChange={(event) => { dirtyFields.current.add('blockers'); setBlockers(event.target.value) }} /></label><label>次日调整建议<textarea rows={2} value={next} onChange={(event) => { dirtyFields.current.add('next'); setNext(event.target.value) }} /></label><button className="primary"><Check size={15} /> 保存 / 更新本日结算</button></fieldset>
    </form>{message && <div className={error ? 'notice error-notice' : 'notice'}>{message}</div>}</section></div>
}
