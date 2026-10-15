# Contributing

1. Do not commit secrets: real `auth_key`, Bearer tokens, private IPs as
   "canonical" host addresses, or personal account names.
2. Keep tooling paths under `.config/winremote` and `skills/` — never `%TEMP%`.
3. Prefer placeholders: `<WINDOWS_LAN_IP>`, `<WINREMOTE_AUTH_KEY>`,
   `%USERPROFILE%`.
4. Skills stay agent-oriented: short HARD rules, working scripts, no essay.

Upstream MCP server bugs → [dddabtc/winremote-mcp](https://github.com/dddabtc/winremote-mcp).
