import { useEffect, useState } from 'react'
import { Download, Eye, EyeOff, FileCheck2, HardDriveDownload, ImagePlus, KeyRound, RotateCcw, ShieldCheck, UserRound } from 'lucide-react'
import { EngineeringJournal } from './EngineeringJournal'

type Request = (path: string, options?: RequestInit) => Promise<any>

export type Appearance = { theme: 'classic' | 'cyberpunk-night'; has_background: boolean; background_url?: string | null; background_darkness: number; background_blur: number; background_opacity: number }

export function applyAppearance(value: Appearance) {
  const root = document.documentElement
  root.dataset.theme = value.theme
  root.style.setProperty('--theme-background-art', value.background_url ? `url("${value.background_url}")` : 'none')
  root.style.setProperty('--theme-background-darkness', `${value.background_darkness}%`)
  root.style.setProperty('--theme-background-blur', `${value.background_blur}px`)
  root.style.setProperty('--theme-background-opacity', `${value.background_opacity}%`)
  try { localStorage.setItem('personal-os-theme-cache', JSON.stringify({ theme: value.theme, background_darkness: value.background_darkness, background_blur: value.background_blur, background_opacity: value.background_opacity })) } catch { /* Theme still applies for this page even if storage is unavailable. */ }
}

export function AppearanceSettingsPanel({ request }: { request: Request }) {
  const [appearance, setAppearance] = useState<Appearance | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState(false)
  useEffect(() => { void request('/api/account/appearance').then((value) => { setAppearance(value); applyAppearance(value) }).catch((err) => { setError(true); setMessage(err instanceof Error ? err.message : '外观设置读取失败') }) }, [])
  async function save(event?: React.FormEvent) {
    event?.preventDefault()
    if (!appearance) return
    setBusy(true)
    try {
      const value = await request('/api/account/appearance', { method: 'PATCH', body: JSON.stringify({ theme: appearance.theme, background_darkness: appearance.background_darkness, background_blur: appearance.background_blur, background_opacity: appearance.background_opacity }) })
      setAppearance(value); applyAppearance(value); setMessage('主题偏好已保存，会在刷新和重新登录后保留。'); setError(false)
    } catch (err) { setMessage(err instanceof Error ? err.message : '主题保存失败'); setError(true) } finally { setBusy(false) }
  }
  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file || !appearance) return
    if (file.size > 5 * 1024 * 1024) { setMessage('图片不能超过 5 MB。'); setError(true); event.target.value = ''; return }
    const body = new FormData(); body.append('background', file)
    setBusy(true)
    try { const value = await request('/api/account/appearance/background', { method: 'PUT', body }); setAppearance(value); applyAppearance(value); setMessage('背景图已安全保存到你的私有空间。'); setError(false) }
    catch (err) { setMessage(err instanceof Error ? err.message : '背景上传失败'); setError(true) }
    finally { setBusy(false); event.target.value = '' }
  }
  async function removeBackground() {
    setBusy(true)
    try { const value = await request('/api/account/appearance/background', { method: 'DELETE' }); setAppearance(value); applyAppearance(value); setMessage('已恢复为内置夜城背景。'); setError(false) }
    catch (err) { setMessage(err instanceof Error ? err.message : '恢复默认背景失败'); setError(true) }
    finally { setBusy(false) }
  }
  if (!appearance) return <section className="panel settings-card"><h3>外观与主题</h3><p className={error ? 'error' : 'muted'}>{message || '正在读取主题偏好…'}</p></section>
  return <section className="panel settings-card appearance-settings"><div className="panel-head"><div><p className="eyebrow">PERSONAL SPACE / APPEARANCE</p><h3>外观与主题</h3><p className="muted">主题偏好仅作用于当前账号；Classic 保留原有深绿与米白风格。</p></div></div>
    <div className="appearance-options" role="radiogroup" aria-label="选择主题">
      <button type="button" role="radio" aria-checked={appearance.theme === 'classic'} className={appearance.theme === 'classic' ? 'appearance-option selected' : 'appearance-option'} onClick={() => setAppearance({ ...appearance, theme: 'classic' })}><span className="theme-swatch classic-swatch" /><strong>Classic</strong><small>深绿 · 米白 · 暖金</small></button>
      <button type="button" role="radio" aria-checked={appearance.theme === 'cyberpunk-night'} className={appearance.theme === 'cyberpunk-night' ? 'appearance-option selected' : 'appearance-option'} onClick={() => setAppearance({ ...appearance, theme: 'cyberpunk-night' })}><span className="theme-swatch cyber-swatch" /><strong>Cyberpunk Night</strong><small>夜城控制台 · 原创视觉</small></button>
    </div>
    {appearance.theme === 'cyberpunk-night' && <div className="appearance-controls"><div><p className="muted">夜城背景效果。只支持静态 PNG、JPEG、WebP；图片私有存储，不会作为公共静态文件提供。</p><label className="background-upload"><ImagePlus size={17} />{appearance.has_background ? '替换自定义背景' : '上传自定义背景'}<input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void upload(event)} disabled={busy} /></label>{appearance.has_background && <button className="secondary" type="button" onClick={() => void removeBackground()} disabled={busy}><RotateCcw size={15} /> 恢复内置背景</button>}<small>最大 5 MB、24 MP；不支持 SVG、动画图片或其他格式。</small></div>
      <label>背景暗化 <span>{appearance.background_darkness}%</span><input type="range" min="0" max="90" step="1" value={appearance.background_darkness} onChange={(event) => setAppearance({ ...appearance, background_darkness: Number(event.target.value) })} /></label>
      <label>背景模糊 <span>{appearance.background_blur}px</span><input type="range" min="0" max="20" step="1" value={appearance.background_blur} onChange={(event) => setAppearance({ ...appearance, background_blur: Number(event.target.value) })} /></label>
      <label>背景强度 <span>{appearance.background_opacity}%</span><input type="range" min="0" max="70" step="1" value={appearance.background_opacity} onChange={(event) => setAppearance({ ...appearance, background_opacity: Number(event.target.value) })} /></label>
    </div>}
    <div className="settings-actions"><button className="primary" disabled={busy} onClick={() => void save()}>应用并保存</button><span className="muted">背景效果只在 Cyberpunk Night 下显示。</span></div>
    {message && <div role="status" className={`notice inline-notice ${error ? 'error-notice' : 'success-notice'}`}>{message}</div>}
  </section>
}

