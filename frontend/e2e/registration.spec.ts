import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'

const runId = process.env.R0_RUN_ID || `registration-${Date.now()}`
const password = 'Register-Test-Password-2026!'
const backendDir = join(process.cwd(), '..', 'backend')

test.use({ trace: 'off', screenshot: 'off' })

function issueInvitation(state: 'OPEN' | 'EXPIRED' = 'OPEN') {
  const seed = [
    'from datetime import timedelta',
    'from app.database import SessionLocal',
    'from app.services.invitation_service import create_invitation',
    'db = SessionLocal()',
    'token, invitation = create_invitation(db, ttl=timedelta(hours=1))',
    ...(state === 'EXPIRED' ? ["invitation.expires_at = invitation.created_at - timedelta(seconds=1)"] : []),
    'db.commit()',
    'print(token)',
    'db.close()',
  ].join('\n')
  return execFileSync(process.env.R0_PYTHON || 'python', ['-c', seed], {
    cwd: backendDir,
    env: process.env,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim()
}

async function register(page: Page, code: string, username: string) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
  await page.getByRole('button', { name: '邀请码注册' }).click()
  await page.getByLabel('邀请码').fill(code)
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByLabel('确认密码').fill(password)
  await page.getByRole('button', { name: '创建账户' }).click()
}

async function csrf(page: Page) {
  const value = (await page.context().cookies()).find((cookie) => cookie.name === 'personal_os_csrf')?.value
  if (!value) throw new Error('authenticated test context has no CSRF cookie')
  return decodeURIComponent(value)
}

async function assertCookieFlags(page: Page) {
  const cookies = await page.context().cookies()
  const session = cookies.find((cookie) => cookie.name === 'personal_os_session')
  const clientCsrf = cookies.find((cookie) => cookie.name === 'personal_os_csrf')
  expect(session).toMatchObject({ httpOnly: true, sameSite: 'Lax', secure: false })
  expect(clientCsrf).toMatchObject({ httpOnly: false, sameSite: 'Lax', secure: false })
  expect(cookies.some((cookie) => cookie.name === 'personal_os_registration_csrf')).toBe(false)
}

test('invitation registration, task persistence, logout, and two-user task isolation', async ({ page, browser }) => {
  const cookieProbe = await browser.newContext({ baseURL: process.env.R0_BASE_URL })
  try {
    const challengeResponse = await cookieProbe.request.get('/api/auth/registration/challenge')
    expect(challengeResponse.status()).toBe(200)
    const challengeCookie = (await cookieProbe.cookies()).find((cookie) => cookie.name === 'personal_os_registration_csrf')
    expect(challengeCookie).toMatchObject({ httpOnly: true, sameSite: 'Strict', secure: false, path: '/api/auth' })
  } finally {
    await cookieProbe.close()
  }
  const invitationA = issueInvitation()
  const invitationB = issueInvitation()
  await register(page, invitationA, `reg-a-${runId.slice(-10).replace(/[^a-z0-9]/gi, '')}`)
  await expect(page.getByRole('heading', { name: '今天，把重要的事变得可验证。' })).toBeVisible()
  const meA = await page.request.get('/api/auth/me')
  expect(meA.status()).toBe(200)
  const usernameA = (await meA.json()).username as string
  await assertCookieFlags(page)

  await page.getByRole('button', { name: '统一任务' }).click()
  await page.getByText('新建任务', { exact: true }).click()
  const taskTitle = `registration-private-task-${runId.slice(-8)}`
  await page.getByLabel('名称').fill(taskTitle)
  await page.getByRole('button', { name: '保存任务' }).click()
  await expect(page.getByText('任务已保存到统一任务系统。')).toBeVisible()
  const ownTasks = await page.request.get('/api/tasks')
  expect(ownTasks.status()).toBe(200)
  const task = (await ownTasks.json()).find((item: { title: string }) => item.title === taskTitle)
  expect(task).toBeTruthy()

  await page.reload()
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
  await page.getByLabel('用户名').fill(usernameA)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '进入 Personal OS' }).click()
  await expect(page.getByRole('heading', { name: '今天，把重要的事变得可验证。' })).toBeVisible()
  await page.getByRole('button', { name: '统一任务' }).click()
  await expect(page.getByRole('button', { name: new RegExp(taskTitle) })).toBeVisible()

  await page.getByRole('button', { name: '退出' }).click()
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
  expect((await page.request.get('/api/tasks')).status()).toBe(401)

  const contextB = await browser.newContext({ baseURL: process.env.R0_BASE_URL })
  const pageB = await contextB.newPage()
  try {
    await register(pageB, invitationB, `reg-b-${runId.slice(-10).replace(/[^a-z0-9]/gi, '')}`)
    await expect(pageB.getByRole('heading', { name: '今天，把重要的事变得可验证。' })).toBeVisible()
    const meB = await pageB.request.get('/api/auth/me')
    expect(meB.status()).toBe(200)
    expect((await meB.json()).username).not.toBe(usernameA)
    await assertCookieFlags(pageB)
    const concurrentChallenges = await Promise.all([
      page.request.get('/api/auth/registration/challenge'),
      pageB.request.get('/api/auth/registration/challenge'),
    ])
    expect(concurrentChallenges.map((response) => response.status())).toEqual([200, 200])
    const foreignRead = await pageB.request.get(`/api/tasks/${task.id}`)
    expect(foreignRead.status()).toBe(404)
    expect(await foreignRead.text()).not.toContain(taskTitle)
    const foreignUpdate = await pageB.request.patch(`/api/tasks/${task.id}`, {
      headers: { 'X-CSRF-Token': await csrf(pageB) },
      data: { title: 'attempted-cross-user-update' },
    })
    expect(foreignUpdate.status()).toBe(404)
    expect(await foreignUpdate.text()).not.toContain(taskTitle)
    expect((await pageB.request.get('/api/tasks')).status()).toBe(200)
    expect(JSON.stringify(await (await pageB.request.get('/api/tasks')).json())).not.toContain(taskTitle)
  } finally {
    await contextB.close()
  }

  const contextReplay = await browser.newContext({ baseURL: process.env.R0_BASE_URL })
  const replayPage = await contextReplay.newPage()
  try {
    await register(replayPage, invitationA, `reg-c-${runId.slice(-10).replace(/[^a-z0-9]/gi, '')}`)
    await expect(replayPage.getByRole('alert')).toHaveText('注册无法完成。请检查邀请码与用户名，或联系邀请发放者。')
    expect((await replayPage.request.get('/api/auth/me')).status()).toBe(401)
  } finally {
    await contextReplay.close()
  }

  // No screenshot is taken on a page containing an invitation code or password.
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible()
  await page.getByRole('button', { name: '邀请码注册' }).click()
  await page.screenshot({ path: test.info().outputPath('registration-form-empty.png'), fullPage: true })
})

