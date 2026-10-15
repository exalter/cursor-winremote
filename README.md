# cursor-winremote

Cursor skills and host setup for **[winremote-mcp](https://github.com/dddabtc/winremote-mcp)** — remote Windows control (Shell, FS, GUI) from a Mac agent over LAN HTTP MCP.

This repo is **not** the MCP server itself (that lives on PyPI / upstream). It packages:

| Path | What |
|------|------|
| [`skills/winremote-mcp/`](skills/winremote-mcp/) | Agent skill: install, Job Object / WinError 5, Kaspersky hygiene, preflight |
| [`skills/win-ui-find/`](skills/win-ui-find/) | Agent skill: find & act on Windows UI via UIA (+ OCR fallback), no VLM |
| [`mcp/`](mcp/) | Cursor `mcp.json` notes + `winremote.toml` example |

## Quick start

### 1. Windows host

```powershell
# clone somewhere durable (not %TEMP%)
cd $env:USERPROFILE\Documents
git clone https://github.com/<YOU>/cursor-winremote.git
cd cursor-winremote\skills\winremote-mcp\scripts
powershell -NoProfile -ExecutionPolicy Bypass -File .\ensure-winremote.ps1
```

Script installs Python 3.12 (winget), `winremote-mcp`, writes
`%USERPROFILE%\.config\winremote\winremote.toml` with **`enable_tier3 = true`**,
opens firewall TCP **8090** (Domain/Private), sets autostart via
`start-winremote-detached.py` (breaks out of Windows Job Object).

Save the printed `auth_key` for the Mac client.

Preflight:

```powershell
.\assert-av-safe.ps1 -RestartIfInJob
```

Health: `http://127.0.0.1:8090/health`

### 2. Cursor on Mac (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "winremote": {
      "url": "http://<WINDOWS_LAN_IP>:8090/mcp",
      "headers": {
        "Authorization": "Bearer <WINREMOTE_AUTH_KEY>"
      }
    }
  }
}
```

Reload MCP. Smoke: tool `Shell` → `hostname`.

### 3. Install skills into Cursor

Copy (or symlink) skill folders into your Cursor skills directory, e.g.:

```bash
# macOS / Linux example
cp -R skills/winremote-mcp skills/win-ui-find ~/.cursor/skills/
```

Or point Agent Store / project `.cursor/skills` at this clone. New chat after install so the agent picks up skill descriptions.

Optional UI skill on the Windows host (venv once):

```powershell
cd skills\win-ui-find\scripts
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install winocr   # or rapidocr-onnxruntime
.\.venv\Scripts\python.exe probe.py
```

## Security

- Listen on LAN only; do **not** expose port 8090 to the internet.
- Always set a long `auth_key`; never commit a real Bearer / toml with secrets.
- Without `enable_tier3 = true`, remote agents do not get `Shell` / `FileRead` / `KillProcess`.
- Prefer tooling under `.config\winremote\` and `skills\` — not `%TEMP%` (AV / PDM).

See [`skills/winremote-mcp/kaspersky-exclusions.md`](skills/winremote-mcp/kaspersky-exclusions.md).

## Docs

- Skill card: [`skills/winremote-mcp/SKILL.md`](skills/winremote-mcp/SKILL.md)
- MCP notes: [`mcp/winremote.md`](mcp/winremote.md)
- Config template: [`mcp/winremote.toml.example`](mcp/winremote.toml.example)
- Upstream server: [dddabtc/winremote-mcp](https://github.com/dddabtc/winremote-mcp)

## License

MIT — see [LICENSE](LICENSE).
