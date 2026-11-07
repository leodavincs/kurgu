# Kurgu with OpenAI Codex (CLI, IDE extension, desktop app)

```
kurgu setup-agent --codex --dry-run          # show what would change
kurgu setup-agent --codex                    # ~/.codex/config.toml [mcp_servers.kurgu] + ~/.agents/skills/kurgu
kurgu setup-agent --codex --project <dir>    # also add a short Kurgu section to <dir>/AGENTS.md
```

Restart Codex, then check `codex mcp list`. The config entry sets `tool_timeout_sec = 700` (Codex default: 60 s) so `notes_wait` is not cut off.
Codex cannot be woken by a background process: the agent waits with the MCP `notes_wait` tool (repeated), or you tell it "check notes" after pressing Send.
Run Codex inside the project folder with sandbox `workspace-write`. The instruction text lives in `kurgu_cli.py` (`AGENT_BLOCK`) and `skills/kurgu/SKILL.md`.
