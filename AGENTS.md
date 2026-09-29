# AGENTS.md: Kurgu

Kurgu is a local Premiere-style timeline editor for videos that an agent writes as code. You (the agent) write and edit `project.json`; the human fine-tunes it in a browser editor; `render.py` turns it into an mp4. Everything runs on `127.0.0.1`. Contract: `SPEC.md`. **Field reference for writing projects: `skills/kurgu/reference.md` (read it before writing a project from scratch).**

## Setup

Python 3.10+, ffmpeg + ffprobe on PATH, installed with `pipx install git+https://github.com/leodavincs/kurgu` (commands `kurgu`, `kurgu-render`, `kurgu-mcp`) or, in a clone, a virtualenv with `pip install -r requirements.txt` (`python scripts/check_deps.py` says what is missing). Tests: `python -m pytest -q`. Use `python` (or `py -3` on Windows) where this file says `python3`.

## Workflow

1. Put every asset in `<dir>/media/` (fonts in `<dir>/fonts/`); reference it relatively (`"src": "media/clip.mp4"`). Probe durations with `ffprobe` so `start`/`end`/`duration` match reality.
2. Write `<dir>/project.json`. Layers array: first element is front-most. Every layer gets a short stable `id` and a readable `name`.
3. Start the editor and tell the human the URL: `open_editor` (MCP) or `python3 server.py <dir> --no-open` in the background.
4. Look before you show: render stills (`render_frame`, or `python3 render.py <dir> --frame 12.3 --output /tmp/f.png --draft`) at the first frame, each title, and the end. Fix what is off.
5. **Brief.** `<dir>/brief.md` is the video's creative brief (max 250 words, short bullets: what it is, audience/platform, tone/style, hard rules, open decisions). Read it before editing (`get_brief`). Whenever the human states intent, style, audience or a rule about the video, update it (`update_brief`: full replace, max 2000 chars; without MCP edit the file). No secrets/paths/personal data. Ask AI in the editor passes it to its agent.
6. **Before every follow-up edit, re-read what the human did:** current `project.json`, the tail of `changes.md` (one line per manual edit; respect them, do not revert values they set), and `.kurgu/state.json` (`{time, selected, playing, language, updated}`: "this text" = the selected layer(s), "here" = `time`). If `project.json` has `notes`, they are the human's timeline comments (Notes panel). When the human says "apply my notes" / "notlarımı uygula": `get_notes` (open ones, with the layers under each), handle each at its time, then `update_note` it with `done: true` and a one-line `reply` ("Blur reduced 22→8"; shown under the note). Resolve, do not delete; `add_note` to flag something. Without MCP set `done`/`reply` on the note in project.json.
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
python3 -m pytest -q
```

## Code map

`schema.py` (defaults, normalisation, validation), `fonts.py` (registry), `anim.py` + `web/js/anim.js` (motion maths twins: keyframes, easing, seeded wiggle; shared vectors in `tests/anim_vectors.json`), `presets.py` (cam.punch / ken-burns / shake baked into keys), `server.py` (local HTTP API), `render.py` (numpy + ffmpeg), `transcribe.py` + `captions.py` (optional local Whisper, caption chunking), `mcp_server.py` (MCP over stdio, reuses the others), `ai.py` ("Ask AI": `POST /api/ai` runs the user's own `claude`/`codex` CLI headless with the Kurgu MCP tools; override with `KURGU_AGENT_CMD` or `agent_cmd` in the user-level `~/.config/kurgu/config.json` (never from the project's `.kurgu/config.json`: a project folder is untrusted; that file only takes `agent`, `ai_timeout`, `brief_timeout`); the Codex fallback is experimental; UI in `web/js/ai.js`; the box is a conversation thread: sessions resumed via `--resume`, threads in `.kurgu/ai-threads.json`, run from `.kurgu/ai-work/` so they never mix with the user's own terminal sessions; `brief.md` + `POST /api/brief/generate` summarise the terminal conversation with `claude -p --continue --fork-session`), `web/` (editor; `web/js/platform.js` = Mac/Windows modifier + `{mod}`/`{shift}`/`{alt}`/`{modkey}` placeholders in i18n strings, never hard-code ⌘; `web/js/merge.js` = pure three-way merge after a 409 save conflict, node-tested by `tests/test_merge.py`). Code and JSON keys are English; UI text goes through `web/i18n/{en,tr}.json`.