export function AccountSettingsPanel({ request }: { request: Request }) {
  const [profile, setProfile] = useState<any>(null)
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [visible, setVisible] = useState(false)
  const [revoke, setRevoke] = useState(true)
  const [message, setMessage] = useState('')
  const [error, setError] = useState(false)
  async function load() { const value = await request('/api/account/profile'); setProfile(value); setUsername(value.username); setDisplayName(value.display_name || '') }
  useEffect(() => { void load().catch((err) => { setMessage(err instanceof Error ? err.message : '账户信息读取失败'); setError(true) }) }, [])
  async function saveProfile(event: React.FormEvent) { event.preventDefault(); try { const value = await request('/api/account/profile', { method: 'PATCH', body: JSON.stringify({ username, display_name: displayName || null }) }); setProfile({ ...profile, ...value }); setMessage('账户资料已更新；账号 ID 保持不变。'); setError(false) } catch (err) { setMessage(err instanceof Error ? err.message : '账户资料更新失败'); setError(true) } }
  async function changePassword(event: React.FormEvent) { event.preventDefault(); if (newPassword !== confirmPassword) { setMessage('两次输入的新密码不一致。'); setError(true); return }; try { const value = await request('/api/account/password', { method: 'POST', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword, revoke_other_sessions: revoke }) }); setCurrentPassword(''); setNewPassword(''); setConfirmPassword(''); setMessage(`密码已更新；已撤销其他设备会话 ${value.other_sessions_revoked} 个，当前设备保持登录。`); setError(false); await load() } catch (err) { setMessage(err instanceof Error ? err.message : '密码更新失败'); setError(true) } }
  const classes = [/[a-z]/.test(newPassword), /[A-Z]/.test(newPassword), /\d/.test(newPassword), /[^A-Za-z0-9\s]/.test(newPassword)].filter(Boolean).length
  const strongEnough = newPassword.length >= 12 && classes >= 3
  return <div className="settings-panels">
    <section className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">ACCOUNT ID · {profile?.id ?? '…'}</p><h3><UserRound size={18} /> 账号资料</h3><p className="muted">单用户私有系统；修改用户名不会更改账号 ID。</p></div></div><form className="settings-form" onSubmit={saveProfile}><label>登录用户名<input autoComplete="username" minLength={3} maxLength={80} value={username} onChange={(e) => setUsername(e.target.value)} required /></label><label>显示名称<input maxLength={120} value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="可选" /></label><button className="primary">保存账号资料</button></form><div className="settings-meta">创建时间：{profile?.created_at || 'UNKNOWN'} · 最近登录：{profile?.last_login_at || 'UNKNOWN'} · 当前会话：{profile?.current_session_created_at || 'UNKNOWN'} · 活跃会话：{profile?.active_sessions ?? 'UNKNOWN'}</div></section>
    <section className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">PASSWORD & SESSIONS</p><h3><KeyRound size={18} /> 修改密码</h3></div><ShieldCheck size={20} /></div><form className="settings-form" onSubmit={changePassword}><label>当前密码<input type="password" autoComplete="current-password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} required /></label><label>新密码<div className="password-input"><input type={visible ? 'text' : 'password'} autoComplete="new-password" minLength={12} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required /><button type="button" className="icon-btn" aria-label={visible ? '隐藏密码' : '显示密码'} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>{newPassword && <p className={strongEnough ? 'password-strength good' : 'password-strength'}>{strongEnough ? '强度符合要求' : `至少 12 个字符，并使用 4 类字符中的至少 3 类（当前 ${classes}/4）`}</p>}<label>确认新密码<input type={visible ? 'text' : 'password'} autoComplete="new-password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required /></label><label className="consent"><input type="checkbox" checked={revoke} onChange={(e) => setRevoke(e.target.checked)} /> 修改后撤销其他设备的会话（建议）</label><button className="primary" disabled={!strongEnough || newPassword !== confirmPassword}>验证旧密码并更新</button></form></section>
    {message && <div className={`notice inline-notice ${error ? 'error-notice' : 'success-notice'}`}>{message}</div>}
  </div>
}

