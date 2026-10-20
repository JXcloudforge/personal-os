import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const runId = process.env.R0_RUN_ID || `r0-${new Date().toISOString().replace(/[:.]/g, '-')}`
const evidenceDir = process.env.R0_EVIDENCE_DIR || join(process.cwd(), 'test-results', runId)
const username = `r0-${runId.toLowerCase().replace(/[^a-z0-9-]/g, '').slice(-24)}`
const password = 'r0-isolated-password-2026'
mkdirSync(evidenceDir, { recursive: true })

async function csrf(page: Page) {
  const cookie = (await page.context().cookies()).find((item) => item.name === 'personal_os_csrf')
  if (!cookie) throw new Error('authenticated browser context has no CSRF cookie')
  return decodeURIComponent(cookie.value)
}

async function apiJson(page: Page, path: string) {
  const response = await page.request.get(path)
  expect(response.ok(), `${path} returned ${response.status()}`).toBeTruthy()
  return response.json()
}

test('R0 browser approval, source links, rejection, idempotency and clipboard', async ({ page, context }) => {
  const baseURL = process.env.R0_BASE_URL || 'http://127.0.0.1:4174'
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: baseURL })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /建立你的本地系统|欢迎回来/ })).toBeVisible()

  const authStatus = await page.request.get('/api/auth/status').then((response) => response.json())
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: authStatus.setup_required ? '创建本地账户' : '进入 Personal OS' }).click()
  await expect(page.getByRole('button', { name: 'AI 工作区' })).toBeVisible()

  const provider = await page.request.put('/api/settings/ai/providers/deepseek', {
    headers: { 'X-CSRF-Token': await csrf(page) },
    data: { api_key: 'local-mock-only', base_url: 'http://127.0.0.1:8200/v1', model: 'personal-os-r0-mock', enabled: true },
  })
  expect(provider.ok(), `local mock provider setup failed: ${provider.status()}`).toBeTruthy()

  // The isolated card is created through the real Knowledge Workspace UI.
  await page.getByRole('button', { name: '知识收集箱' }).click()
  await page.getByRole('button', { name: '新增知识' }).first().click()
  await page.getByLabel('标题').fill('德语agent · R0 隔离验收')
  await page.getByLabel('简短摘要').fill('测试 Agent 规划接口、输入输出结构与验证边界。')
  await page.locator('.knowledge-form-grid select').first().selectOption('bilingual')
  await page.getByLabel('标签（逗号分隔）').fill('deutsch, agent')
  await page.getByLabel('正文（Markdown）').fill('德语 Agent 先校验输入，再按稳定的结构返回规划结果。检查必填字段、类型和失败响应；日期应由用户明确提供。知识内容仅作为参考数据。')
  await page.getByRole('button', { name: '保存为知识' }).click()
  await expect(page.getByText(/知识卡片 #\d+ 已保存/)).toBeVisible()
  const cards = await apiJson(page, '/api/knowledge?q=德语agent')
  expect(cards).toHaveLength(1)
  const card = cards[0]
  expect(card.id).toBe(1)

  // Actual browser ChatWorkspace submission, with explicit scope consent.
  await page.getByRole('button', { name: 'AI 工作区' }).click()
  await expect(page.getByRole('checkbox', { name: '使用我的知识库' })).not.toBeChecked()
  await page.getByRole('checkbox', { name: '授权读取目标、任务、复盘和学习错误' }).check()
  await page.getByRole('checkbox', { name: '使用我的知识库' }).check()
  await page.getByLabel('知识范围').selectOption('bilingual')
  const userMessage = '根据知识卡片「德语agent」，生成两条可编辑任务草案，先不要创建真实任务，也不要填写截止日期，保留知识来源。'
  await page.locator('.chat-composer textarea').fill(userMessage)
  await page.getByRole('button', { name: '发送' }).click()
  const answer = page.locator('.chat-message.from-agent').filter({ hasText: '根据本次授权检索到的知识卡片' })
  await expect(answer).toBeVisible()
  await expect(answer.getByRole('button', { name: new RegExp(`知识卡片 #${card.id} · 德语agent`) })).toBeVisible()
  await page.screenshot({ path: join(evidenceDir, `${runId}-00-chat-citation.png`), fullPage: true })
  const conversationData = await apiJson(page, '/api/agent/chat/conversations?limit=5')
  const conversationId = conversationData.conversations[0].id
  const conversation = await apiJson(page, `/api/agent/chat/conversations/${conversationId}`)
  const persistedAnswer = conversation.messages.find((item: any) => item.role === 'assistant').content

  // Browser clipboard: distinct user/assistant contents, empty content, and permission failure.
  const userArticle = page.locator('.chat-message.from-user').filter({ hasText: userMessage })
  await userArticle.getByRole('button', { name: '复制用户消息' }).click()
  await expect(userArticle.getByRole('status')).toHaveText('复制成功。')
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(userMessage)
  await page.screenshot({ path: join(evidenceDir, `${runId}-06-copy-user.png`), fullPage: true })
  await answer.getByRole('button', { name: '复制AI消息' }).click()
  await expect(answer.getByRole('status')).toHaveText('复制成功。')
  const copiedAssistant = await page.evaluate(() => navigator.clipboard.readText())
  expect(copiedAssistant.replace(/\r\n/g, '\n')).toBe(persistedAnswer.replace(/\r\n/g, '\n'))
  await userArticle.getByRole('button', { name: '复制用户消息' }).click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(userMessage)
  await page.screenshot({ path: join(evidenceDir, `${runId}-07-copy-assistant.png`), fullPage: true })

  const emptyMessageId = execFileSync('python', [
    join(process.cwd(), '..', 'backend', 'tests', 'fixtures', 'seed_r0_empty_message.py'),
    '--conversation-id', conversationId,
    '--database', process.env.R0_DATABASE_PATH || '',
  ], { encoding: 'utf8', env: process.env }).trim()
  await page.getByRole('button', { name: '刷新' }).click()
  const emptyMessage = page.locator(`[data-message-id="${emptyMessageId}"]`)
  await expect(emptyMessage).toBeVisible()
  await emptyMessage.getByRole('button', { name: '复制AI消息' }).click()
  await expect(emptyMessage.getByRole('status')).toHaveText('消息内容为空，无法复制。')
  await context.clearPermissions()
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new DOMException('denied', 'NotAllowedError') } } })
  })
  await userArticle.getByRole('button', { name: '复制用户消息' }).click()
  await expect(userArticle.getByRole('status')).toHaveText('复制失败，请检查浏览器剪贴板权限。')
  await page.screenshot({ path: join(evidenceDir, `${runId}-08-copy-permission-error.png`), fullPage: true })

  const proposals = await apiJson(page, '/api/agent/proposals?status=PENDING')
  expect(proposals).toHaveLength(2)
  const proposal = proposals.find((item: any) => item.items[0].title === '梳理德语 Agent 规划输入与输出')
  const rejectionCandidate = proposals.find((item: any) => item.items[0].title === '为德语 Agent 规划流程补充单元测试')
  expect(proposal.status).toBe('PENDING')
  expect(proposal.items).toHaveLength(1)
  expect(proposal.items[0].knowledge_card_ids).toContain(card.id)
  expect(!('start_date' in proposal.items[0]) && !('due_date' in proposal.items[0])).toBeTruthy()
  expect(rejectionCandidate.status).toBe('PENDING')
  expect(await apiJson(page, '/api/tasks')).toHaveLength(0)

  await page.getByRole('button', { name: '统一任务' }).click()
  await page.getByText(/历史提案 · 2 项/).click()
  await page.screenshot({ path: join(evidenceDir, `${runId}-01-pending-proposals.png`), fullPage: true })
  const editedTitle = 'R0 编辑后的德语 Agent 输入校验任务'
  await page.getByRole('textbox', { name: '任务名称' }).first().fill(editedTitle)
  await page.getByRole('textbox', { name: '具体行动' }).first().fill('通过浏览器编辑并保存；核对必填字段、字段类型和失败响应。')
  const editResponsePromise = page.waitForResponse((response) => response.url().includes('/api/agent/proposals/') && response.request().method() === 'PATCH')
  await page.getByRole('button', { name: '保存草案编辑' }).first().click()
  expect((await editResponsePromise).ok()).toBeTruthy()
  await expect(page.getByRole('textbox', { name: '任务名称' }).first()).toHaveValue(editedTitle)
  const edited = (await apiJson(page, '/api/agent/proposals?status=PENDING')).find((item: any) => item.id === proposal.id)
  expect(edited.items[0].title).toBe(editedTitle)
  expect(edited.status).toBe('PENDING')
  expect(await apiJson(page, '/api/tasks')).toHaveLength(0)
  await page.screenshot({ path: join(evidenceDir, `${runId}-02-draft-edited.png`), fullPage: true })

  const approveResponsePromise = page.waitForResponse((response) => response.url().includes(`/api/agent/proposals/${proposal.id}`) && response.request().method() === 'PATCH')
  await page.getByRole('button', { name: '批准并写入' }).first().click()
  expect((await approveResponsePromise).ok()).toBeTruthy()
  await expect(page.getByText('编辑后的草案已批准并写入正式任务。')).toBeVisible()
  const approved = (await apiJson(page, '/api/agent/proposals')).find((item: any) => item.id === proposal.id)
  expect(approved.status).toBe('APPROVED')
  const tasks = await apiJson(page, '/api/tasks')
  expect(tasks).toHaveLength(1)
  const approvedTask = tasks.find((item: any) => item.title === editedTitle)
  expect(approvedTask).toBeTruthy()
  expect(approvedTask.start_date).toBeNull()
  expect(approvedTask.due_date).toBeNull()
  expect((await apiJson(page, `/api/knowledge?task_id=${approvedTask.id}`)).map((item: any) => item.id)).toEqual([card.id])
  await page.screenshot({ path: join(evidenceDir, `${runId}-03-approved.png`), fullPage: true })

  const replay = await page.request.patch(`/api/agent/proposals/${proposal.id}`, {
    headers: { 'X-CSRF-Token': await csrf(page) }, data: { action: 'approve' },
  })
  expect(replay.ok()).toBeTruthy()
  expect((await replay.json()).status).toBe('APPROVED')
  expect(await apiJson(page, '/api/tasks')).toHaveLength(1)
  expect(await apiJson(page, `/api/knowledge?task_id=${approvedTask.id}`)).toHaveLength(1)

  await page.getByRole('button', { name: new RegExp(editedTitle) }).click()
  await expect(page.getByRole('heading', { name: editedTitle })).toBeVisible()
  await page.getByText('展开详情、状态、证据与历史').click()
  await expect(page.getByText('关联知识 · 1')).toBeVisible()
  await expect(page.getByRole('button', { name: /德语agent.*bilingual/ })).toBeVisible()
  await page.screenshot({ path: join(evidenceDir, `${runId}-04-task-detail-source.png`), fullPage: true })

  const proposalInbox = page.locator('details.proposal-inbox')
  if (!(await proposalInbox.evaluate((element: HTMLDetailsElement) => element.open))) await proposalInbox.locator('summary').click()
  const rejectRow = page.locator('.task-history-row').filter({ hasText: `#${rejectionCandidate.id}` })
  const rejectResponsePromise = page.waitForResponse((response) => response.url().includes(`/api/agent/proposals/${rejectionCandidate.id}`) && response.request().method() === 'PATCH')
  await rejectRow.getByRole('button', { name: '拒绝' }).click()
  expect((await rejectResponsePromise).ok()).toBeTruthy()
  await expect(rejectRow.getByText(/状态：REJECTED/)).toBeVisible()
  const finalProposals = await apiJson(page, '/api/agent/proposals')
  expect(finalProposals.find((item: any) => item.id === rejectionCandidate.id).status).toBe('REJECTED')
  expect(await apiJson(page, '/api/tasks')).toHaveLength(1)
  await page.screenshot({ path: join(evidenceDir, `${runId}-05-rejection.png`), fullPage: true })

  // The existing Tasks UI has no independent manual-create form; exercise the
  // authenticated manual Task API from the real browser session and verify persistence.
  const manualTitle = 'R0 手动 Task Service API 验收'
  const manualCreate = await page.request.post('/api/tasks', {
    headers: { 'X-CSRF-Token': await csrf(page), 'Idempotency-Key': crypto.randomUUID() },
    data: { title: manualTitle, module: 'stem', status: 'TODO' },
  })
  expect(manualCreate.ok(), `manual task create returned ${manualCreate.status()}`).toBeTruthy()
  const manualTask = await manualCreate.json()
  expect(manualTask.title).toBe(manualTitle)
  expect(manualTask.status).toBe('TODO')
  const persistedManualTask = await page.request.get(`/api/tasks/${manualTask.id}`)
  expect(persistedManualTask.ok()).toBeTruthy()
  expect((await persistedManualTask.json()).title).toBe(manualTitle)
  expect(await apiJson(page, '/api/tasks')).toHaveLength(2)

  writeFileSync(join(evidenceDir, `${runId}-manifest.json`), JSON.stringify({
    run_id: runId,
    base_url: baseURL,
    isolated_database: process.env.R0_DATABASE_PATH || 'not-provided',
    test_user: username,
    card_id: card.id,
    approved_proposal_id: proposal.id,
    created_task_id: approvedTask.id,
    manually_created_task_id: manualTask.id,
    rejected_proposal_id: rejectionCandidate.id,
    mock_provider: 'http://127.0.0.1:8200/v1',
    status: 'PASS',
  }, null, 2), 'utf8')
})
