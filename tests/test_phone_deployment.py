"""Small deployment fixtures; never invoke the live deploy entry point."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
if not NODE:
    bundled = Path(os.environ.get("LOCALAPPDATA", "")) / "MusicPracticeHomeServer/runtime/node.exe"
    NODE = str(bundled) if bundled.exists() else None
POWERSHELL = shutil.which("powershell")


class GatewayTests(unittest.TestCase):
    @unittest.skipUnless(NODE, "Node is unavailable")
    def test_failed_spawn_can_retry_without_old_close_clearing_new_child(self):
        source = (ROOT / "scripts/music-home-server.mjs").read_text()
        function = source[source.index("async function ensurePlanner()"):source.index("const contentTypes")]
        code = """
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {resolve} from 'node:path';
let plannerChild=null, plannerStarting=false, plannerPort=1, calls=0;
process.env.PRACTICE_SERVER_PATH='C:/synthetic/server.py';
process.env.PRACTICE_PYTHON='synthetic-python';
const fetch=async()=>{throw {cause:{code:'ECONNREFUSED'}}};
const spawn=()=>{calls++;return new EventEmitter()};
""" + function + """
await Promise.all([ensurePlanner(),ensurePlanner()]);
assert.equal(calls,1);
const first=plannerChild;
first.emit('error',new Error('ENOENT'));
assert.equal(plannerChild,null);
await ensurePlanner();
assert.equal(calls,2);
const second=plannerChild;
first.emit('close',-1);
assert.equal(plannerChild,second);
second.emit('exit',1);
await ensurePlanner();
assert.equal(calls,3);
"""
        result = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(NODE, "Node is unavailable")
    def test_static_roots_and_manifest_type_preserve_legacy_files(self):
        source = (ROOT / "scripts/music-home-server.mjs").read_text()
        functions = source[source.index("const contentTypes"):source.index("function proxyTo")]
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            site = base / "site"
            site.mkdir()
            (site / "index.html").write_text("legacy index")
            (site / "home.html").write_text("legacy home")
            sibling = base / "site-other"
            sibling.mkdir()
            (sibling / "secret.txt").write_text("outside")
            code = """
