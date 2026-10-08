param([ValidateSet('Start','Stop')][string]$Action='Start', [int]$Port=8000, [switch]$NoBrowser, [string]$PythonExecutable='')
$ErrorActionPreference='Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$sessionPath = Join-Path $projectRoot '.runtime\session.json'
function Test-OwnProcess($entry) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.pid)" -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    $recordedTime = if ($entry.created -is [datetime]) { $entry.created.ToUniversalTime().ToString('o') } else { [DateTimeOffset]::Parse([string]$entry.created).UtcDateTime.ToString('o') }
    return $process.CommandLine.Contains((Join-Path $projectRoot 'manage.py')) -and $process.CreationDate.ToUniversalTime().ToString('o') -eq $recordedTime
}
if ($Action -eq 'Stop') {
    if (-not (Test-Path -LiteralPath $sessionPath)) { Write-Output 'No owned session found.'; exit 0 }
    $session = Get-Content -LiteralPath $sessionPath -Raw | ConvertFrom-Json
    foreach ($entry in @($session.processes | Sort-Object created -Descending)) {
        if (Test-OwnProcess $entry) { Stop-Process -Id $entry.pid -ErrorAction Stop }
    }
    Remove-Item -LiteralPath $sessionPath
    for ($stopAttempt=0; $stopAttempt -lt 20; $stopAttempt++) {
        if (@($session.processes | Where-Object { Test-OwnProcess $_ }).Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    }
    Write-Output 'Owned platform processes stopped.'
    exit 0
}
if (Test-Path -LiteralPath $sessionPath) {
    $existing = Get-Content -LiteralPath $sessionPath -Raw | ConvertFrom-Json
    if (@($existing.processes | Where-Object { Test-OwnProcess $_ }).Count -gt 0) {
        Write-Output 'Platform session already exists. Stop it before starting another session.'
        exit 0
    }
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Error "Port $Port is occupied. No process was stopped. Choose another port."
    exit 1
}
$pythonPath = if ($PythonExecutable) { [IO.Path]::GetFullPath($PythonExecutable) } else { Join-Path $projectRoot 'venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create the project virtual environment first.' }
$managePath = Join-Path $projectRoot 'manage.py'
& $pythonPath -X utf8 $managePath check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonPath -X utf8 $managePath migrate --check
if ($LASTEXITCODE -ne 0) { throw 'Database migrations are pending. Back up the instance, then run manage.py migrate.' }
& $pythonPath -X utf8 $managePath collectstatic --noinput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$runtimePath = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
$ownedProcesses = @()
try {
    foreach ($arguments in @(@('serve','--port',"$Port"), @('worker'))) {
        $name = $arguments[0]
        $process = Start-Process -FilePath $pythonPath -ArgumentList (@('-X','utf8',('"'+$managePath+'"')) + $arguments) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath "$name.stdout.log") -RedirectStandardError (Join-Path $runtimePath "$name.stderr.log")
        $identity = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)"
        $ownedProcesses += @{pid=$process.Id; created=$identity.CreationDate.ToUniversalTime().ToString('o')}
        # Windows venv/python.exe is a launcher; the listening interpreter is its child.
        Start-Sleep -Milliseconds 400
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($process.Id)" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($managePath) })) {
            $ownedProcesses += @{pid=$child.ProcessId; created=$child.CreationDate.ToUniversalTime().ToString('o')}
        }
    }
    @{port=$Port; processes=$ownedProcesses} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $sessionPath -Encoding UTF8
    $ready = $false
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        if (@($ownedProcesses | Where-Object { Test-OwnProcess $_ }).Count -eq 0) { throw 'Platform processes exited. Review .runtime/*.stderr.log.' }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/" -TimeoutSec 1
            $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
            if ($health.app -eq 'zhishiku-platform' -and $health.status -eq 'ok' -and $listener.OwningProcess -in $ownedProcesses.pid) { $ready=$true; break }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Platform did not become healthy. Review logs/platform.log.' }
    Write-Output "Platform ready: http://127.0.0.1:$Port/"
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/" }
} catch {
    foreach ($entry in @($ownedProcesses | Sort-Object created -Descending)) { if (Test-OwnProcess $entry) { Stop-Process -Id $entry.pid } }
    throw
}
