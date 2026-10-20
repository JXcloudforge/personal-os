import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

const runId = process.env.R0_RUN_ID || `p02-${new Date().toISOString().replace(/[:.]/g, '-')}`
const evidenceDir = process.env.R0_EVIDENCE_DIR || join(process.cwd(), 'test-results', runId)
const ownerName = `p02-owner-${runId.toLowerCase().replace(/[^a-z0-9-]/g, '').slice(-20)}`
const r0OwnerName = `r0-${(process.env.R0_RUN_ID || '').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(-24)}`
const otherName = `p02-other-${runId.toLowerCase().replace(/[^a-z0-9-]/g, '').slice(-20)}`
const password = 'p02-isolated-review-password'
const r0Password = 'r0-isolated-password-2026'
const backendDir = join(process.cwd(), '..', 'backend')

mkdirSync(evidenceDir, { recursive: true })

async function csrf(page: Page) {
  const value = (await page.context().cookies()).find((item) => item.name === 'personal_os_csrf')?.value
  if (!value) throw new Error('authenticated browser context has no CSRF cookie')
  return decodeURIComponent(value)
}

function mondayOf(isoDate: string) {
  const value = new Date(`${isoDate}T00:00:00Z`)
  value.setUTCDate(value.getUTCDate() - ((value.getUTCDay() + 6) % 7))
  return value
}

function formatDate(value: Date) {
  return `${value.getUTCFullYear()}-${String(value.getUTCMonth() + 1).padStart(2, '0')}-${String(value.getUTCDate()).padStart(2, '0')}`
}

