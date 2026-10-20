param([ValidateSet('Menu', 'Start', 'Stop', 'Restart', 'Status', 'Functions')][string]$Action = 'Menu')
$ErrorActionPreference = 'Stop'

$managerScriptDirectory = if ($env:PERSONAL_OS_MANAGER_PATH) { Split-Path -Parent $env:PERSONAL_OS_MANAGER_PATH } else { $PSScriptRoot }
$script:ProjectRoot = (Resolve-Path (Join-Path $managerScriptDirectory '..')).Path
$script:BackendRoot = Join-Path $script:ProjectRoot 'backend'
$script:PythonExe = Join-Path $script:BackendRoot '.venv\Scripts\python.exe'
$script:PythonWindowlessExe = Join-Path $script:BackendRoot '.venv\Scripts\pythonw.exe'
$script:ServiceEntry = Join-Path $script:BackendRoot 'scripts\windows_service.py'
$script:TaskName = 'PersonalOS-FastAPI'
$script:HealthUrl = 'http://127.0.0.1:8000/api/health'
$script:Port = 8000
$localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
$script:LogPath = Join-Path $localAppData 'PersonalOS\logs\fastapi.log'
$script:LifecycleLogPath = Join-Path $localAppData 'PersonalOS\logs\manager-lifecycle.log'

function Write-ManagerLifecycleEvent {
    param([Parameter(Mandatory)][string]$LifecycleEvent)
    try {
        $directory = Split-Path -Parent $script:LifecycleLogPath
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        $line = '{0:o} event={1} pid={2}' -f (Get-Date), $LifecycleEvent, $PID
        Add-Content -LiteralPath $script:LifecycleLogPath -Value $line -Encoding UTF8
    } catch {
        Write-Warning "无法写入管理器生命周期日志：$script:LifecycleLogPath"
    }
}

