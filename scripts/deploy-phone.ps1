param(
  [string]$ServiceRoot = 'C:\Users\Lox\AppData\Local\MusicPracticeHomeServer',
  [string]$Python = 'C:\Users\Lox\AppData\Local\Programs\Python\Python312\python.exe'
)
$ErrorActionPreference = 'Stop'

function Get-OwnedListener {
  param([int]$Port, [string]$Script, [string]$Executable, [switch]$AllowAbsent)
  $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
  if (!$listeners.Count -and $AllowAbsent) { return $null }
  if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1') { throw "Expected exactly one loopback listener on port $Port." }
  $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$($listeners[0].OwningProcess)"
  $escaped = [regex]::Escape($Script)
  $argumentPattern = '(?:^|\s)(?:"' + $escaped + '"|' + $escaped + ')(?=\s|$)'
  if (!$candidate -or $candidate.ExecutablePath -ine $Executable -or $candidate.CommandLine -notmatch $argumentPattern) {
    throw "Port $Port belongs to an unexpected process; it will not be stopped."
  }
  return $candidate
}

function Assert-SameProcess {
  param($Expected, $Actual)
  if (!$Expected -or !$Actual -or $Expected.ProcessId -ne $Actual.ProcessId -or $Expected.CreationDate -ne $Actual.CreationDate -or $Expected.CommandLine -cne $Actual.CommandLine) {
    throw 'Service process identity changed; no stop is permitted.'
  }
}

function Assert-PlannerIdle {
  $meta = Invoke-RestMethod 'http://127.0.0.1:8977/api/meta' -TimeoutSec 3
  if ($null -eq $meta.coachRunning -or $null -eq $meta.coachQueue.pending -or $null -eq $meta.coachQueue.processing -or $null -eq $meta.practiceLogs.counts.processing) {
    throw 'Planner activity could not be established; defer deployment.'
  }
  if ($meta.coachRunning -or $meta.coachQueue.pending -or $meta.coachQueue.processing -or $meta.practiceLogs.counts.processing) { throw 'Practice coach is active; defer deployment.' }
  $sessions = Invoke-RestMethod 'http://127.0.0.1:8977/api/sessions' -TimeoutSec 3
  if ($null -eq $sessions.sessions) { throw 'Practice session state could not be established; defer deployment.' }
  if (@($sessions.sessions.blocks | Where-Object {$_.status -eq 'active'}).Count) { throw 'A practice timer is active; defer deployment.' }
}

function Stop-OwnedProcess {
  param($Expected, [int]$Port, [string]$Script, [string]$Executable)
  $fresh = Get-OwnedListener -Port $Port -Script $Script -Executable $Executable
  Assert-SameProcess $Expected $fresh
  # Pin an OS process handle so a recycled PID cannot select another process.
  $handle = [Diagnostics.Process]::GetProcessById([int]$fresh.ProcessId)
  try {
    $null = $handle.Handle
    if ([Math]::Abs(($handle.StartTime.ToUniversalTime() - $fresh.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) { throw 'Process start time changed; refusing to stop it.' }
    $handle.Kill()
    if (!$handle.WaitForExit(5000)) { throw 'The owned process did not stop within five seconds.' }
  } finally { $handle.Dispose() }
}

function Wait-Gateway {
  param([string]$App, [string]$Path = '/api/health', [int]$Port = 8790)
  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
      $health = Invoke-RestMethod "http://127.0.0.1:$Port$Path" -TimeoutSec 2
      if ($health.app -eq $App) { return $health }
    } catch { }
  }
  throw "Gateway did not become ready at $Path."
}

function Restore-Deployment {
  param([string]$RollbackPath, [string]$GatewayPath, [string]$StartPath, [string]$PlannerPath, $AssetState)
  Copy-Item -LiteralPath (Join-Path $RollbackPath 'music-home-server.mjs') -Destination $GatewayPath
  Copy-Item -LiteralPath (Join-Path $RollbackPath 'start.ps1') -Destination $StartPath
  foreach ($asset in $AssetState.Keys) {
    $target = Join-Path $PlannerPath $asset
    if ($AssetState[$asset]) {
      Copy-Item -LiteralPath (Join-Path (Join-Path $RollbackPath 'planner') $asset) -Destination $target
    } elseif (Test-Path -LiteralPath $target) {
      # Fixed deployment asset names only; no recursive removal.
      Remove-Item -LiteralPath $target
    }
  }
}