test('daily and weekly Review persists owned snapshots and rejects cross-user task access', async ({ page, playwright }) => {
  const authStatus = await page.request.get('/api/auth/status').then((response) => response.json())
  const primaryOwnerName = authStatus.setup_required ? ownerName : r0OwnerName
  const primaryOwnerPassword = authStatus.setup_required ? password : r0Password
  await page.goto('/')
  if (authStatus.setup_required) {
    await expect(page.getByRole('heading', { name: '建立你的本地系统' })).toBeVisible()
    await page.getByLabel('用户名').fill(primaryOwnerName)
    await page.getByLabel('密码').fill(primaryOwnerPassword)
    await page.getByRole('button', { name: '创建本地账户' }).click()
  } else {
    await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
    await page.getByLabel('用户名').fill(r0OwnerName)
    await page.getByLabel('密码').fill(r0Password)
    await page.getByRole('button', { name: '进入 Personal OS' }).click()
  }
  await expect(page.getByRole('button', { name: /每日复盘/ })).toBeVisible()

  const taskResponse = await page.request.post('/api/tasks', {
    headers: { 'X-CSRF-Token': await csrf(page) },
    data: { title: 'P0.2 Synthetic Review Task', module: 'system' },
  })
  expect(taskResponse.status()).toBe(200)
  const task = await taskResponse.json()

  const dashboardResponse = await page.request.get('/api/dashboard')
  expect(dashboardResponse.ok()).toBeTruthy()
  const today = (await dashboardResponse.json()).today_date as string

  let delayedDailyRead = false
  await page.route('**/api/reviews/daily/**', async (route) => {
    if (!delayedDailyRead) {
      delayedDailyRead = true
      const response = await route.fetch()
      await new Promise((resolve) => setTimeout(resolve, 900))
      await route.fulfill({ response })
      return
    }
    await route.continue()
  })
  await page.getByRole('button', { name: /每日复盘/ }).click()
  await expect(page.getByRole('heading', { name: '每日执行结算' })).toBeVisible()
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 })
    const geometry = await page.locator('.daily-review-form').evaluate((form) => {
      const formRect = form.getBoundingClientRect()
      const controls = Array.from(form.querySelectorAll('textarea, button.primary'))
      const labels = Array.from(form.querySelectorAll('label'))
      const refresh = document.querySelector('.daily-review-workspace > .panel > .panel-head > button.secondary')
      const refreshRect = refresh?.getBoundingClientRect()
      return {
        formWidth: formRect.width,
        scrollWidth: form.scrollWidth,
        clientWidth: form.clientWidth,
        labelsHaveGridLayout: labels.every((label) => getComputedStyle(label).display === 'grid'),
        controlsFit: controls.every((control) => {
          const rect = control.getBoundingClientRect()
          return rect.width > 0 && rect.left >= formRect.left - 1 && rect.right <= formRect.right + 1
        }),
        refreshButtonWidth: refreshRect?.width || 0,
        refreshButtonHeight: refreshRect?.height || 0,
      }
    })
    expect(geometry.formWidth).toBeGreaterThan(0)
    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth)
    expect(geometry.labelsHaveGridLayout).toBeTruthy()
    expect(geometry.controlsFit).toBeTruthy()
    expect(geometry.refreshButtonWidth).toBeGreaterThanOrEqual(62)
    expect(geometry.refreshButtonHeight).toBeLessThanOrEqual(48)
    await expect(page.getByLabel('实际完成了什么')).toBeVisible()
    await page.screenshot({ path: test.info().outputPath(`review-daily-${width}px.png`), fullPage: true })
    if (width === 390) {
      const mobileSummary = 'Synthetic Review save and refresh at 390px.'
      await page.getByLabel('实际完成了什么').fill(mobileSummary)
      await page.getByRole('button', { name: /保存 \/ 更新本日结算/ }).click()
      await expect(page.getByText(`已保存 ${today} 的复盘。任务状态未被自动更改。`)).toBeVisible()
      await page.locator('.daily-review-workspace > .panel > .panel-head button.secondary').click()
      await expect(page.getByLabel('实际完成了什么')).toHaveValue(mobileSummary)
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 })
  const delayedReadRequest = page.waitForRequest((request) => request.url().includes('/api/reviews/daily/') && request.method() === 'GET')
  await page.getByText('刷新', { exact: true }).last().click()
  await delayedReadRequest
  const initialSummary = 'P0.2 synthetic review was completed.'
  await page.getByLabel('实际完成了什么').fill(initialSummary)
  await expect(page.getByLabel('实际完成了什么')).toHaveValue(initialSummary)
  await page.waitForTimeout(1000)
  await expect(page.getByLabel('实际完成了什么')).toHaveValue(initialSummary)
  await page.getByLabel('添加任务到结算').selectOption(String(task.id))
  await expect(page.getByLabel('实际完成了什么')).toHaveValue(initialSummary)
  await page.getByLabel('执行证据（自报）').fill('Self-reported evidence; not formal TaskEvidence.')
  const initialSaveRequest = page.waitForRequest((request) => request.url().endsWith('/api/reviews/daily') && request.method() === 'POST')
  await page.getByRole('button', { name: /保存 \/ 更新本日结算/ }).click()
  const submittedInitialReview = JSON.parse((await initialSaveRequest).postData() || '{}')
  expect(submittedInitialReview.actual_summary).toBe(initialSummary)
  await expect(page.getByText(`已保存 ${today} 的复盘。任务状态未被自动更改。`)).toBeVisible()

  await page.reload()
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
  await page.getByLabel('用户名').fill(primaryOwnerName)
  await page.getByLabel('密码').fill(primaryOwnerPassword)
  await page.getByRole('button', { name: '进入 Personal OS' }).click()
  await page.getByRole('button', { name: /每日复盘/ }).click()
  await expect(page.getByRole('heading', { name: '每日执行结算' })).toBeVisible()
  await page.waitForLoadState('networkidle')
  await expect(page.getByLabel('实际完成了什么')).toHaveValue(initialSummary)
  await expect(page.getByLabel('执行证据（自报）')).toHaveValue('Self-reported evidence; not formal TaskEvidence.')

  const editedSummary = 'P0.2 synthetic review was edited and persisted.'
  await page.getByLabel('实际完成了什么').fill(editedSummary)
  await expect(page.getByLabel('实际完成了什么')).toHaveValue(editedSummary)
  await page.getByLabel('执行证据（自报）').fill('Edited self-report for the same review snapshot.')
  const updateRequest = page.waitForRequest((request) => request.url().endsWith('/api/reviews/daily') && request.method() === 'POST')
  await page.getByRole('button', { name: /保存 \/ 更新本日结算/ }).click()
  const submittedReview = JSON.parse((await updateRequest).postData() || '{}')
  expect(submittedReview.actual_summary).toBe(editedSummary)
  await expect(page.getByText(`已保存 ${today} 的复盘。任务状态未被自动更改。`)).toBeVisible()

  const dailyAfterSave = await page.request.get(`/api/reviews/daily/${today}`)
  expect(dailyAfterSave.ok()).toBeTruthy()
  const daily = await dailyAfterSave.json()
  expect(daily.actual_summary).toBe(editedSummary)
  expect(daily.task_entries).toHaveLength(1)
  expect(daily.task_entries[0].task_title).toBe('P0.2 Synthetic Review Task')
  expect(daily.task_entries[0].evidence_note).toBe('Edited self-report for the same review snapshot.')

  await page.getByRole('button', { name: '查看每周总结' }).click()
  await expect(page.getByRole('heading', { name: '每周执行总结' })).toBeVisible()
  await expect(page.getByText(editedSummary)).toBeVisible()
  await expect(page.getByText(/1\/7 天有复盘 · 1 项任务记录 · 1 条自报证据/)).toBeVisible()
  await expect(page.getByText(/P0.2 Synthetic Review Task/)).toBeVisible()
  await page.screenshot({ path: join(evidenceDir, `${runId}-review-weekly.png`), fullPage: true })

  const futureMonday = mondayOf(today)
  futureMonday.setUTCDate(futureMonday.getUTCDate() + 7)
  const emptyWeek = formatDate(futureMonday)
  const weekPicker = page.getByLabel('周起始日期')
  await weekPicker.fill(emptyWeek)
  await expect(page.getByText('0/7 天有复盘 · 0 项任务记录 · 0 条自报证据')).toBeVisible()
  await expect(page.getByText('暂无复盘')).toHaveCount(7)
  const emptyDay = formatDate(futureMonday)
  const emptyDaily = await page.request.get(`/api/reviews/daily/${emptyDay}`)
  expect(emptyDaily.ok()).toBeTruthy()
  expect((await emptyDaily.json()).task_entries).toEqual([])

  const invalidWednesday = new Date(futureMonday)
  invalidWednesday.setUTCDate(invalidWednesday.getUTCDate() + 2)
  await weekPicker.fill(formatDate(invalidWednesday))
  await expect(page.getByText('周起始日期必须为周一。')).toBeVisible()
  await page.screenshot({ path: join(evidenceDir, `${runId}-review-invalid-week.png`), fullPage: true })

  const seedOtherUser = [
    'from app.auth.security import hash_password',
    'from app.database import SessionLocal',
    'from app.models import User',
    'import sys',
    'db = SessionLocal()',
    'db.add(User(username=sys.argv[1], password_hash=hash_password(sys.argv[2])))',
    'db.commit()',
    'db.close()',
  ].join('\n')
  execFileSync(process.env.R0_PYTHON || 'python', ['-c', seedOtherUser, otherName, password], {
    cwd: backendDir,
    env: process.env,
    stdio: 'pipe',
  })

  const other = await playwright.request.newContext({ baseURL: process.env.R0_BASE_URL })
  try {
    const login = await other.post('/api/auth/login', { data: { username: otherName, password } })
    expect(login.status()).toBe(200)
    const otherCsrf = (await other.cookies()).find((item) => item.name === 'personal_os_csrf')?.value
    expect(otherCsrf).toBeTruthy()
    const crossUserWrite = await other.post('/api/reviews/daily', {
      headers: { 'X-CSRF-Token': decodeURIComponent(otherCsrf!) },
      data: {
        review_date: today,
        actual_summary: 'Must not expose another user task.',
        task_entries: [{ task_id: task.id, report_category: 'in_progress' }],
      },
    })
    expect(crossUserWrite.status()).toBe(404)
    expect(await crossUserWrite.text()).not.toContain('P0.2 Synthetic Review Task')
    const otherWeek = await other.get(`/api/reviews/weekly?week_start=${formatDate(mondayOf(today))}`)
    expect(otherWeek.ok()).toBeTruthy()
    const otherSummary = await otherWeek.json()
    expect(otherSummary.task_entry_count).toBe(0)
    expect(JSON.stringify(otherSummary)).not.toContain('P0.2 Synthetic Review Task')
  } finally {
    await other.dispose()
  }

  const evidenceCheck = [
    'import sqlite3, sys',
    'connection = sqlite3.connect(sys.argv[1])',
    'count = connection.execute("SELECT COUNT(*) FROM task_evidence WHERE task_id = ?", (int(sys.argv[2]),)).fetchone()[0]',
    'print(count)',
    'connection.close()',
  ].join('\n')
  const formalEvidenceCount = execFileSync(process.env.R0_PYTHON || 'python', ['-c', evidenceCheck, process.env.R0_DATABASE_PATH || '', String(task.id)], {
    cwd: backendDir,
    env: process.env,
    encoding: 'utf8',
  }).trim()
  expect(formalEvidenceCount).toBe('0')
})
