import { test, expect } from '@playwright/test'

const runId = process.env.R0_RUN_ID || `engineering-${new Date().toISOString().replace(/[:.]/g, '-')}`
const username = `r0-${runId.toLowerCase().replace(/[^a-z0-9-]/g, '').slice(-24)}`
const password = 'r0-isolated-password-2026'

test('settings navigation opens the public-safe engineering journal', async ({ page }) => {
  const authStatus = await page.request.get('/api/auth/status').then((response) => response.json())
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /建立你的本地系统|欢迎回来/ })).toBeVisible()
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: authStatus.setup_required ? '创建本地账户' : '进入 Personal OS' }).click()
  await expect(page.getByRole('button', { name: '设置' })).toBeVisible()
  await page.getByRole('button', { name: '设置' }).click()
  await page.getByRole('tab', { name: '版本与工程记录' }).click()

  const journal = page.getByTestId('engineering-journal')
  await expect(journal.getByRole('heading', { name: '技术更新 / Engineering Journal' })).toBeVisible()
  await expect(journal.getByText('v0.4.0 · Source Release Candidate')).toBeVisible()
  await journal.locator('details').filter({ hasText: 'Invitation、原子领取与 Alembic' }).locator('summary').click()
  await expect(journal.getByText(/注册 API、Session 原子创建/)).toBeVisible()
  await journal.locator('details').filter({ hasText: '同机独立安装、旧库升级与恢复演练' }).locator('summary').click()
  await expect(journal.getByText(/普通 SQLite 快照保留 Session/)).toBeVisible()
  await journal.locator('details').filter({ hasText: 'Fail-Closed Schema Startup Guard' }).locator('summary').click()
  await expect(journal.getByText(/production Lifespan 在备份调度器前只读检查/)).toBeVisible()
  await journal.screenshot({ path: test.info().outputPath('engineering-journal.png') })
})