$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$servicePath = (Resolve-Path -LiteralPath $ServiceRoot).Path
if ($servicePath -ne 'C:\Users\Lox\AppData\Local\MusicPracticeHomeServer') { throw 'Unexpected service directory; inspect ownership before deploying.' }
$runtimePath = Join-Path $servicePath 'runtime'
$gatewayPath = Join-Path $runtimePath 'music-home-server.mjs'
$nodePath = Join-Path $runtimePath 'node.exe'
$startPath = Join-Path $runtimePath 'start.ps1'
$backendPath = Join-Path $sourceRoot 'server.py'
$plannerPath = Join-Path $servicePath 'site\planner'
$rollbackPath = Join-Path $runtimePath 'planner-rollback'
$status = tailscale status --json | ConvertFrom-Json
if ($LASTEXITCODE) { throw 'Tailscale identity could not be read.' }
$serve = tailscale serve status --json | ConvertFrom-Json
if ($LASTEXITCODE) { throw 'Tailscale routes could not be read.' }
if ($status.Self.DNSName -ne 'lox-pc.tail89d19b.ts.net.') { throw 'Phone hostname differs from the owner contract.' }
if ($serve.Web.'lox-pc.tail89d19b.ts.net:10000'.Handlers.'/'.Proxy -ne 'http://127.0.0.1:8790') { throw 'Practice Room route differs from the owner contract.' }
$routeBefore = $serve | ConvertTo-Json -Depth 20 -Compress
$gatewayProcess = Get-OwnedListener -Port 8790 -Script $gatewayPath -Executable $nodePath
$backendProcess = Get-OwnedListener -Port 8977 -Script $backendPath -Executable $Python -AllowAbsent
if ($backendProcess) { Assert-PlannerIdle }
& $Python -c "from zoneinfo import ZoneInfo; print(ZoneInfo('Europe/London'))"
if ($LASTEXITCODE) { throw 'Install requirements.txt in the selected Python before deployment.' }
& $nodePath --check (Join-Path $PSScriptRoot 'music-home-server.mjs')
if ($LASTEXITCODE) { throw 'Gateway syntax validation failed.' }
$assets = @('index.html','app.css','app.js','manifest.webmanifest','icon.svg','icon-192.png','icon-512.png')
foreach ($asset in $assets) { if (!(Test-Path -LiteralPath (Join-Path $sourceRoot $asset) -PathType Leaf)) { throw "Missing asset $asset" } }

