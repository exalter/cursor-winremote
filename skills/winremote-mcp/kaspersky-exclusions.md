# Kaspersky: exclusions for winremote (hand to IT / AV admin)

Goal: avoid `PDM:Trojan.Win32.Generic` / `HEUR:Trojan.PowerShell.*` and
not wipe autostart (bat / lnk / scheduled task / toml).

Replace `%USERNAME%` / paths with the Windows host account that runs winremote.

## Permanent paths (PDM + Application Control)

1. `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`
2. `%LOCALAPPDATA%\Programs\Python\Python312\Scripts\winremote-mcp.exe`
3. `%USERPROFILE%\.config\winremote\` (entire directory)
4. `%USERPROFILE%\.cursor\skills\`
5. `%USERPROFILE%\Documents\cursor\skills\` (if you keep a clone / junction there)
6. `%USERPROFILE%\Documents\GitHub\cursor-winremote\` (this repo, if used on the host)
7. `%USERPROFILE%\winremote-mcp-start.bat`
8. `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\WinRemote MCP.lnk`

Preferably: `powershell.exe` as a **child** of item 1 (python winremote).

## Do not restore / do not "treat"

`%TEMP%\ps-script-*.ps1` and GUID `.ps1` - ephemeral Cursor files. Quarantine
often has **no content**. Canonical tooling does **not** live there.

## Policy for agents / operators

- All permanent code only under the paths above.
- Start winremote only via `start-winremote-detached.py` (not Temp, not bare
  `Start-Process` from Cursor).
- In a KES alert - do not "Treat and reboot" on python / `.config\winremote` /
  skills / bat / lnk.
