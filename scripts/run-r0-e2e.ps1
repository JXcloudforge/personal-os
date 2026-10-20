$ErrorActionPreference = 'Stop'

$appRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $appRoot 'backend'
$frontendDir = Join-Path $appRoot 'frontend'
$pythonExe = Join-Path $backendDir '.venv\Scripts\python.exe'
$runId = 'R0-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$testRoot = Join-Path $env:TEMP "personal_os_r033_r0_e2e_$($runId.Replace('-', '_'))"
$evidenceDir = Join-Path $env:TEMP "PersonalOS-R0-evidence\$runId"
$databasePath = Join-Path $testRoot 'isolated.sqlite3'
$viteConfigPath = Join-Path $frontendDir 'vite.r0.temp.config.ts'

foreach ($port in 18100, 18200, 18474) {
  $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($listener) { throw "Port $port is already in use (PID $($listener.OwningProcess)); refusing to stop an unknown process." }
}
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Backend virtual environment Python was not found.' }

New-Item -ItemType Directory -Force -Path $testRoot, $evidenceDir | Out-Null
$env:PATH = (Join-Path $backendDir '.venv\Scripts') + ';' + $env:PATH
$env:PERSONAL_OS_TESTING = '1'
$env:PERSONAL_OS_RUNTIME_ENVIRONMENT = 'test'
$env:PERSONAL_OS_COOKIE_SECURE = 'false'
$env:PERSONAL_OS_SECRET_KEY = 'r0-test-only-secret-key-not-for-any-environment-2026'
$env:PERSONAL_OS_CREDENTIAL_MASTER_KEY = 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='
$env:PERSONAL_OS_DATABASE_URL = 'sqlite:///' + $databasePath.Replace('\', '/')
$env:PERSONAL_OS_PRODUCTION_DATABASE_URL = 'sqlite:///' + (Join-Path $testRoot 'production-sentinel.sqlite3').Replace('\', '/')
$env:PERSONAL_OS_ALLOWED_ORIGINS = 'http://127.0.0.1:18474'
$env:R0_DATABASE_PATH = $databasePath
$env:R0_RUN_ID = $runId
$env:R0_EVIDENCE_DIR = $evidenceDir
$env:R0_PLAYWRIGHT_OUTPUT_DIR = $evidenceDir
$env:R0_BASE_URL = 'http://127.0.0.1:18474'
$env:R0_BACKEND_DIR = $backendDir
$env:R0_MANAGE_SERVERS = '1'
$env:R0_MOCK_LOG = Join-Path $evidenceDir 'mock-provider.log'

try {
  & $pythonExe (Join-Path $backendDir 'tests\fixtures\migrate_isolated_r0_db.py') --database $databasePath
  if ($LASTEXITCODE -ne 0) { throw 'Isolated Alembic migration failed.' }

  @'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({ plugins: [react()], server: { host: '127.0.0.1', port: 18474, strictPort: true, proxy: { '/api': 'http://127.0.0.1:18100' } } })
'@ | Set-Content -LiteralPath $viteConfigPath -Encoding utf8

  Push-Location $frontendDir
  try {
    pnpm exec playwright test
    if ($LASTEXITCODE -ne 0) { throw 'Playwright R0 E2E failed; failure artifacts are retained in the evidence directory.' }
  } finally {
    Pop-Location
  }
} finally {
  if (Test-Path -LiteralPath $viteConfigPath) { Remove-Item -LiteralPath $viteConfigPath }
  Remove-Item Env:PERSONAL_OS_TESTING -ErrorAction SilentlyContinue
  Remove-Item Env:PERSONAL_OS_RUNTIME_ENVIRONMENT -ErrorAction SilentlyContinue
  Remove-Item Env:PERSONAL_OS_COOKIE_SECURE -ErrorAction SilentlyContinue
  Remove-Item Env:PERSONAL_OS_SECRET_KEY -ErrorAction SilentlyContinue
  Remove-Item Env:PERSONAL_OS_CREDENTIAL_MASTER_KEY -ErrorAction SilentlyContinue
  Remove-Item Env:PERSONAL_OS_DATABASE_URL -ErrorAction SilentlyContinue
  Remove-Item Env:PERSONAL_OS_PRODUCTION_DATABASE_URL -ErrorAction SilentlyContinue
  Remove-Item Env:PERSONAL_OS_ALLOWED_ORIGINS -ErrorAction SilentlyContinue
  Remove-Item Env:R0_DATABASE_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:R0_RUN_ID -ErrorAction SilentlyContinue
  Remove-Item Env:R0_EVIDENCE_DIR -ErrorAction SilentlyContinue
  Remove-Item Env:R0_PLAYWRIGHT_OUTPUT_DIR -ErrorAction SilentlyContinue
  Remove-Item Env:R0_BASE_URL -ErrorAction SilentlyContinue
  Remove-Item Env:R0_BACKEND_DIR -ErrorAction SilentlyContinue
  Remove-Item Env:R0_MANAGE_SERVERS -ErrorAction SilentlyContinue
  Remove-Item Env:R0_MOCK_LOG -ErrorAction SilentlyContinue
}
