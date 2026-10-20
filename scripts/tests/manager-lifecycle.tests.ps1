$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\personal_os_manager.ps1') -Action Functions

function Assert-True($Condition, $Message) { if (-not $Condition) { throw $Message } }
# All process/network/logging effects are replaced; no production service changes.
function Write-ManagerLifecycleEvent { param($LifecycleEvent) }
function Get-ManagedTask { return $null }
function Get-HealthResult { return @{status='ok'; database='ok'} }
function Wait-ForHealth { return @{status='ok'; database='ok'} }
function Get-ListenerDetails { return $script:MockListener }
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru)
    Assert-True ($FilePath -eq $script:PythonWindowlessExe) 'must use project pythonw'
    Assert-True ($WorkingDirectory -eq $script:BackendRoot) 'must use fixed backend cwd'
    Assert-True ($WindowStyle -eq 'Hidden') 'must launch hidden'
    Assert-True ($ArgumentList -eq ('-B "{0}"' -f $script:ServiceEntry)) 'wrong entry'
    $script:LaunchCount++
    $script:MockListener = @{Safe=$true; ProcessId=12345}
    return @{Id=12344}
}
$script:LaunchCount=0
$script:MockListener=$null
Start-PersonalOS
Assert-True ($script:LaunchCount -eq 1) 'start should launch once'
Start-PersonalOS
Assert-True ($script:LaunchCount -eq 1) 'healthy repeated start must be idempotent'
$script:MockListener=@{Safe=$false; Reason='foreign process'; ProcessId=999}
$refused=$false
try { Start-PersonalOS } catch { $refused=$true }
Assert-True $refused 'foreign listener must be rejected'
Assert-True ($script:LaunchCount -eq 1) 'must not start over foreign listener'
function Get-HealthResult { return $null }
$script:MockListener=@{Safe=$true; ProcessId=12345}
$refused=$false
try { Start-PersonalOS } catch { $refused=$true }
Assert-True $refused 'unhealthy existing process must not launch duplicate'
$script:StopCount=0
function Stop-Process { param($Id,$ErrorAction); Assert-True ($Id -eq 12345) 'stop only verified listener'; $script:StopCount++ }
function Get-NetTCPConnection { param($State,$LocalPort,$ErrorAction); return $null }
Stop-PersonalOS
Assert-True ($script:StopCount -eq 1) 'explicit stop must stop verified process'
$script:MockListener=@{Safe=$false; Reason='foreign process'; ProcessId=999}
$refused=$false
try { Stop-PersonalOS } catch { $refused=$true }
Assert-True $refused 'must refuse foreign stop'
Assert-True ($script:StopCount -eq 1) 'must not kill foreign process'
'PASS: detached launch, repeated start, foreign listener, unhealthy listener, explicit verified stop, foreign stop'