function Get-ListenerDetails {
    $connections = @(Get-NetTCPConnection -State Listen -LocalPort $script:Port -ErrorAction SilentlyContinue)
    if (-not $connections) { return $null }
    $ids = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($ids.Count -ne 1 -or @($connections | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) {
        return [pscustomobject]@{ Safe = $false; Reason = '端口监听地址或进程数与预期不符'; ProcessId = $null }
    }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($ids[0])" -ErrorAction SilentlyContinue
    if (-not $proc) {
        Start-Sleep -Milliseconds 300
        $stillListening = Get-NetTCPConnection -State Listen -LocalPort $script:Port -ErrorAction SilentlyContinue
        if (-not $stillListening) { return $null }
        return [pscustomobject]@{ Safe = $false; Reason = '无法核验监听进程'; ProcessId = $ids[0] }
    }

    $expectedExe = [IO.Path]::GetFullPath($script:PythonExe)
    $command = [string]$proc.CommandLine
    $newEntry = $command.IndexOf([IO.Path]::GetFullPath($script:ServiceEntry), [StringComparison]::OrdinalIgnoreCase) -ge 0
    $legacyEntry = $command -match '(?i)uvicorn\s+app\.main:app' -and $command -match '(?i)127\.0\.0\.1' -and $command -match '(?i)8000'
    # Windows venv launchers may resolve ExecutablePath to the base Python
    # binary while preserving the venv executable in the actual command line.
    $expectedExecutableQuoted = '"' + $expectedExe + '"'
    $usesProjectVenv = $command.StartsWith($expectedExecutableQuoted, [StringComparison]::OrdinalIgnoreCase) -or $command.StartsWith($expectedExe + ' ', [StringComparison]::OrdinalIgnoreCase)
    $windowless = [IO.Path]::GetFullPath($script:PythonWindowlessExe)
    $usesProjectVenv = $usesProjectVenv -or $command.StartsWith(('"' + $windowless + '"'), [StringComparison]::OrdinalIgnoreCase) -or $command.StartsWith($windowless + ' ', [StringComparison]::OrdinalIgnoreCase)
    $verified = $usesProjectVenv -and ($newEntry -or $legacyEntry)
    return [pscustomobject]@{
        Safe = $verified
        Reason = if ($verified) { '已核验 Personal OS Python/Uvicorn 进程' } else { '8000 端口由非 Personal OS 或无法核验的进程占用；不会停止它' }
        ProcessId = [int]$proc.ProcessId
        ParentProcessId = [int]$proc.ParentProcessId
        CommandLine = $command
    }
}

function Get-HealthResult {
    try {
        $result = Invoke-RestMethod -Uri $script:HealthUrl -TimeoutSec 4 -ErrorAction Stop
        if ($result.status -eq 'ok' -and $result.database -eq 'ok') { return $result }
    } catch { }
    return $null
}

function Wait-ForHealth {
    param([int]$Seconds = 45)
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        $health = Get-HealthResult
        if ($health) { return $health }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Get-ManagedTask {
    return Get-ScheduledTask -TaskPath '\' -TaskName $script:TaskName -ErrorAction SilentlyContinue
}

function Test-ExpectedTaskAction {
    param($Task)
    if (-not $Task -or $Task.Actions.Count -ne 1) { return $false }
    $action = $Task.Actions[0]
    $manager = Join-Path $managerScriptDirectory 'personal_os_manager.ps1'
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $expectedArguments = '-NoLogo -NoProfile -NonInteractive -File "{0}" -Action Start' -f $manager
    if ([string]$action.Execute -ieq $powershell -and [string]$action.Arguments -ceq $expectedArguments -and [string]$action.WorkingDirectory -ieq $script:BackendRoot) { return $true }
    $python = [IO.Path]::GetFullPath($script:PythonExe)
    $service = [IO.Path]::GetFullPath($script:ServiceEntry)
    $actualExe = try { [IO.Path]::GetFullPath([string]$action.Execute) } catch { [string]$action.Execute }
    if ($actualExe -ieq $python -and ([string]$action.Arguments).IndexOf($service, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and [string]$action.WorkingDirectory -ieq $script:BackendRoot) {
        return $true
    }
    $legacy = [string]$action.Arguments
    return ([string]$action.Execute -ieq (Join-Path $env:SystemRoot 'System32\cmd.exe')) -and $legacy.Contains('start_personal_os.cmd') -and ([string]$action.WorkingDirectory -ieq $script:BackendRoot)
}

function Show-ServiceStatus {
    $task = Get-ManagedTask
    $taskInfo = if ($task) { Get-ScheduledTaskInfo -TaskPath '\' -TaskName $script:TaskName -ErrorAction SilentlyContinue } else { $null }
    $listener = Get-ListenerDetails
    $health = Get-HealthResult
    Write-Host ''
    if ($listener -and $listener.Safe) {
        Write-Host "Personal OS 进程：PID $($listener.ProcessId)，127.0.0.1:$script:Port"
    } elseif ($listener) {
        Write-Host "端口检查：$($listener.Reason)"
    } else { Write-Host 'Personal OS 监听进程：未运行' }
    $healthText = if ($health) { 'API/数据库健康：正常' } else { 'API/数据库健康：不可用' }
    Write-Host $healthText
    if ($task) {
        Write-Host "登录自动启动任务：$($task.State)；动作核验：$(if (Test-ExpectedTaskAction $task) {'符合预期'} else {'不符合预期'})"
        if ($taskInfo) {
            Write-Host "最近运行：$($taskInfo.LastRunTime)；退出码：$($taskInfo.LastTaskResult)"
        }
        Write-Host "触发器：$(($task.Triggers | ForEach-Object { $_.CimClass.CimClassName }) -join ', ')；运行身份：$($task.Principal.UserId)"
    } else { Write-Host '登录自动启动任务：未安装' }
    Write-Host "日志：$script:LogPath"
}

function Start-PersonalOS {
    # Serialize simultaneous manager/logon starts within this logged-in session.
    $gate = [Threading.Mutex]::new($false, 'Local\PersonalOS-Windowless-Start')
    $acquired = $false
    try {
        try { $acquired = $gate.WaitOne(60000) } catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) { throw '另一个启动操作仍在进行，请稍后查看状态。' }
        Start-PersonalOSCore
    } finally {
        if ($acquired) { $gate.ReleaseMutex() }
        $gate.Dispose()
    }
}

function Start-PersonalOSCore {
    $listener = Get-ListenerDetails
    if ($listener) {
        if (-not $listener.Safe) { throw $listener.Reason }
        if (Get-HealthResult) {
            Write-Host "Personal OS 已在运行（PID $($listener.ProcessId)）；未创建第二个实例。" -ForegroundColor Green
            return
        }
        throw "检测到 Personal OS 进程 PID $($listener.ProcessId)，但健康检查未通过。为避免重复启动，请先查看日志或执行重启。"
    }
    if (-not (Test-Path -LiteralPath $script:PythonWindowlessExe)) { throw '项目 pythonw.exe 不存在，未尝试替代运行环境。' }
    Write-ManagerLifecycleEvent 'windowless_start_requested'
    $started = Start-Process -FilePath $script:PythonWindowlessExe -ArgumentList ('-B "{0}"' -f $script:ServiceEntry) -WorkingDirectory $script:BackendRoot -WindowStyle Hidden -PassThru
    Write-ManagerLifecycleEvent "windowless_launcher_pid_$($started.Id)"
    Write-Host '已无控制台启动，等待 API 与数据库健康检查…'
    $health = Wait-ForHealth
    if (-not $health) {
        $started.Refresh()
        $result = if ($started.HasExited) { "启动进程退出码 $($started.ExitCode)" } else { '启动进程尚在运行，提交状态需检查' }
        throw "启动后未通过健康检查；$result。不会盲目重试；请查看日志：$script:LogPath"
    }
    $listener = Get-ListenerDetails
    if (-not $listener -or -not $listener.Safe) { throw '健康接口已响应，但监听进程身份无法确认；未报告启动成功。' }
    Write-Host "启动成功：PID $($listener.ProcessId)，数据库状态 $($health.database)。" -ForegroundColor Green
}

function Stop-PersonalOS {
    param([ValidateSet('menu_stop', 'restart', 'install_repair')][string]$Source = 'menu_stop')
    $task = Get-ManagedTask
    $listener = Get-ListenerDetails
    if ($listener -and -not $listener.Safe) { throw $listener.Reason }
    if (-not $listener -and (-not $task -or $task.State -ne 'Running')) {
        Write-Host 'Personal OS 当前未运行。'
        return
    }
    if ($task -and $task.State -eq 'Running') {
        if (-not (Test-ExpectedTaskAction $task)) { throw '运行中的计划任务动作未核验；拒绝停止。' }
        Write-ManagerLifecycleEvent "stop_requested_$Source"
        Stop-ScheduledTask -TaskPath '\' -TaskName $script:TaskName
        $until = (Get-Date).AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 500
            $listener = Get-ListenerDetails
            if (-not $listener) { break }
            if (-not $listener.Safe) { throw $listener.Reason }
        } while ((Get-Date) -lt $until)
        if (Get-ListenerDetails) { throw '任务已收到停止请求，但端口仍在监听。不会强制结束进程；请检查任务状态和日志。' }
    } elseif ($listener) {
        # Re-read identity immediately before stopping only the verified listener.
        $current = Get-ListenerDetails
        if (-not $current -or -not $current.Safe -or $current.ProcessId -ne $listener.ProcessId) { throw '进程身份发生变化；拒绝停止。' }
        Write-ManagerLifecycleEvent "stop_requested_${Source}_pid_$($current.ProcessId)"
        Stop-Process -Id $current.ProcessId -ErrorAction Stop
        $until = (Get-Date).AddSeconds(20)
        while ((Get-NetTCPConnection -State Listen -LocalPort $script:Port -ErrorAction SilentlyContinue) -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 500 }
        if (Get-NetTCPConnection -State Listen -LocalPort $script:Port -ErrorAction SilentlyContinue) { throw '停止后端口仍被占用；不扩大终止范围。' }
    }
    Write-Host 'Personal OS 已停止；登录自动启动配置仍保留。' -ForegroundColor Yellow
}

function Install-OrRepairAutostart {
    if (-not (Test-Path $script:PythonWindowlessExe)) { throw "项目虚拟环境不存在：$script:PythonWindowlessExe" }
    if (-not (Test-Path $script:ServiceEntry)) { throw "服务入口不存在：$script:ServiceEntry" }
    $existing = Get-ManagedTask
    if ($existing -and -not (Test-ExpectedTaskAction $existing)) {
        throw '同名计划任务的动作不属于已知的旧版或新版 Personal OS 启动器。为避免覆盖无关任务，已停止安装。'
    }
    if ($existing -and $existing.State -eq 'Running') {
        Stop-PersonalOS -Source 'install_repair'
    } elseif (Get-ListenerDetails) {
        $listener = Get-ListenerDetails
        if (-not $listener.Safe) { throw $listener.Reason }
        throw "发现未由当前计划任务管理的 Personal OS 进程 PID $($listener.ProcessId)。请先通过原管理方式停止后再安装，以免产生双实例。"
    }

    if ($existing) {
        $rollbackDir = Join-Path $localAppData 'PersonalOS\ops'
        New-Item -ItemType Directory -Path $rollbackDir -Force | Out-Null
        $rollbackPath = Join-Path $rollbackDir ("PersonalOS-FastAPI-before-{0}.xml" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Export-ScheduledTask -TaskPath '\' -TaskName $script:TaskName | Set-Content -LiteralPath $rollbackPath -Encoding Unicode
        Write-Host "已保留旧计划任务定义：$rollbackPath"
    }

    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument ('-NoLogo -NoProfile -NonInteractive -File "{0}" -Action Start' -f (Join-Path $managerScriptDirectory 'personal_os_manager.ps1')) -WorkingDirectory $script:BackendRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskPath '\' -TaskName $script:TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Personal OS FastAPI service; managed by PersonalOS-Manager.cmd.' -Force | Out-Null

    $installed = Get-ManagedTask
    $executionLimit = [Xml.XmlConvert]::ToTimeSpan([string]$installed.Settings.ExecutionTimeLimit)
    if (-not (Test-ExpectedTaskAction $installed) -or $executionLimit -ne [TimeSpan]::Zero -or $installed.Settings.MultipleInstances -ne 'IgnoreNew') {
        throw '计划任务注册后核验失败。请检查任务定义与日志；旧定义已保留供回滚。'
    }
    Write-Host "登录自动启动已安装：当前用户 $user；仅在该用户登录时运行。" -ForegroundColor Green
    Write-Host '关机或注销后服务不可访问；进入桌面会话后由登录触发器启动。'
}

function Show-RecentLogs {
    if (Test-Path $script:LifecycleLogPath) {
        Write-Host '最近管理器启停事件：'
        Get-Content -LiteralPath $script:LifecycleLogPath -Tail 20
        Write-Host "管理器生命周期日志：$script:LifecycleLogPath"
    } else {
        Write-Host "管理器生命周期日志尚未生成：$script:LifecycleLogPath"
    }
    if (-not (Test-Path $script:LogPath)) { Write-Host "日志尚未生成：$script:LogPath"; return }
    $redact = {
        param([string]$Line)
        $safe = $Line -replace '(?i)(api[_-]?key|authorization|cookie|secret|token|password)\s*[:=]\s*[^\s,;]+', '$1=[REDACTED]'
        $safe = $safe -replace '(?i)Bearer\s+\S+', 'Bearer [REDACTED]'
        $safe = $safe -replace '\bsk-[A-Za-z0-9_-]{8,}\b', '[REDACTED_KEY]'
        return $safe
    }
    Get-Content -LiteralPath $script:LogPath -Tail 80 | ForEach-Object { & $redact $_ }
    Write-Host "日志文件：$script:LogPath"
}

function Show-TailscaleServe {
    $cli = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if (-not $cli) { Write-Host '未找到 tailscale.exe。未执行网络配置变更。'; return }
    $output = & $cli.Source serve status 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host ($output -join "`n"); return }
    Write-Host ($output -join "`n")
    if (($output -join ' ') -match '(?i)tailnet only') {
        Write-Host '访问范围：Tailnet only；本管理器不会启用 Funnel。' -ForegroundColor Green
    } else {
        Write-Host '无法从状态输出确认访问范围；请勿据此假定为私有。'
    }
}

function Invoke-MenuAction {
    param([string]$Choice)
    try {
        switch ($Choice) {
            '1' { Start-PersonalOS }
            '2' { Stop-PersonalOS -Source 'menu_stop' }
            '3' { Stop-PersonalOS -Source 'restart'; Start-PersonalOS }
            '4' { Show-ServiceStatus }
            '5' { Show-RecentLogs }
            '6' { Install-OrRepairAutostart }
            '7' { Show-TailscaleServe }
            default { Write-Host '请输入 1 至 8。' }
        }
    } catch {
        Write-Host "操作未完成：$($_.Exception.Message)" -ForegroundColor Red
        Write-Host "可查看最近日志：$script:LogPath"
    }
}

if ($Action -eq 'Functions') { return }
if ($Action -ne 'Menu') {
    switch ($Action) {
        'Start' { Start-PersonalOS }
        'Stop' { Stop-PersonalOS }
        'Restart' { Stop-PersonalOS -Source 'restart'; Start-PersonalOS }
        'Status' { Show-ServiceStatus }
    }
    return
}
Write-ManagerLifecycleEvent 'manager_started'
while ($true) {
    Write-Host ''
    Write-Host 'Personal OS 服务管理' -ForegroundColor Cyan
    Write-Host '1. 启动 Personal OS'
    Write-Host '2. 停止 Personal OS'
    Write-Host '3. 重启 Personal OS'
    Write-Host '4. 查看服务状态'
    Write-Host '5. 查看最近日志'
    Write-Host '6. 安装或修复登录自动启动'
    Write-Host '7. 检查 Tailscale Serve'
    Write-Host '8. 退出管理器（服务继续运行）'
    $choice = Read-Host '请选择'
    if ($choice -eq '8') {
        Write-ManagerLifecycleEvent 'manager_exit_requested_service_unchanged'
        break
    }
    Invoke-MenuAction $choice
    [void](Read-Host '按 Enter 返回菜单')
}
