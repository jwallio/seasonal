# Run once from an elevated PowerShell. Does not register a new GitHub runner.
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RunnerRoot = 'D:\actions-runner-wn2',
    [string]$TaskName = 'GitHubActionsRunner-seasonal',
    [string]$Python = 'C:\Users\jlwal\miniconda3\python.exe',
    [string]$Archive = 'D:\analogwx\data\era5_proc\z500_anom.zarr\zarr.json',
    [Parameter(Mandatory)][string]$BackupDirectory
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open an elevated PowerShell to install the noninteractive Seasonal task.'
}
$runnerPath = (Resolve-Path -LiteralPath $RunnerRoot).Path.TrimEnd('\')
$task = Get-ScheduledTask -TaskName $TaskName
if ($task.Actions.Count -ne 1 -or $task.Actions[0].WorkingDirectory.TrimEnd('\') -ne $runnerPath) {
    throw 'Existing task does not exclusively target the expected Seasonal runner directory.'
}
$registration = Get-Content -LiteralPath (Join-Path $runnerPath '.runner') -Raw | ConvertFrom-Json
if ($registration.agentName -ne 'wn2-analogwx-windows-01' -or
    $registration.gitHubUrl.TrimEnd('/') -notin @('https://github.com/jwallio/seasonal', 'https://github.com/jwallio/wn2')) {
    throw 'Runner registration is not the expected Seasonal runner. No changes made.'
}
if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $Archive)) {
    throw 'The existing Python or analog archive is missing.'
}
if (-not [IO.Path]::IsPathRooted($BackupDirectory)) { throw 'BackupDirectory must be absolute.' }
if (Test-Path -LiteralPath (Join-Path $BackupDirectory 'task-before.xml')) {
    throw 'Use a new backup directory; never overwrite the original task backup.'
}
if (-not $PSCmdlet.ShouldProcess($TaskName, 'Back up and install S4U startup plus five-minute recovery triggers')) { return }
New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
$backupPath = (Resolve-Path -LiteralPath $BackupDirectory).Path
$before = Export-ScheduledTask -TaskName $TaskName
$before | Set-Content -LiteralPath (Join-Path $backupPath 'task-before.xml') -Encoding Unicode
$probeName = "$TaskName-S4U-repair-probe"
if (Get-ScheduledTask -TaskName $probeName -ErrorAction SilentlyContinue) { throw 'Repair probe already exists; inspect it before continuing.' }
$ownerSid = if ($task.Principal.UserId -like 'S-1-*') {
    ([Security.Principal.SecurityIdentifier]$task.Principal.UserId).Value
} else {
    ([Security.Principal.NTAccount]$task.Principal.UserId).Translate([Security.Principal.SecurityIdentifier]).Value
}
# Use the existing account, with no password stored and no elevated job token.
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $ownerSid -LogonType S4U -RunLevel Limited
$probeFile = Join-Path $backupPath 'probe.ps1'
$probeResult = Join-Path $backupPath 'probe-result.json'
$quote = { param($value) "'" + $value.Replace("'", "''") + "'" }
$probeSource = @'
$ErrorActionPreference = 'Stop'
$result = [ordered]@{ utc = [DateTime]::UtcNow.ToString('o'); interactive = [Environment]::UserInteractive }
try {
    $result.archive = Test-Path -LiteralPath ARCHIVE_PATH
    & PYTHON_PATH -B -c 'import numpy,xarray,zarr,dask,scipy'
    $result.python_exit = $LASTEXITCODE
    $result.https = (Invoke-WebRequest -UseBasicParsing -Uri 'https://api.github.com' -TimeoutSec 30).StatusCode
    $result.ok = $result.archive -and $result.python_exit -eq 0 -and $result.https -eq 200 -and -not $result.interactive
} catch { $result.ok = $false; $result.error = $_.Exception.Message }
$result | ConvertTo-Json | Set-Content -LiteralPath RESULT_PATH
if (-not $result.ok) { exit 1 }
'@
$probeSource.Replace('ARCHIVE_PATH', (& $quote $Archive)).Replace('PYTHON_PATH', (& $quote $Python)).Replace('RESULT_PATH', (& $quote $probeResult)) | Set-Content -LiteralPath $probeFile
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$probeAction = New-ScheduledTaskAction -Execute $powershell -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$probeFile`""
$probeSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName $probeName -Action $probeAction -Principal $taskPrincipal -Settings $probeSettings | Out-Null
try {
    Start-ScheduledTask -TaskName $probeName
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while (-not (Test-Path -LiteralPath $probeResult) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Seconds 2 }
    if (-not (Test-Path -LiteralPath $probeResult)) { throw 'Noninteractive probe did not complete; existing runner task is unchanged.' }
    $probe = Get-Content -LiteralPath $probeResult -Raw | ConvertFrom-Json
    if (-not $probe.ok) { throw 'Noninteractive archive/runtime/HTTPS probe failed; inspect probe-result.json. Existing task unchanged.' }
} finally {
    # This task was created above for this repair only.
    if ((Get-ScheduledTask -TaskName $probeName).State -eq 'Running') { Stop-ScheduledTask -TaskName $probeName }
    Unregister-ScheduledTask -TaskName $probeName -Confirm:$false
}

# Refuse to interrupt a job. Query only runner metadata; never read credentials.
$runnerJson = & gh api "repos/jwallio/seasonal/actions/runners/$($registration.agentId)"
if ($LASTEXITCODE -ne 0) { throw 'Could not verify runner idle state; existing task is unchanged.' }
$runner = $runnerJson | ConvertFrom-Json
if ($runner.busy) { throw 'Seasonal runner is busy. Retry after its job finishes.' }
$startup = New-ScheduledTaskTrigger -AtStartup
$startup.Delay = 'PT30S'
$retry = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = $task.Settings
$settings.MultipleInstances = 2 # IgnoreNew: repeated triggers never interrupt an active listener/job.
$settings.ExecutionTimeLimit = 'PT0S'
$settings.StartWhenAvailable = $true
$wasRunning = $task.State -eq 'Running'
$listenerPath = Join-Path $runnerPath 'bin\Runner.Listener.exe'
$oldListeners = @(Get-CimInstance Win32_Process -Filter "Name='Runner.Listener.exe'" |
    Where-Object { $_.ExecutablePath -eq $listenerPath })
try {
    if ($wasRunning) { Stop-ScheduledTask -TaskName $TaskName }
    # Stopping the cmd task can leave its listener child alive. Remove only
    # captured listeners from this runner, after confirming GitHub reports idle.
    foreach ($listener in $oldListeners) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.ProcessId)"
        if ($current -and $current.ExecutablePath -eq $listenerPath -and $current.CreationDate -eq $listener.CreationDate) {
            Stop-Process -Id $listener.ProcessId -ErrorAction Stop
        }
    }
    Set-ScheduledTask -TaskName $TaskName -Action $task.Actions -Principal $taskPrincipal -Trigger @($startup, $retry) -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Export-ScheduledTask -TaskName $TaskName | Set-Content -LiteralPath (Join-Path $backupPath 'task-after.xml') -Encoding Unicode
    [ordered]@{ utc = [DateTime]::UtcNow.ToString('o'); task = $TaskName; registration_id = $registration.agentId; installed = $true; online_verification_required = $true } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupPath 'install-result.json')
} catch {
    Register-ScheduledTask -TaskName $TaskName -Xml $before -Force | Out-Null
    if ($wasRunning) { Start-ScheduledTask -TaskName $TaskName }
    throw
}
Write-Output 'Task updated. Verify runner online and a complete analog workflow before declaring recovery complete.'
