# Readiness helper for the local dev services. ASCII-only on purpose.
#
#   probe <url>  -> exit 0 when the service answers, 1 otherwise
#   open  <url>  -> poll until it answers (max ~120s), then open the browser
#
# Two hard-won details:
#   * HttpWebRequest, NOT HttpClient. Windows PowerShell 5.1 does not have the
#     System.Net.Http assembly loaded, so New-Object System.Net.Http.HttpClientHandler
#     throws "cannot find type ... make sure the assembly containing this type is
#     loaded" (and Add-Type cannot be used here). HttpWebRequest lives in
#     System.dll and is always available.
#   * Proxy = $null is mandatory. This machine runs Clash; a proxied request to
#     127.0.0.1 answers 502 even when the service is perfectly healthy.
param(
    [Parameter(Mandatory=$true)][ValidateSet('probe','open')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$Url
)
$ErrorActionPreference = 'SilentlyContinue'

function Test-ServiceUp([string]$u) {
    try {
        $rq = [System.Net.HttpWebRequest]::Create($u)
        $rq.Proxy = $null
        $rq.Timeout = 3000
        $rq.ReadWriteTimeout = 3000
        $rq.Method = 'GET'
        $rq.AllowAutoRedirect = $true
        $resp = $rq.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        return ($code -ge 200 -and $code -lt 500)
    } catch {
        # an HTTP error status still proves something is serving on that port
        if ($_.Exception.Response) { return $true }
        return $false
    }
}

if ($Mode -eq 'probe') {
    if (Test-ServiceUp $Url) { exit 0 }
    exit 1
}

$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    if (Test-ServiceUp $Url) {
        Start-Process $Url      # default browser, no COM needed
        exit 0
    }
    Start-Sleep -Milliseconds 900
}
exit 1