import assert from 'node:assert/strict';
import {existsSync,statSync} from 'node:fs';
import {join,resolve,normalize,sep} from 'node:path';
""" + functions + "\nconst root=" + json.dumps(str(site)) + ";\n" + r"""
assert.match(contentTypes['.webmanifest'], /^application\/manifest\+json/);
assert.equal(resolveRequest('/home',root),join(root,'home.html'));
assert.equal(resolveRequest('/index.html',root),join(root,'index.html'));
assert.equal(resolveRequest('/%zz',root),null);
for(const p of ['/../site-other/secret.txt','/%2e%2e%5csite-other%5csecret.txt']) {
  assert.notEqual(resolveRequest(p,root),resolve(root,'../site-other/secret.txt'));
}
"""
            result = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is unavailable")
class DeploymentTests(unittest.TestCase):
    def run_powershell(self, body):
        # Load function ASTs only. The deployment main entry point cannot run.
        prefix = r"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:DEPLOY_SOURCE,[ref]$tokens,[ref]$errors)
if ($errors) { throw ($errors | Out-String) }
foreach ($fn in $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
  Invoke-Expression $fn.Extent.Text
}
function Assert-True($Value,$Label) { if (!$Value) {throw $Label} }
"""
        env = dict(os.environ, DEPLOY_SOURCE=str(ROOT / "scripts/deploy-phone.ps1"))
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "test.ps1"
            script.write_text(prefix + body, encoding="utf-8")
            result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_owner_and_recycled_pid_are_rejected(self):
        self.run_powershell(r"""
function Get-NetTCPConnection { [pscustomobject]@{LocalAddress='127.0.0.1';OwningProcess=123} }
$script:owner=[pscustomobject]@{ProcessId=123;ExecutablePath='C:\node.exe';CommandLine='"C:\node.exe" "C:\gateway.mjs"';CreationDate=[datetime]'2026-09-22'}
function Get-CimInstance { $script:owner }
$known=Get-OwnedListener -Port 1 -Script 'C:\gateway.mjs' -Executable 'C:\node.exe'
Assert-True ($known.ProcessId -eq 123) 'Expected owner was rejected'
$script:owner=$known.PSObject.Copy(); $script:owner.CreationDate=$known.CreationDate.AddSeconds(1)
$rejected=$false
try {Assert-SameProcess $known $script:owner} catch {$rejected=$true}
Assert-True $rejected 'Recycled PID was accepted'
$script:owner.CommandLine='"C:\node.exe" "C:\gateway.mjs.other"'
$rejected=$false
try {Get-OwnedListener -Port 1 -Script 'C:\gateway.mjs' -Executable 'C:\node.exe'} catch {$rejected=$true}
Assert-True $rejected 'Substring script match was accepted'
""")

    def test_launch_failure_restores_files_and_restarts_previous_gateway(self):
        body = r"""
$tempRoot=Join-Path $PSScriptRoot 'fixture'
$rollbackPath=Join-Path $tempRoot 'rollback'
$plannerPath=Join-Path $tempRoot 'planner'
$sourceRoot=Join-Path $tempRoot 'source'
$gatewayPath=Join-Path $tempRoot 'gateway.mjs'
$startPath=Join-Path $tempRoot 'start.ps1'
$nodePath='synthetic-node'; $Python='synthetic-python'; $backendPath='synthetic-server'
$runtimePath=$tempRoot
New-Item -ItemType Directory -Force -Path $rollbackPath,$plannerPath,$sourceRoot,(Join-Path $rollbackPath 'planner') | Out-Null
'old gateway' | Set-Content $gatewayPath
'$global:oldRestarted=$true' | Set-Content $startPath
'old index' | Set-Content (Join-Path $plannerPath 'index.html')
'preserved score' | Set-Content (Join-Path $plannerPath 'score.pdf')
Copy-Item $gatewayPath (Join-Path $rollbackPath 'music-home-server.mjs')
Copy-Item $startPath (Join-Path $rollbackPath 'start.ps1')
Copy-Item (Join-Path $plannerPath 'index.html') (Join-Path $rollbackPath 'planner/index.html')
'new index' | Set-Content (Join-Path $sourceRoot 'index.html')
'new script' | Set-Content (Join-Path $sourceRoot 'app.js')
$assets=@('index.html','app.js'); $assetState=@{'index.html'=$true;'app.js'=$false}
$backendProcess=$null; $gatewayProcess=[pscustomobject]@{ProcessId=1}
$config=''; $startText='throw "synthetic launch failure"'
$global:oldRestarted=$false; $global:stops=0
function Stop-OwnedProcess {$global:stops++}
function Get-OwnedListener {return $null}
function Wait-Gateway {return @{app='music-practice'}}
function Assert-PlannerIdle {}
function Start-Process {$global:backendRestored=$true}
# Source copy is mocked only for the gateway file; all asset/rollback I/O is real.
function Copy-Item {
  param($LiteralPath,$Destination)
  if ($LiteralPath -eq (Join-Path $PSScriptRoot 'music-home-server.mjs')) {'new gateway' | Set-Content $Destination}
  else {Microsoft.PowerShell.Management\Copy-Item -LiteralPath $LiteralPath -Destination $Destination}
}
$text=[IO.File]::ReadAllText($env:DEPLOY_SOURCE)
$start=$text.IndexOf('$stoppedGateway = $false')
$end=$text.IndexOf('@{date=(Get-Date)', $start)
$failure=$null
try {Invoke-Expression $text.Substring($start,$end-$start)} catch {$failure=$_}
Assert-True ($failure -like '*previous gateway and assets were restored*') "Wrong failure: $failure"
Assert-True $global:oldRestarted 'Old gateway was not restarted'
Assert-True ($global:stops -eq 1) 'Unexpected process stop'
Assert-True ((Get-Content $gatewayPath) -eq 'old gateway') 'Gateway not restored'
Assert-True ((Get-Content (Join-Path $plannerPath 'index.html')) -eq 'old index') 'Index not restored'
Assert-True (!(Test-Path (Join-Path $plannerPath 'app.js'))) 'New asset left behind'
Assert-True ((Get-Content (Join-Path $plannerPath 'score.pdf')) -eq 'preserved score') 'Unrelated asset changed'
"""
        self.run_powershell(body)
        with_backend = body.replace("$backendProcess=$null;", "$backendProcess=[pscustomobject]@{ProcessId=2};")
        with_backend = with_backend.replace("function Get-OwnedListener {return $null}", "function Get-OwnedListener {param([int]$Port) if ($Port -eq 8977) {return $backendProcess} return $null}")
        with_backend = with_backend.replace("$global:stops -eq 1", "$global:stops -eq 2")
        self.run_powershell(with_backend + "\nAssert-True $global:backendRestored 'Standalone backend was not restored'\n")

    def test_supervised_child_exit_is_accepted_without_stopping_a_successor(self):
        self.run_powershell(r"""
$original=[pscustomobject]@{ProcessId=123;CreationDate=[datetime]'2026-09-22';CommandLine='python server.py'}
$script:remaining=$null; $script:process=$null; $script:stops=0
function Get-OwnedListener {$script:remaining}
function Get-CimInstance {$script:process}
function Stop-OwnedProcess {$script:stops++}
function Assert-PlannerIdle {}
Stop-RemainingPlanner -Expected $original -Script 'server.py' -Executable 'python'
Assert-True ($script:stops -eq 0) 'An exited child caused a process stop'
$script:process=$original
$rejected=$false
try {Stop-RemainingPlanner -Expected $original -Script 'server.py' -Executable 'python'} catch {$rejected=$true}
Assert-True $rejected 'A live child without a listener was ignored'
$script:remaining=$original.PSObject.Copy(); $script:remaining.CreationDate=$original.CreationDate.AddSeconds(1)
$rejected=$false
try {Stop-RemainingPlanner -Expected $original -Script 'server.py' -Executable 'python'} catch {$rejected=$true}
Assert-True $rejected 'A replacement process was accepted'
Assert-True ($script:stops -eq 0) 'A replacement process was stopped'
""")


if __name__ == "__main__":
    unittest.main()
