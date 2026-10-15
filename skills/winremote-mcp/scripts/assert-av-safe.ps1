# Preflight: winremote layout is AV-safe (no tooling in Temp) and host is out of Job.
# Exit 0 = OK; non-zero = fix before relying on Shell from Mac.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File assert-av-safe.ps1
#   powershell ... -File assert-av-safe.ps1 -RestartIfInJob

[CmdletBinding()]
param(
    [switch]$RestartIfInJob
)

$ErrorActionPreference = "Continue"
$failed = 0
function Ok($m) { Write-Host "OK  $m" }
function Bad($m) { Write-Host "BAD $m"; $script:failed++ }

$python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
$configDir = Join-Path $env:USERPROFILE ".config\winremote"
$starter = Join-Path $configDir "start-winremote-detached.py"
$toml = Join-Path $configDir "winremote.toml"
$bat = Join-Path $env:USERPROFILE "winremote-mcp-start.bat"
$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "WinRemote MCP.lnk"
$skillStarter = Join-Path $env:USERPROFILE "Documents\cursor\skills\winremote-mcp\scripts\start-winremote-detached.py"

foreach ($p in @($python, $starter, $toml, $bat, $lnk, $skillStarter)) {
    if (Test-Path -LiteralPath $p) { Ok $p } else { Bad "missing $p" }
}

# Permanent scripts must NOT live only under Temp
$badTemp = Get-ChildItem $env:TEMP -Filter "ensure-winremote*.ps1" -EA SilentlyContinue
$badTemp2 = Get-ChildItem $env:TEMP -Filter "reg-winremote*.ps1" -EA SilentlyContinue
$badTemp3 = Get-ChildItem $env:TEMP -Filter "WinRemoteMCP.xml" -EA SilentlyContinue
foreach ($f in @($badTemp) + @($badTemp2) + @($badTemp3)) {
    if ($f) {
        Bad "leftover in Temp (delete or move): $($f.FullName)"
        Remove-Item -LiteralPath $f.FullName -Force -EA SilentlyContinue
    }
}

# HKCU Run
$run = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -EA SilentlyContinue).WinRemoteMCP
if ($run -and ($run -match 'start-winremote-detached\.py')) { Ok "HKCU Run -> starter" }
else { Bad "HKCU Run WinRemoteMCP missing or not pointing at starter" }

# Listener + Job
$conn = Get-NetTCPConnection -LocalPort 8090 -State Listen -EA SilentlyContinue | Select-Object -First 1
if (-not $conn) {
    Bad "nothing listening on :8090"
    if ($RestartIfInJob -and (Test-Path $starter)) {
        Write-Host "Starting detached..."
        & $python $starter
        Start-Sleep -Seconds 3
        $conn = Get-NetTCPConnection -LocalPort 8090 -State Listen -EA SilentlyContinue | Select-Object -First 1
    }
}
if ($conn) {
    $pidW = $conn.OwningProcess
    Ok "listen :8090 PID=$pidW"
    $jobOut = & $python -c "import ctypes,ctypes.wintypes as w,sys; pid=int(sys.argv[1]); h=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid); j=w.BOOL(); ctypes.windll.kernel32.IsProcessInJob(h,None,ctypes.byref(j)); print(int(bool(j.value))); ctypes.windll.kernel32.CloseHandle(h)" $pidW 2>$null
    if ($jobOut -eq "0") {
        Ok "IsProcessInJob=False"
    } else {
        Bad "IsProcessInJob=True (Cursor Job) - Shell may get WinError 5"
        if ($RestartIfInJob) {
            Write-Host "Restarting via breakaway starter..."
            & $python $starter
            Start-Sleep -Seconds 3
            $pidW2 = (Get-NetTCPConnection -LocalPort 8090 -State Listen -EA SilentlyContinue | Select-Object -First 1).OwningProcess
            $jobOut2 = & $python -c "import ctypes,ctypes.wintypes as w,sys; pid=int(sys.argv[1]); h=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid); j=w.BOOL(); ctypes.windll.kernel32.IsProcessInJob(h,None,ctypes.byref(j)); print(int(bool(j.value))); ctypes.windll.kernel32.CloseHandle(h)" $pidW2 2>$null
            if ($jobOut2 -eq "0") { Ok "after restart IsProcessInJob=False PID=$pidW2" }
            else { Bad "still in Job after restart" }
        }
    }
    try {
        $h = (Invoke-WebRequest "http://127.0.0.1:8090/health" -UseBasicParsing -TimeoutSec 5).Content
        Ok "health $h"
    } catch { Bad "health failed: $($_.Exception.Message)" }
}

Write-Host ""
Write-Host "Exclusions for admin: skills/winremote-mcp/kaspersky-exclusions.md"
Write-Host "Also: Downloads\kaspersky-exclusions-winremote.md"
if ($failed -gt 0) {
    Write-Host "FAILED checks=$failed"
    exit 1
}
Write-Host "ALL OK"
exit 0