export function PrivacySettingsPanel({ request, onHistoryCleared }: { request: Request; onHistoryCleared: () => void }) {
  const [state, setState] = useState<any>(null)
  const [message, setMessage] = useState('')
  useEffect(() => { void request('/api/account/data-settings').then(setState).catch((err) => setMessage(err instanceof Error ? err.message : '隐私设置读取失败')) }, [])
  async function update(key: string, value: unknown) { try { const result = await request('/api/account/data-settings', { method: 'PATCH', body: JSON.stringify({ [key]: value }) }); setState(result); if (key === 'chat_history_enabled' && value === false) onHistoryCleared(); setMessage(key === 'chat_history_enabled' && value === false ? '持久化对话已清除；之后的对话仅保留在当前页面内存。' : '隐私设置已保存。') } catch (err) { setMessage(err instanceof Error ? err.message : '设置保存失败') } }
  return <section className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">CONVERSATION & PRIVACY</p><h3>对话保存与数据边界</h3></div><ShieldCheck size={20} /></div>{state && <><label className="consent privacy-toggle"><input type="checkbox" checked={state.chat_history_enabled} onChange={(e) => { if (!e.target.checked && !window.confirm('关闭后会永久清除此账号的已保存聊天记录及会话。继续？')) return; void update('chat_history_enabled', e.target.checked) }} /> 保存 AI 助手对话到本机数据库</label><p className="muted">聊天内容和会话仅属于当前账号。关闭保存会清除已有会话；之后的对话仅保留在当前页面内存中，刷新、重新登录或换设备后无法恢复。健康、财务和凭证不会因此加入 AI 上下文。</p><p className="settings-meta">保存开启时刷新、重新登录或其他已登录设备可继续会话。明确触发 AI 时仍遵循每次请求的授权范围。</p></>}{message && <div className="notice inline-notice info-notice">{message}</div>}</section>
}