# Refresh one bounded rollback slot. No live file changes before final checks.
New-Item -ItemType Directory -Force -Path (Join-Path $rollbackPath 'planner') | Out-Null
Copy-Item -LiteralPath $gatewayPath -Destination (Join-Path $rollbackPath 'music-home-server.mjs')
Copy-Item -LiteralPath $startPath -Destination (Join-Path $rollbackPath 'start.ps1')
$assetState = @{}
foreach ($asset in $assets) {
  $existing = Join-Path $plannerPath $asset
  $assetState[$asset] = Test-Path -LiteralPath $existing -PathType Leaf
  if ($assetState[$asset]) { Copy-Item -LiteralPath $existing -Destination (Join-Path (Join-Path $rollbackPath 'planner') $asset) }
}
$assetState | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $rollbackPath 'assets.json') -Encoding utf8
$startText = [IO.File]::ReadAllText($startPath)
$config = @(
  ('$env:PRACTICE_PYTHON = ''{0}''' -f $Python.Replace("'", "''")),
  ('$env:PRACTICE_SERVER_PATH = ''{0}''' -f $backendPath.Replace("'", "''")),
  ('$env:PRACTICE_PLANNER_SITE_ROOT = ''{0}''' -f $plannerPath.Replace("'", "''")),
  '$env:PRACTICE_PORT = ''8977'''
) -join [Environment]::NewLine
$startText = [regex]::Replace($startText, '(?m)^\$env:PRACTICE_(PYTHON|SERVER_PATH|PLANNER_SITE_ROOT|PORT)\s*=.*(?:\r?\n|$)', '')
$backendNow = Get-OwnedListener -Port 8977 -Script $backendPath -Executable $Python -AllowAbsent
if ($backendProcess -or $backendNow) {
  Assert-SameProcess $backendProcess $backendNow
  Assert-PlannerIdle
}
Assert-SameProcess $gatewayProcess (Get-OwnedListener -Port 8790 -Script $gatewayPath -Executable $nodePath)
$stoppedGateway = $false
$stoppedBackend = $false
$changedFiles = $false
$launchTime = $null
try {
  Stop-OwnedProcess -Expected $gatewayProcess -Port 8790 -Script $gatewayPath -Executable $nodePath
  $stoppedGateway = $true
  if ($backendProcess) {
    Assert-PlannerIdle
    Stop-OwnedProcess -Expected $backendProcess -Port 8977 -Script $backendPath -Executable $Python
    $stoppedBackend = $true
  }
  $changedFiles = $true
  New-Item -ItemType Directory -Force -Path $plannerPath | Out-Null
  foreach ($asset in $assets) { Copy-Item -LiteralPath (Join-Path $sourceRoot $asset) -Destination (Join-Path $plannerPath $asset) }
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'music-home-server.mjs') -Destination $gatewayPath
  [IO.File]::WriteAllText($startPath, $config + [Environment]::NewLine + $startText)
  $launchTime = Get-Date
  & $startPath
  $health = Wait-Gateway -App 'practice-room'
  $null = Get-OwnedListener -Port 8790 -Script $gatewayPath -Executable $nodePath
  $null = Get-OwnedListener -Port 8977 -Script $backendPath -Executable $Python
  $routeAfter = (tailscale serve status --json | ConvertFrom-Json) | ConvertTo-Json -Depth 20 -Compress
  if ($LASTEXITCODE -or $routeBefore -ne $routeAfter) { throw 'Serve configuration changed unexpectedly; investigate before claiming deployment.' }
} catch {
  $deploymentError = $_
  try {
    # Only newly created, exactly identified owned processes may be stopped.
    if ($launchTime) {
      $newGateway = Get-OwnedListener -Port 8790 -Script $gatewayPath -Executable $nodePath -AllowAbsent
      if ($newGateway) {
        if ($newGateway.CreationDate -lt $launchTime) { throw 'Rollback found an older gateway; refusing to stop it.' }
        Stop-OwnedProcess -Expected $newGateway -Port 8790 -Script $gatewayPath -Executable $nodePath
      }
      $newBackend = Get-OwnedListener -Port 8977 -Script $backendPath -Executable $Python -AllowAbsent
      if ($newBackend) {
        if ($newBackend.CreationDate -lt $launchTime) { throw 'Rollback found an older backend; refusing to stop it.' }
        Assert-PlannerIdle
        Stop-OwnedProcess -Expected $newBackend -Port 8977 -Script $backendPath -Executable $Python
      }
    }
    if ($changedFiles) { Restore-Deployment $rollbackPath $gatewayPath $startPath $plannerPath $assetState }
    if ($stoppedGateway) {
      # An originally standalone planner also needs restoration; start it before
      # the old gateway so its supervisor cannot race another backend launch.
      if ($stoppedBackend) {
        $env:BROWSER_NONE = '1'
        $env:PRACTICE_SKIP_GIT_PULL = '1'
        $env:PRACTICE_PORT = '8977'
        $null = Start-Process -FilePath $Python -ArgumentList ('"{0}" --no-browser' -f $backendPath) -WorkingDirectory $sourceRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimePath 'planner-stdout.log') -RedirectStandardError (Join-Path $runtimePath 'planner-stderr.log') -PassThru
        $null = Wait-Gateway -App 'practice-room' -Port 8977
      }
      & $startPath
      $null = Wait-Gateway -App 'music-practice' -Path '/healthz'
    }
  } catch {
    throw "Deployment failed: $deploymentError Rollback needs attention: $_ Saved files are in $rollbackPath."
  }
  throw "Deployment failed; the previous gateway and assets were restored: $deploymentError"
}
@{date=(Get-Date).ToString('o');source=$sourceRoot;gateway=(Get-FileHash $gatewayPath).Hash;route='https://lox-pc.tail89d19b.ts.net:10000/';health=$health} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runtimePath 'planner-deployment.json') -Encoding utf8
Write-Output 'Planner is running on the existing phone route. Verify a rendered connected browser session before delivery.'
