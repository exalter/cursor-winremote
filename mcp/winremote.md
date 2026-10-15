# winremote

Remote HTTP MCP to control a **Windows host** from Cursor on Mac
(PowerShell, files, processes, screen/click GUI). Server package:
[winremote-mcp](https://github.com/dddabtc/winremote-mcp) (PyPI).

Chat namespace: `winremote` / `user-winremote` (depends on the key in `mcp.json`).
Agent skill: **`winremote-mcp`** (`skills/winremote-mcp/`, script
`ensure-winremote.ps1`).

## Endpoint (LAN)

Replace `<WINDOWS_LAN_IP>` with the host address on your network.

- MCP: `http://<WINDOWS_LAN_IP>:8090/mcp`
- Health: `http://<WINDOWS_LAN_IP>:8090/health` → `{"status":"ok",...}`

Port **8090** is winremote only. If you also run other local bridges (e.g.
`1c-local` on `:6003`), do not bind or restart them together with winremote.

## Cursor client (Mac)

```json
"winremote": {
  "url": "http://<WINDOWS_LAN_IP>:8090/mcp",
  "headers": {
    "Authorization": "Bearer <WINREMOTE_AUTH_KEY>"
  }
}
```

Put the real Bearer from `%USERPROFILE%\.config\winremote\winremote.toml` on
Windows (`server.auth_key`). Never commit the secret.

Reload MCP after URL/key changes.

## Windows host install

Short form (full script: `skills/winremote-mcp/scripts/ensure-winremote.ps1`):

1. Python 3.11+ in PATH (`winget install Python.Python.3.12 --scope user`).
2. `python -m pip install --upgrade pip winremote-mcp`
3. Config `%USERPROFILE%\.config\winremote\winremote.toml` — see
   [`winremote.toml.example`](winremote.toml.example):
   - `host = "0.0.0.0"`, `port = 8090`, long `auth_key`
   - **`[security] enable_tier3 = true`** — otherwise remote has no Shell /
     FileRead / FileWrite / KillProcess (only tier1+tier2: Snapshot, Click,
     FileList, …)
4. Always require auth: without `auth_key` on `0.0.0.0` the server refuses
   (and will not expose tier3).
5. Firewall inbound TCP **8090**, Domain + Private (not Public / not internet).
6. Autostart via `start-winremote-detached.py` (Job breakaway): HKCU Run,
   Startup, scheduled task **WinRemoteMCP** At logon — not bare
   `Start-Process` from Cursor.

Canonical start (against WinError 5):

```text
python %USERPROFILE%\.config\winremote\start-winremote-detached.py
```

## Tool tiers

| Tier | Default | Examples |
|------|---------|----------|
| 1 | on | Snapshot, FileList, ListProcesses, GetSystemInfo |
| 2 | on | Click, Type, Shortcut |
| 3 | **off** | **Shell**, FileRead/Write/Upload/Download, KillProcess, RegWrite, … |

Mac agents that need PowerShell and files require `enable_tier3 = true` (and
Bearer). Leave exclude empty unless you have a separate security reason.

After fix, `tools/list` should include `Shell`, `FileRead`, `KillProcess`.

## Smoke

```bash
curl -sS http://<WINDOWS_LAN_IP>:8090/health
curl -sS -o /dev/null -w "%{http_code}\n" http://<WINDOWS_LAN_IP>:8090/mcp
# without Bearer → 401
```

MCP Shell: `hostname` → Windows host name.

## HARD: WinError 5 (Access denied) on Shell/App

Two common causes (can stack):

1. **Job Object** — python on `:8090` was started from a Cursor agent shell
   (`IsProcessInJob=True`). Fix:
   `skills/winremote-mcp/scripts/start-winremote-detached.py`
   (`CREATE_BREAKAWAY_FROM_JOB`). After fix `IsProcessInJob=False`.
2. **Kaspersky PDM** — alert `PDM:Trojan.Win32.Generic` on GUID `.ps1` in Temp /
   behavior analysis. Do not click "Treat and reboot"; set exclusions once —
   [`skills/winremote-mcp/kaspersky-exclusions.md`](../skills/winremote-mcp/kaspersky-exclusions.md).
   Do not put tooling in `%TEMP%`. Preflight:
   `assert-av-safe.ps1 -RestartIfInJob`.

Do not paper over with Win+R.

## Shell from Mac: antipatterns (60s timeout)

Default Shell tool timeout is **60 seconds**. A long command fails the call
and can hang the whole winremote (`/health` stops answering) — restart only
the `:8090` process.

| Don't | Do |
|-------|----|
| `Get-ChildItem $env:USERPROFILE -Recurse` | `Test-Path` on **known** paths |
| Heavy walk of `AppData\Local\Cursor\AgentStores\...` | Canonical skills: `%USERPROFILE%\.cursor\skills\...` |
| One Shell with "find everything + health + netstat" | Short commands; ports via `netstat -ano \| findstr ":8090"` |
| Rely on default 60s for long installers / Docker | Explicit `timeout` 120+ **or** several short calls |

## What this is / is not

- **winremote** — Windows OS (shell, FS, GUI) for an agent on Mac.
- Companion skill **`win-ui-find`** — UIA/OCR click without VLM (same host).
- Other product bridges (e.g. 1C MCP on another port) are separate services.

## Limits

- Do not publish 8090 to the internet; LAN / this host only.
- Do not listen on `0.0.0.0` without `auth_key`.
- Do not commit a real `auth_key`.
