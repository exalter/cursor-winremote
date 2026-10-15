---
name: winremote-mcp
description: >-
  Remote Windows MCP (PyPI winremote-mcp) for Mac agents: Shell/FS/GUI on a
  LAN host :8090 + Bearer, enable_tier3 (else no Shell/FileRead/KillProcess).
  WinError 5 = Job Object and/or Kaspersky PDM. AV hygiene: tooling only under
  .config/winremote and skills (not Temp); start-winremote-detached.py;
  KES exclusions (kaspersky-exclusions.md); assert-av-safe.ps1. Use when
  winremote, WinError 5, Kaspersky, PDM, Access denied, IsProcessInJob,
  enable_tier3.
---

# winremote-mcp (remote Windows)

MCP notes and toml example: [`mcp/winremote.md`](../../mcp/winremote.md),
[`mcp/winremote.toml.example`](../../mcp/winremote.toml.example).

The **Windows host** runs the server; Cursor on Mac connects over HTTP.

| | |
|---|---|
| MCP | `http://<WINDOWS_LAN_IP>:8090/mcp` |
| Health | `http://<WINDOWS_LAN_IP>:8090/health` |
| Auth | `Authorization: Bearer <auth_key>` from `~/.config/winremote/winremote.toml` |

## Mac `~/.cursor/mcp.json`

```json
"winremote": {
  "url": "http://<WINDOWS_LAN_IP>:8090/mcp",
  "headers": {
    "Authorization": "Bearer <WINREMOTE_AUTH_KEY>"
  }
}
```

Do not commit the secret.

## HARD: WinError 5 on Shell / App

Symptom: `CreateProcess ... WinError 5` (Access denied) on Shell/App/
TaskCreate, while FileRead/ListProcesses/KillProcess often still work.

**Cause:** python winremote was started from a Cursor agent shell and landed in
a **Windows Job Object** (`IsProcessInJob=True`). SessionId may still match
explorer — this is not Session 0.

**Fix (not Win+R as the main path):**

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" `
  "$env:USERPROFILE\.config\winremote\start-winremote-detached.py"
# or from this repo:
.\skills\winremote-mcp\scripts\start-winremote-detached.py
```

After restart: listener `:8090` has `IsProcessInJob` = **False**, SessionId =
explorer. `ReconnectSession`/`tscon` is a separate story (needs rights); do not
confuse with Shell CreateProcess.

## HARD: stay out of Kaspersky again

On hosts with **KES** (`AVP.KES.*`, `klnagent`), PDM/HEUR may block PS in Temp
and sometimes bat/lnk/xml autostart → WinError 5 / wiped autostart.

### Required rules (agent + human)

1. **Permanent code only** under allowlist paths (see
   [`kaspersky-exclusions.md`](kaspersky-exclusions.md)):
   `.config\winremote\`, `skills\`, python312, bat, Startup lnk.
2. **Do not** put working `.ps1` / starters / task xml in `%TEMP%`
   (`ps-script-*.ps1`, GUID `.ps1`, `ensure-winremote-*.ps1` in Temp).
3. Start/restart winremote **only** via `start-winremote-detached.py`
   (python), not bare `Start-Process` from Cursor (Job Object + PDM).
4. From Mac, drive Windows via **winremote Shell** (HTTP), not a local Cursor
   Shell on Windows that writes `%TEMP%\ps-script-*.ps1`.
5. In a KES alert — do **not** "Treat and reboot" on allowlist paths. Do not
   restore ephemeral Temp `.ps1` from quarantine.
6. KES (PDM) exclusions — **once**, via admin; text for IT:
   `kaspersky-exclusions.md`.

### Preflight before long work

```powershell
.\skills\winremote-mcp\scripts\assert-av-safe.ps1 -RestartIfInJob
# exit 0 = IsProcessInJob False, files present, health ok
```

## Install / repair on Windows

```powershell
# from this repo clone:
.\skills\winremote-mcp\scripts\ensure-winremote.ps1
# restart with the same key (via breakaway starter):
.\skills\winremote-mcp\scripts\ensure-winremote.ps1 -Restart
```

Script: Python 3.12, `pip install winremote-mcp`, toml with
`enable_tier3 = true`, bat + Startup + HKCU Run + task **WinRemoteMCP**
(At logon, InteractiveToken), firewall 8090 (Domain/Private), start via
`start-winremote-detached.py` (`CREATE_BREAKAWAY_FROM_JOB`).

Without **`enable_tier3`**, remote has no: `Shell`, `FileRead`/`FileWrite`/
`FileUpload`/`FileDownload`, `KillProcess` (only Snapshot/Click/FileList/…).

## HARD: short Shell commands from Mac

Default Shell timeout = **60s**. `Get-ChildItem $env:USERPROFILE -Recurse`
walks AgentStores and **times out**; sometimes the whole winremote stops
answering `/health` — restart only the `:8090` process.

### Canonical skill paths on Windows

```text
%USERPROFILE%\.cursor\skills\<name>\scripts\...
```

Do **not** recurse the profile and do **not** walk
`AppData\Local\Cursor\AgentStores`.

Companion UI skill in this repo: **`win-ui-find`**.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/ensure-winremote.ps1` | Python, package, toml tier3+auth, firewall, autostart, Restart |
| `scripts/start-winremote-detached.py` | Start outside Job Object (canonical vs WinError 5) |
| `scripts/start-winremote-detached.ps1` | PowerShell wrapper → python starter |
| `scripts/assert-av-safe.ps1` | Preflight: paths, HKCU, Job, health; `-RestartIfInJob` |
| `kaspersky-exclusions.md` | Exclusion text for AV / IT admin |

On the host, working copies live under
`%USERPROFILE%\.config\winremote\start-winremote-detached.py`.

## Limits

- Do not publish 8090 to the internet; LAN only.
- Do not listen on `0.0.0.0` without `auth_key`.
- Do not commit a real Bearer.
