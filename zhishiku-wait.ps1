param([string]$Mode='probe', [string]$Url='http://127.0.0.1:8000/')
try {
    $healthUrl = ([uri]$Url).GetLeftPart([UriPartial]::Authority) + '/health/'
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
    if ($health.app -ne 'zhishiku-platform' -or $health.status -ne 'ok') { exit 1 }
    if ($Mode -eq 'open') { Start-Process $Url }
    exit 0
} catch { exit 1 }
