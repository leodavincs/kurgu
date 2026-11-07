# AGENTS.md: Kurgu

Kurgu is a local Premiere-style timeline editor for videos that an agent writes as code. You (the agent) write and edit `project.json`; the human fine-tunes it in a browser editor; `render.py` turns it into an mp4. Everything runs on `127.0.0.1`. Contract: `SPEC.md`. **Field reference for writing projects: `skills/kurgu/reference.md` (read it before writing a project from scratch).**

## Setup

Install with one line: `curl -LsSf https://raw.githubusercontent.com/leodavincs/kurgu/main/install.sh | sh` (macOS / Linux) or `powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/leodavincs/kurgu/main/install.ps1 | iex"` (Windows): Python 3.10+ and ffmpeg are fetched when missing; commands `kurgu`, `kurgu-render`, `kurgu-mcp`, `kurgu-notes`. `kurgu doctor` says what is missing or wrong. In a clone: a virtualenv with `pip install -r requirements.txt` (`python scripts/check_deps.py`). Tests: `python -m pytest -q`. Use `python` (or `py -3` on Windows) where this file says `python3`.

## Workflow

1. Put every asset in `<dir>/media/` (fonts in `<dir>/fonts/`); reference it relatively (`"src": "media/clip.mp4"`). Probe durations with `ffprobe` so `start`/`end`/`duration` match reality.
2. Write `<dir>/project.json`. Layers array: first element is front-most. Every layer gets a short stable `id` and a readable `name`.
3. Start the editor and tell the human the URL: `open_editor` (MCP) or `python3 server.py <dir> --no-open` in the background.
4. Look before you show: render stills (`render_frame`, or `python3 render.py <dir> --frame 12.3 --output /tmp/f.png --draft`) at the first frame, each title, and the end. Fix what is off.
5. **Brief.** `<dir>/brief.md` is the video's creative brief (max 250 words, short bullets: what it is, audience/platform, tone/style, hard rules, open decisions). Read it before editing (`get_brief`). Whenever the human states intent, style, audience or a rule about the video, update it (`update_brief`: full replace, max 2000 chars; without MCP edit the file). No secrets/paths/personal data. Ask AI in the editor passes it to its agent.
6. **Before every follow-up edit, re-read what the human did:** current `project.json`, the tail of `changes.md` (one line per manual edit; respect them, do not revert values they set), and `.kurgu/state.json` (`{time, selected, playing, language, updated}`: "this text" = the selected layer(s), "here" = `time`). If `project.json` has `notes`, they are the human's timeline comments (Notes panel). When the human says "apply my notes" / "notlarımı uygula": `get_notes` (open ones, with the layers under each), handle each at its time, then `update_note` it with `done: true` and a one-line `reply` ("Blur reduced 22→8"; shown under the note). Resolve, do not delete; `add_note` to flag something.
   **Watch + note loop:** the human can watch the video in the editor (*İzle + not*, or Review mode) and click Send. The exact agent loop is **wait → fix → done → wait**: `kurgu notes wait <dir>` (MCP `notes_wait`) returns the notes of every send you have not consumed yet and otherwise blocks until he sends (omitted `--timeout` = forever; `--timeout 0` = look once, exit 3 when nothing is new; MCP default 120 s, 0 = look once). A saved cursor (`.kurgu/inbox.cursor`, each inbox entry has a `seq`) records what you already got, so a send made while you were fixing is returned by the next wait, never lost; notes that were already marked done are not handed out again, and a note stamped `sent` whose inbox line was never written (a crash) is delivered once as `recovered`. `--since N` peeks at sends above seq N without touching the cursor. Notes get `sent` (timestamp); `NOTLAR.md` in the project folder lists each with timecode, layers at that time, a frame JPEG under `.kurgu/notes/` and, when he drew on the frame, the marks (burned into the JPEG, plus `hint` and layers under each mark). Fix each, then `kurgu notes done <dir> <id...> --reply "what you changed"` (MCP `notes_done`), then `kurgu notes wait <dir>` again. `kurgu notes list <dir> [--open|--sent|--all] [--json]` shows what is still to fix (default `--sent`). `kurgu notes …` runs `agent_notes.py` directly (also installed as `kurgu-notes`); a project folder named `notes` is opened as `kurgu ./notes`.
   **Which agent?** At the start of every turn look once (`notes_wait` timeout 0, or `kurgu notes wait <dir> --timeout 0`). Claude Code: run `kurgu notes wait <dir>` in the background and get re-invoked when it exits. Codex and others: no background wake-up and shell calls may be cut at ~30 s, so call MCP `notes_wait` repeatedly (timeout 120, or 3000 when the user says they are watching; `kurgu setup-agent --codex` sets the tool timeout to 3600 s), or `kurgu notes wait <dir> --timeout 20` on exit 3. `kurgu setup-agent --codex` registers Kurgu with Codex.
   **Wake the agent when he presses Send (optional):** `kurgu setup-agent --codex --wake` (or `--claude --wake`; `--dry-run` shows it, `--no-wake` removes it) sets `on_send` in the USER config (`~/.config/kurgu/config.json`, `%APPDATA%\kurgu\config.json`; env `KURGU_ON_SEND` = JSON argv list): Kurgu then runs it detached in the project folder after every send (placeholders `{dir}` `{seq}` `{count}` `{file}`; env `KURGU_PROJECT_DIR`, `KURGU_SEQ`, `KURGU_NOTES_COUNT`, `KURGU_NOTES_FILE`; output in `.kurgu/hook.log`; one run at a time, sends meanwhile make one follow-up run). It is never read from a project's `.kurgu/config.json`. The hook is `kurgu wake <codex|claude> {dir} {seq}`: Codex runs `codex exec resume --last --json -c sandbox_mode=\"workspace-write\" -c approval_policy=\"never\" \"<prompt>\"` and, when there is no session in that folder yet, `codex exec -s workspace-write -c approval_policy=\"never\" --skip-git-repo-check -C {dir} --json \"<prompt>\"`; Claude Code runs `claude -p \"<prompt>\" --continue --output-format json --permission-mode acceptEdits --allowedTools \"Bash(kurgu notes:*),Bash(ffprobe:*),mcp__kurgu\"` and falls back to the same without `--continue` (token usage / cost end up in `.kurgu/hook.log`). Trade-off: the headless run continues the folder's last session, you do not see it live; Kurgu shows the result as "düzeltildi" + the agent's reply.
