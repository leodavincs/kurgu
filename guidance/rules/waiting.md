@@ start
At the START of every turn look once: MCP `notes_wait` with `timeout: 0`, or `kurgu notes wait <dir> --timeout 0` (exit 3 = nothing new).
@@ keep
Keep waiting: MCP `notes_wait` `timeout: 120` (3000 if the user is watching live), again on each timeout. No MCP: `kurgu notes wait <dir> --timeout 20`, repeat on exit 3.
@@ claude
Claude Code only: run `kurgu notes wait <dir>` (no timeout) as a BACKGROUND command; you are re-invoked when it exits.
@@ codex
Codex / other agents: a background process cannot wake you and shell calls may be cut after ~30 s. Use repeated short waits, or tell the human "send, then say 'check notes'".
@@ full
**Which agent are you? (waiting for the user's notes).** The loop is always: check for sends, fix each note, `notes_done`, check again. Only the waiting differs. At the **start of every turn**, look once whether notes arrived: `notes_wait` with `timeout: 0`, or `kurgu notes wait <dir> --timeout 0` (exit 3 = nothing new).

- **Claude Code:** run `kurgu notes wait <dir>` (no timeout) as a background command; you are re-invoked when it exits.
- **Codex (CLI / IDE / desktop) and other agents:** a background process does not wake you, and long shell calls may be yielded or cut (~30 s observed). Wait with the MCP `notes_wait` tool (`timeout: 120`, or 3000 when the user says they are watching live; register it with `kurgu setup-agent --codex`, which raises Codex's 60 s tool timeout to 3600 s) and call it again when it times out. Without MCP: `kurgu notes wait <dir> --timeout 20`, repeated on exit 3. Or let Kurgu start you: `kurgu setup-agent --codex --wake` makes every Send run `codex exec resume` in the project folder (see AGENTS.md). If you cannot loop, say "send your notes, then tell me 'check notes'" and stop.
- Codex needs write access to the project folder and to run `ffmpeg`/`kurgu-render`: use sandbox `workspace-write` (start Codex inside the project folder or add it with `--add-dir`), and allow the editor's localhost server (`127.0.0.1`) if your approval policy asks.
