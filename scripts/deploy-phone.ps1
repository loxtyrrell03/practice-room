param(
  [string]$ServiceRoot = 'C:\Users\Lox\AppData\Local\MusicPracticeHomeServer',
  [string]$Python = 'C:\Users\Lox\AppData\Local\Programs\Python\Python312\python.exe'
)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$servicePath = (Resolve-Path -LiteralPath $ServiceRoot).Path
if ($servicePath -ne 'C:\Users\Lox\AppData\Local\MusicPracticeHomeServer') { throw 'Unexpected service directory; inspect ownership before deploying.' }
$runtimePath = Join-Path $servicePath 'runtime'
$gatewayPath = Join-Path $runtimePath 'music-home-server.mjs'
$startPath = Join-Path $runtimePath 'start.ps1'
$plannerPath = Join-Path $servicePath 'site\planner'
$rollbackPath = Join-Path $runtimePath 'planner-rollback'
$status = tailscale status --json | ConvertFrom-Json
$serve = tailscale serve status --json | ConvertFrom-Json
if ($status.Self.DNSName -ne 'lox-pc.tail89d19b.ts.net.') { throw 'Phone hostname differs from the owner contract.' }
if ($serve.Web.'lox-pc.tail89d19b.ts.net:10000'.Handlers.'/'.Proxy -ne 'http://127.0.0.1:8790') { throw 'Practice Room route differs from the owner contract.' }
$routeBefore = $serve | ConvertTo-Json -Depth 20 -Compress
$listener = @(Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue)
if ($listener.Count -ne 1) { throw 'Expected exactly one existing Practice Room gateway listener.' }
$gatewayProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener[0].OwningProcess)"
if ($gatewayProcess.CommandLine -notlike "*$gatewayPath*") { throw 'Port 8790 belongs to an unexpected process.' }
$backendProcess = $null
$backendListeners = @(Get-NetTCPConnection -LocalPort 8977 -State Listen -ErrorAction SilentlyContinue)
if ($backendListeners.Count) {
  $backendProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($backendListeners[0].OwningProcess)"
  if ($backendProcess.CommandLine -notlike "*$sourceRoot\server.py*") { throw 'The local planner port is occupied by an unexpected process.' }
  $meta = Invoke-RestMethod 'http://127.0.0.1:8977/api/meta' -TimeoutSec 3
  if ($meta.coachRunning -or $meta.coachQueue.pending -or $meta.practiceLogs.counts.processing) { throw 'Practice coach is active; defer deployment until its work finishes.' }
  $sessions = Invoke-RestMethod 'http://127.0.0.1:8977/api/sessions' -TimeoutSec 3
  if (@($sessions.sessions.blocks | Where-Object {$_.status -eq 'active'}).Count) { throw 'A practice timer is active; defer the scoped restart.' }
}
& $Python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Europe/London'))"
if ($LASTEXITCODE) { throw 'Install requirements.txt in the selected Python before deployment.' }
$assets = @('index.html','app.css','app.js','manifest.webmanifest','icon.svg','icon-192.png','icon-512.png')
foreach ($asset in $assets) { if (!(Test-Path -LiteralPath (Join-Path $sourceRoot $asset))) { throw "Missing asset $asset" } }
if (!(Test-Path -LiteralPath $rollbackPath)) {
  New-Item -ItemType Directory -Path $rollbackPath | Out-Null
  Copy-Item -LiteralPath $gatewayPath -Destination (Join-Path $rollbackPath 'music-home-server.mjs')
  Copy-Item -LiteralPath $startPath -Destination (Join-Path $rollbackPath 'start.ps1')
}
New-Item -ItemType Directory -Force -Path $plannerPath | Out-Null
foreach ($asset in $assets) { Copy-Item -LiteralPath (Join-Path $sourceRoot $asset) -Destination (Join-Path $plannerPath $asset) }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'music-home-server.mjs') -Destination $gatewayPath
$startText = [IO.File]::ReadAllText($startPath)
$config = @"
`$env:PRACTICE_PYTHON = '$Python'
`$env:PRACTICE_SERVER_PATH = '$sourceRoot\server.py'
`$env:PRACTICE_PLANNER_SITE_ROOT = '$plannerPath'
"@
$startText = [regex]::Replace($startText, '(?m)^\$env:PRACTICE_(PYTHON|SERVER_PATH|PLANNER_SITE_ROOT)\s*=.*\r?\n', '')
[IO.File]::WriteAllText($startPath, $config + "`r`n" + $startText)
# Recheck ownership immediately before the scoped stop. No unrelated service,
# browser session, score-app data, scheduled task or Tailscale route is changed.
$prestop = Get-NetTCPConnection -LocalPort 8790 -State Listen
if ($prestop.OwningProcess -ne $gatewayProcess.ProcessId) { throw 'Gateway ownership changed during deploy.' }
Stop-Process -Id $gatewayProcess.ProcessId -ErrorAction Stop
if ($backendProcess) { Stop-Process -Id $backendProcess.ProcessId -ErrorAction Stop }
& $startPath
$ready = $false
for ($attempt=0; $attempt -lt 30; $attempt++) {
  Start-Sleep -Milliseconds 500
  try {
    $health = Invoke-RestMethod 'http://127.0.0.1:8790/api/health' -TimeoutSec 2
    if ($health.app -eq 'practice-room') { $ready=$true; break }
  } catch { }
}
if (!$ready) { throw "Planner did not become ready. Inspect the existing logs; rollback files are in $rollbackPath." }
$routeAfter = (tailscale serve status --json | ConvertFrom-Json) | ConvertTo-Json -Depth 20 -Compress
if ($routeBefore -ne $routeAfter) { throw 'Serve configuration changed unexpectedly; investigate before claiming deployment.' }
@{date=(Get-Date).ToString('o');source=$sourceRoot;gateway=(Get-FileHash $gatewayPath).Hash;route='https://lox-pc.tail89d19b.ts.net:10000/';health=$health} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runtimePath 'planner-deployment.json') -Encoding utf8
Write-Output 'Planner is running on the existing phone route. Verify a rendered connected browser session before delivery.'
