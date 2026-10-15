# Ensure winremote-mcp on this Windows host: Python, package, config (tier3+auth),
# firewall 8090 (Domain/Private), autostart bat+Startup, listen 0.0.0.0:8090.
# Does NOT touch port 6003 (1c-local). Does NOT commit secrets.
#
# Usage (elevated recommended for firewall/task):
#   powershell -NoProfile -ExecutionPolicy Bypass -File ensure-winremote.ps1
#   powershell ... -File ensure-winremote.ps1 -AuthKey "existing-key"   # keep key
#   powershell ... -File ensure-winremote.ps1 -Restart

[CmdletBinding()]
param(
    [string]$AuthKey = "",
    [string]$HostBind = "0.0.0.0",
    [int]$Port = 8090,
    [switch]$Restart,
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
$PythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"
$PythonExe = Join-Path $PythonRoot "python.exe"
$ScriptsDir = Join-Path $PythonRoot "Scripts"
$ConfigDir = Join-Path $env:USERPROFILE ".config\winremote"
$ConfigPath = Join-Path $ConfigDir "winremote.toml"
$BatPath = Join-Path $env:USERPROFILE "winremote-mcp-start.bat"
$StartupLnk = Join-Path ([Environment]::GetFolderPath("Startup")) "WinRemote MCP.lnk"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StarterPySrc = Join-Path $ScriptDir "start-winremote-detached.py"
$StarterPyDst = Join-Path $ConfigDir "start-winremote-detached.py"
$StarterPs1Dst = Join-Path $ConfigDir "start-winremote-detached.ps1"

function Ensure-Python312 {
    if (Test-Path $PythonExe) {
        Write-Host "OK: Python at $PythonExe"
        return
    }
    Write-Host "Installing Python 3.12 via winget (user scope)..."
    winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --scope user -e --disable-interactivity
    if (-not (Test-Path $PythonExe)) {
        throw "Python 3.12 not found after winget install: $PythonExe"
    }
}

function Ensure-Package {
    $env:Path = "$PythonRoot;$ScriptsDir;$env:Path"
    & $PythonExe -m pip install --upgrade pip | Out-Null
    & $PythonExe -m pip install --upgrade winremote-mcp
    Write-Host "OK: winremote-mcp installed"
}

function New-AuthKey {
    & $PythonExe -c "import secrets; print(secrets.token_urlsafe(48))"
}

function Ensure-Config {
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    $key = $AuthKey
    if (-not $key -and (Test-Path $ConfigPath)) {
        $line = Select-String -Path $ConfigPath -Pattern '^\s*auth_key\s*=' | Select-Object -First 1
        if ($line) {
            $key = ($line.Line -split '=', 2)[1].Trim().Trim('"').Trim("'")
        }
    }
    if (-not $key) {
        $key = New-AuthKey
        Write-Host "Generated new auth_key (save for Mac mcp.json Bearer)"
    } else {
        Write-Host "Using existing/provided auth_key"
    }

    $toml = @"
[server]
host = "$HostBind"
port = $Port
auth_key = "$key"

[security]
enable_tier3 = true
"@
    [System.IO.File]::WriteAllText($ConfigPath, $toml)
    try {
        icacls $ConfigPath /inheritance:r /grant:r "${env:USERNAME}:(R,W)" | Out-Null
    } catch {
        Write-Warning "icacls failed: $($_.Exception.Message)"
    }
    Write-Host "OK: config $ConfigPath (enable_tier3=true)"
    return $key
}

function Ensure-DetachedStarter {
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    if (-not (Test-Path $StarterPySrc)) {
        throw "Missing starter source: $StarterPySrc"
    }
    Copy-Item -Force $StarterPySrc $StarterPyDst
    $ps1 = @"
`$ErrorActionPreference = 'Stop'
& '$PythonExe' '$StarterPyDst'
exit `$LASTEXITCODE
"@
    [System.IO.File]::WriteAllText($StarterPs1Dst, $ps1)
    Write-Host "OK: detached starter $StarterPyDst"
}

function Ensure-Bat {
    $bat = @"
@echo off
set PYTHONIOENCODING=utf-8
"$PythonExe" "$StarterPyDst"
"@
    [System.IO.File]::WriteAllText($BatPath, $bat)
    Write-Host "OK: $BatPath"
}

function Ensure-StartupShortcut {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($StartupLnk)
    $lnk.TargetPath = $PythonExe
    $lnk.Arguments = "`"$StarterPyDst`""
    $lnk.WorkingDirectory = $env:USERPROFILE
    $lnk.WindowStyle = 7
    $lnk.Description = "WinRemote MCP (LAN :$Port + auth + tier3 + breakaway)"
    $lnk.Save()
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
        -Name "WinRemoteMCP" -Value "`"$PythonExe`" `"$StarterPyDst`""
    Write-Host "OK: Startup + HKCU Run"
}

function Ensure-Firewall {
    if ($SkipFirewall) {
        Write-Host "Skip firewall"
        return
    }
    $ruleName = "WinRemote MCP $Port"
    $script = @"
`$ErrorActionPreference = 'Continue'
`$ruleName = '$ruleName'
`$port = $Port
try {
  if (-not (Get-NetFirewallRule -DisplayName `$ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName `$ruleName -Direction Inbound -Protocol TCP -LocalPort `$port -Action Allow -Profile Private,Domain | Out-Null
    'FW_CREATED'
  } else {
    Enable-NetFirewallRule -DisplayName `$ruleName -ErrorAction SilentlyContinue
    'FW_EXISTS'
  }
} catch {
  netsh advfirewall firewall add rule name=`"`$ruleName`" dir=in action=allow protocol=TCP localport=`$port profile=private,domain | Out-Null
  'FW_NETSH'
}
"@
    # Never write elevated helpers under %TEMP% - Kaspersky PDM quarantines GUID/Temp .ps1
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    $fwScript = Join-Path $ConfigDir "ensure-firewall-elevated.ps1"
    [System.IO.File]::WriteAllText($fwScript, $script)
    try {
        $p = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $fwScript) -Verb RunAs -Wait -PassThru
        Write-Host "Firewall elevated exit=$($p.ExitCode)"
    } catch {
        Write-Warning "Firewall elevation failed (add rule manually for TCP $Port Domain/Private): $($_.Exception.Message)"
    }
}

function Stop-ListenerIfNeeded {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        Write-Host "Stopping PID $($c.OwningProcess) on :$Port"
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

function Start-Server {
    $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listening -and -not $Restart) {
        Write-Host "Already listening on :$Port PID=$($listening.OwningProcess)"
        return
    }
    # Do not touch 6003. Always start via breakaway starter (avoids Cursor Job Object / WinError 5).
    $still6003 = Get-NetTCPConnection -LocalPort 6003 -State Listen -ErrorAction SilentlyContinue
    Write-Host "1c-local :6003 listening=$([bool]$still6003)"
    & $PythonExe $StarterPyDst
    if ($LASTEXITCODE -ne 0) {
        throw "start-winremote-detached.py failed exit=$LASTEXITCODE"
    }
    Start-Sleep -Seconds 1
}

function Test-Health {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 10
        Write-Host "health $($r.StatusCode) $($r.Content)"
    } catch {
        Write-Warning "health failed: $($_.Exception.Message)"
    }
}

Ensure-Python312
Ensure-Package
$keyUsed = Ensure-Config
Ensure-DetachedStarter
Ensure-Bat
Ensure-StartupShortcut
Ensure-Firewall
Start-Server
Test-Health

Write-Host ""
Write-Host "=== Mac ~/.cursor/mcp.json fragment (auth_key not printed twice if long) ==="
Write-Host @"
`"winremote`": {
  `"url`": `"http://<WINDOWS_LAN_IP>:$Port/mcp`",
  `"headers`": {
    `"Authorization`": `"Bearer $keyUsed`"
  }
}
"@
Write-Host "Keep 1c-local on :6003 separately. Do not commit auth_key."
