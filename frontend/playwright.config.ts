import { defineConfig } from '@playwright/test'

const outputDir = process.env.R0_PLAYWRIGHT_OUTPUT_DIR || 'test-results/r0'
const manageServers = process.env.R0_MANAGE_SERVERS === '1'
const backendDir = process.env.R0_BACKEND_DIR || `${process.cwd()}/../backend`
const baseURL = process.env.R0_BASE_URL || 'http://127.0.0.1:18474'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  outputDir,
  reporter: [['list'], ['html', { outputFolder: `${outputDir}-html`, open: 'never' }]],
  webServer: manageServers ? [
    {
      command: 'python tests/fixtures/r0_mock_provider.py',
      cwd: backendDir,
      url: 'http://127.0.0.1:18200/health',
      reuseExistingServer: false,
      timeout: 30_000,
      env: { R0_MOCK_LOG: process.env.R0_MOCK_LOG || `${outputDir}/mock-provider.log`, R0_MOCK_PORT: '18200' },
    },
    {
      command: 'python -m uvicorn app.main:app --host 127.0.0.1 --port 18100 --log-level warning',
      cwd: backendDir,
      url: 'http://127.0.0.1:18100/api/health',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        PERSONAL_OS_TESTING: '1',
        PERSONAL_OS_RUNTIME_ENVIRONMENT: 'test',
        PERSONAL_OS_DATABASE_URL: process.env.PERSONAL_OS_DATABASE_URL || '',
        PERSONAL_OS_ALLOWED_ORIGINS: 'http://127.0.0.1:18474',
        PERSONAL_OS_PRODUCTION_DATABASE_URL: process.env.PERSONAL_OS_PRODUCTION_DATABASE_URL || '',
      },
    },
    {
      command: 'pnpm exec vite --config vite.r0.temp.config.ts --host 127.0.0.1',
      cwd: process.cwd(),
      url: baseURL,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ] : undefined,
  use: {
    baseURL,
    channel: 'chrome',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
})