test('registration feedback covers challenge failure, expired invite, and uncertain network response without retry', async ({ page }) => {
  const expiredInvitation = issueInvitation('EXPIRED')
  await register(page, expiredInvitation, `reg-exp-${runId.slice(-8).replace(/[^a-z0-9]/gi, '')}`)
  await expect(page.getByRole('alert')).toHaveText('注册无法完成。请检查邀请码与用户名，或联系邀请发放者。')

  await page.getByLabel('邀请码').fill('synthetic-invalid-challenge-code')
  await page.route('**/api/auth/register', async (route) => {
    await route.continue({ headers: { ...route.request().headers(), 'X-Registration-CSRF': 'not-the-cookie-challenge' } })
  })
  await page.getByRole('button', { name: '创建账户' }).click()
  await expect(page.getByRole('alert')).toHaveText('安全校验未通过，请重新获取挑战后再试。')

  await page.unroute('**/api/auth/register')
  await page.route('**/api/auth/register', (route) => route.abort('connectionfailed'))
  let postCount = 0
  page.on('request', (request) => { if (request.url().endsWith('/api/auth/register') && request.method() === 'POST') postCount += 1 })
  await page.getByRole('button', { name: '创建账户' }).click()
  await expect(page.getByRole('alert')).toContainText('请先尝试使用该用户名和密码登录')
  expect(postCount).toBe(1)

  await page.unroute('**/api/auth/register')
  await page.route('**/api/auth/registration/challenge', (route) => route.fulfill({ status: 429, contentType: 'application/json', body: JSON.stringify({ detail: 'Too many registration requests' }) }))
  await page.getByRole('button', { name: '创建账户' }).click()
  await expect(page.getByRole('alert')).toHaveText('注册请求过于频繁，请稍后再试。')
})