export function DataBackupPanel({ request }: { request: Request }) {
  const [password, setPassword] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState(false)
  async function validatePackage(event: React.FormEvent) { event.preventDefault(); if (!file || !password) return; const form = new FormData(); form.append('migration_file', file); form.append('recovery_password', password); setBusy(true); try { const result = await request('/api/data/migration/validate', { method: 'POST', body: form }); setMessage(`验证通过 · Alembic ${result.alembic_revision} · Provider 凭证解密校验通过。验证在临时目录完成，未修改正式库。`); setError(false) } catch (err) { setMessage(err instanceof Error ? err.message : '迁移包校验失败'); setError(true) } finally { setBusy(false) } }
  return <div className="settings-panels">
    <section className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">LOCAL SNAPSHOT</p><h3><HardDriveDownload size={18} /> 本机备份</h3></div></div><div className="notice inline-notice info-notice">Web 实例备份管理当前不可用。普通 Web 账号不能查看或创建整库备份，也不能设置后台整库备份计划。</div><p className="settings-meta">CLI 和既有后台备份流程保留；请由受信任的本机运维流程管理实例级备份。</p></section>
    <section className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">PORTABLE RESTORE PACKAGE</p><h3><Download size={18} /> 加密迁移包</h3><p className="muted">Web 全库迁移包导出暂不可用；恢复仍由受信任的本机 CLI 执行。</p></div><FileCheck2 size={20} /></div><p className="notice inline-notice info-notice">Web 全库迁移导出已禁用，避免普通账号取得实例级数据库或凭证材料。</p><form className="settings-form restore-check" onSubmit={validatePackage}><label>选择迁移包（.posmig）<input type="file" accept=".posmig,application/vnd.personal-os.migration" onChange={(e) => setFile(e.target.files?.[0] || null)} required /></label><label>恢复口令<input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label><button className="secondary" disabled={busy || !file}>在隔离临时目录校验</button></form><p className="settings-meta">恢复程序：停止服务后使用本机 CLI 恢复到新的空路径；不会覆盖当前数据库或自动切换服务。完整恢复说明见下方。</p><details className="recovery-instructions"><summary>查看安全恢复说明</summary><ol><li>先在新机器安装同版本 Personal OS，并安全复制加密的 .posmig 文件。</li><li>在本机 CLI 输入恢复口令并校验；不得把口令与迁移包放在同一位置。</li><li>停止 FastAPI 后，用恢复脚本指定一个不存在的新 SQLite 路径；原数据库保留不动。</li><li>迁移包含现有 Fernet 主密钥，解密只在受保护恢复流程内校验；导入后旧会话全部撤销，需要重新登录。</li><li>更新本机安全配置指向新数据库/原 Fernet 主密钥之前，先保留旧库与另一份验证过的备份并明确批准切换。</li></ol><p>聊天记录随本轮新增的数据库表保存。尚无媒体附件存储实现；异地备份也未配置。</p></details></section>
    {message && <div className={`notice inline-notice ${error ? 'error-notice' : 'success-notice'}`}>{message}</div>}
  </div>
}