7. Render a draft first (`--draft`, half resolution), full quality only when the human says it is right.

## Rules

- Keep ids stable; never regenerate ids of existing layers. Patch `project.json`, do not overwrite it wholesale once the human has been editing.
- Do not touch `.kurgu/` except reading `state.json`.
- Times are seconds on the timeline; `end` is exclusive; `duration` must cover the last layer's `end`.
- Text `font` is a font id (e.g. `inter-bold`), not a file name. Turkish text needs ş ğ ı İ ö ü ç: all bundled fonts have them.
- No personal or private data in `project.json`.

## MCP tools (preferred when your agent has them)

Same powers over MCP (`mcp_server.py`, stdio, zero dependencies). Every tool takes an optional `project_dir` (default: server cwd or `$KURGU_PROJECT_DIR`).

| tool | what it does |
|---|---|
| `open_editor` | start the browser editor if needed, return the URL |
| `get_state` | selection + playhead, with full JSON of the selected layers (says if the editor is closed / state stale) |
| `get_project` | normalised project; `summary: true` = compact layer table |
| `edit_project` | atomic batch: `update:[{id,set}]`, `add:[layer]` (+`index`), `remove:[ids]`, `project:{...}`, `summary`. Validated; invalid batches are rejected whole. Goes through the running editor (live reload, backup, changes.md `[agent]` line) or writes the file directly |
| `render_frame` | one PNG frame (image), `t`, `draft` |
| `render` | full render, `draft` (progress notifications) |
| `get_notes`, `update_note`, `add_note` | read open/done notes with the layers at their time; resolve/edit/reply; leave an agent note |
| `notes_list`, `notes_wait`, `notes_done` | notes the human sent from watch mode: list (default sent), block until he sends, mark fixed with a reply |
| `get_brief`, `update_brief` | read / fully replace `brief.md` (max 2000 chars; logs `[agent] brief updated`) |
| `get_changes` | tail of `changes.md` (what the human did by hand) |
| `transcribe`, `make_captions` | optional local speech-to-text (needs `pip install mlx-whisper` or `faster-whisper`): word timings in timeline time; one `text` layer per caption chunk in group "Captions" (`cap_1`, ...) |
| `list_fonts`, `list_media`, `import_file` | font ids, media with durations, copy a file into `media/`/`fonts/` |

With MCP: `get_state` + `get_changes` before each edit, change the project only via `edit_project`, check with `render_frame`. Without it, do the same by editing the files and running `render.py`.

Setup snippets for Claude Code, Codex CLI, Cursor and Gemini CLI: `README.md`, section "Use with other agents".

## Commands

```
python3 server.py <dir> [--port 8765] [--no-open]                  # editor
python3 render.py <dir> [--draft] [--output f.mp4]                 # video
python3 render.py <dir> --frame 12.3 --output f.png [--draft]      # one frame
python3 mcp_server.py                                              # MCP server on stdio
kurgu notes wait|list|done <dir> ...                               # watch + note loop (python3 server.py notes ...)
python3 -m pytest -q
```

## Code map

`schema.py` (defaults, normalisation, validation), `fonts.py` (registry), `anim.py` + `web/js/anim.js` (motion maths twins: keyframes, easing, seeded wiggle; shared vectors in `tests/anim_vectors.json`), `presets.py` (cam.punch / ken-burns / shake baked into keys), `server.py` (local HTTP API), `render.py` (numpy + ffmpeg), `transcribe.py` + `captions.py` (optional local Whisper, caption chunking), `mcp_server.py` (MCP over stdio, reuses the others), `ai.py` ("Ask AI": `POST /api/ai` runs the user's own `claude`/`codex` CLI headless with the Kurgu MCP tools; override with `KURGU_AGENT_CMD` or `agent_cmd` in the user-level `~/.config/kurgu/config.json` (never from the project's `.kurgu/config.json`: a project folder is untrusted; that file only takes `agent`, `ai_timeout`, `brief_timeout`); the Codex fallback is experimental; UI in `web/js/ai.js`; the box is a conversation thread: sessions resumed via `--resume`, threads in `.kurgu/ai-threads.json`, run from `.kurgu/ai-work/` so they never mix with the user's own terminal sessions; `brief.md` + `POST /api/brief/generate` summarise the terminal conversation with `claude -p --continue --fork-session`), `web/` (editor; `web/js/platform.js` = Mac/Windows modifier + `{mod}`/`{shift}`/`{alt}`/`{modkey}` placeholders in i18n strings, never hard-code ⌘; `web/js/merge.js` = pure three-way merge after a 409 save conflict, node-tested by `tests/test_merge.py`). Code and JSON keys are English; UI text goes through `web/i18n/{en,tr}.json`.