export function SystemInfoPanel({ request }: { request: Request }) {
  const [info, setInfo] = useState<any>(null)
  const [error, setError] = useState('')
  async function load() { try { const [system, health] = await Promise.all([request('/api/system/info'), request('/api/health')]); setInfo({ ...system, health: health.status, database_health: health.database }); setError('') } catch (err) { setError(err instanceof Error ? err.message : '系统信息读取失败') } }
  useEffect(() => { void load() }, [])
  return <div className="settings-panels system-page">
    <section className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">SYSTEM STATUS</p><h3>系统信息</h3></div><button className="secondary" onClick={load}>刷新</button></div>{error ? <div className="notice error-notice">{error}</div> : <dl className="system-info">{[['应用版本', info?.app_version], ['运行环境', info?.runtime_environment], ['数据库类型', info?.database], ['数据库文件名', info?.database_path], ['Alembic 版本', info?.alembic_revision], ['服务在线', info?.service_online ? '是' : '否'], ['数据库健康', info?.database_health], ['异地备份', info?.offsite_backup === 'not_configured' ? '未配置' : info?.offsite_backup]].map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd>{value ?? '读取中…'}</dd></div>)}</dl>}</section>
    {info && <>
      <EngineeringJournal />
      {info.releases?.filter((item: any) => item.lessons?.length).map((item: any) => <section className="panel settings-card" key={item.version} aria-label="稳定性发布经验"><p className="eyebrow">RELEASED</p><h3>Personal OS v{item.version} · 已发布</h3><p>{item.name}</p><p>{item.summary}</p><ul>{item.features.map((feature: string) => <li key={feature}>{feature}</li>)}</ul><details><summary>《这次我们学到了什么》</summary><ol>{item.lessons.map((lesson: string) => <li key={lesson}>{lesson}</li>)}</ol></details><p className="muted">{item.verification}</p></section>)}
      <section className="panel settings-card release-section"><div className="panel-head"><div><p className="eyebrow">PRODUCT HISTORY</p><h3>版本更新记录</h3><p className="muted">仅展示经人工确认发布的版本；产品版本与 Alembic 数据库版本相互独立。</p></div></div><div className="release-list">{info.releases?.map((release: any) => <article className="release-card" key={release.version}><div className="release-heading"><span className="release-version">v{release.version}</span><span className="release-name">{release.name}</span><span className="release-date">{release.release_datetime || release.release_date || '发布日期未记录'}</span></div><h4>主要功能</h4><ul>{release.features.map((feature: string) => <li key={feature}>{feature}</li>)}</ul><p className="release-problem"><strong>解决的问题：</strong>{release.user_problem}</p>{release.maintenance_results?.length > 0 && <><h4>后续维护成果</h4><ul>{release.maintenance_results.map((item: string) => <li key={item}>{item}</li>)}</ul></>}<details><summary>技术实现、架构思路与限制</summary><p>{release.technical_details}</p><h4>已知限制 / 尚未完成</h4><ul>{release.limitations.map((item: string) => <li key={item}>{item}</li>)}</ul></details></article>)}</div></section>
      <section className="panel settings-card roadmap-section"><div className="panel-head"><div><p className="eyebrow">PLANNED · NOT RELEASED</p><h3>后续规划</h3><p className="muted">规划内容不代表已实现或已发布。</p></div></div><div className="roadmap-list">{info.roadmap?.map((item: any) => <div className="roadmap-row" key={item.version}><span className="release-version">v{item.version}</span><strong>{item.name}</strong><span className="tag planned-tag">规划中</span></div>)}</div></section>
    </>}
  </div>
}

export function AIUsagePanel({ request }: { request: Request }) {
  const [data, setData] = useState<any>(null)
  const [conversations, setConversations] = useState<any[]>([])
  const [conversationId, setConversationId] = useState('')
  const [maxToolCalls, setMaxToolCalls] = useState(8)
  const [maxTokens, setMaxTokens] = useState(1600)
  const [message, setMessage] = useState('')
  const [error, setError] = useState(false)
  async function load() {
    try {
      const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
      const value = await request(`/api/settings/ai/usage${query}`)
      setData(value); setMaxToolCalls(value.budget.max_tool_calls); setMaxTokens(value.budget.max_output_tokens); setError(false)
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : '用量读取失败'); setError(true) }
  }
  useEffect(() => {
    void request('/api/agent/chat/conversations?limit=100').then((value) => setConversations(value.conversations || [])).catch(() => setConversations([]))
  }, [])
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 10000); return () => window.clearInterval(timer) }, [conversationId])
  async function saveBudget(event: React.FormEvent) {
    event.preventDefault()
    try { const result = await request('/api/settings/ai/usage/budget', { method: 'PUT', body: JSON.stringify({ max_tool_calls: maxToolCalls, max_output_tokens: maxTokens }) }); setMessage(`预算已保存：每轮最多 ${result.max_tool_calls} 次工具调用、最多请求 ${result.max_output_tokens} 个输出 Token。`); setError(false); await load() }
    catch (cause) { setMessage(cause instanceof Error ? cause.message : '预算保存失败'); setError(true) }
  }
  const fmt = (value: number | null | undefined) => value == null ? 'UNKNOWN' : value.toLocaleString()
  const periodLabel = (key: string) => ({ today: '今天', last_7_days: '最近 7 天', month_to_date: '本月' } as Record<string, string>)[key] || key
  return <div className="settings-panels ai-usage-page">
    <section className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">AI USAGE · {data?.timezone || '读取中'}</p><h3>AI 用量与成本</h3><p className="muted">只统计本应用记录的实际请求。Token 以服务商返回的 usage 为准；未提供时显示 UNKNOWN，不估算费用。</p></div><button className="secondary" onClick={() => void load()}>立即刷新</button></div>
      <div className="usage-periods">{Object.entries(data?.periods || {}).map(([key, value]: [string, any]) => <article className="usage-period" key={key}><strong>{periodLabel(key)}</strong><span>输入 {fmt(value.input_tokens)}</span><span>输出 {fmt(value.output_tokens)}</span><b>合计 {fmt(value.total_tokens)}</b><small>未知用量请求 {value.unknown_usage_records} · 历史聚合 Run {value.legacy_aggregated_runs}</small></article>)}</div>
      <label className="usage-session-select">会话累计（逐请求明细）<select value={conversationId} onChange={(event) => setConversationId(event.target.value)}><option value="">选择会话…</option>{conversations.map((item) => <option key={item.id} value={item.id}>{item.title || '新会话'} · {item.domain}</option>)}</select></label>
      {data?.conversation_usage && <p className="settings-meta">会话：输入 {fmt(data.conversation_usage.input_tokens)} · 输出 {fmt(data.conversation_usage.output_tokens)} · 未知 {data.conversation_usage.unknown_usage_records}</p>}
      {data?.by_model?.length > 0 && <div className="usage-models"><h4>按 Provider / 模型</h4>{data.by_model.map((row: any) => <div key={`${row.provider}:${row.model}`}><strong>{row.provider} / {row.model}</strong><span>{fmt(row.input_tokens)} 输入 · {fmt(row.output_tokens)} 输出 · {row.requests} 条统计记录</span><small>未知用量 {row.unknown_usage}</small></div>)}</div>}
      <details className="usage-request-details"><summary>最近模型请求（最多 50 条）</summary><div className="usage-request-list">{data?.recent_requests?.map((row: any) => <article key={row.id}><strong>Run #{row.run_id} · 请求 {row.request_index} · {row.provider}/{row.model || 'UNKNOWN'}</strong><span>{row.status} · {row.requested_at} · {row.total_tokens == null ? 'Token UNKNOWN' : `${row.input_tokens} 输入 / ${row.output_tokens} 输出`}</span></article>)}{!data?.recent_requests?.length && <p className="muted">尚无逐请求用量记录。</p>}</div></details>
      <p className="settings-meta">历史精度：{data?.legacy_detail_note || 'UNKNOWN'} · 本次刷新：{data?.refreshed_at || '…'}</p>
    </section>
    <section className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">REQUEST GUARDS</p><h3>每轮预算提醒</h3><p className="muted">输出 Token 上限用于模型请求参数；工具调用次数由服务端强制限制。输入 Token 由服务商实际计量，无法预先精确估算。</p></div></div><form className="usage-budget-form" onSubmit={saveBudget}><label>最多工具调用<input type="number" min="1" max="8" value={maxToolCalls} onChange={(event) => setMaxToolCalls(Number(event.target.value))} /></label><label>每次请求输出 Token 上限<input type="number" min="256" max="8192" step="128" value={maxTokens} onChange={(event) => setMaxTokens(Number(event.target.value))} /></label><button className="primary">保存限制</button></form>{message && <div className={`notice inline-notice ${error ? 'error-notice' : 'info-notice'}`}>{message}</div>}</section>
    <section className="panel settings-card"><div className="panel-head"><div><p className="eyebrow">WEB SEARCH · TAVILY</p><h3>联网搜索调用量</h3><p className="muted">搜索调用与模型 Token 分开统计。</p></div></div>{data && <p>今天 {data.search.today_calls} 次 · 最近 7 天 {data.search.last_7_days_calls} 次 · 本月 {data.search.month_to_date_calls} 次</p>}<p className="settings-meta">Credits：{data?.search?.credits == null ? 'UNKNOWN' : data.search.credits}。{data?.search?.credits_note}</p></section>
  </div>
}
