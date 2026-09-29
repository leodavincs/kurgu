<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/kurgu-lockup-dark.svg">
    <img src="docs/assets/kurgu-lockup-light.svg" alt="Kurgu" width="320">
  </picture>
</p>

# Kurgu

**English** | [Türkçe](README.tr.md)

> **Your coding agent writes the video. You direct it.** Free, open source, runs on your machine.

Kurgu is a local video editor built around one plain file, `project.json`. Claude Code writes the timeline (clips, titles, subtitles, music, effects). You open it in a browser editor with a layer list, a live preview and an inspector, and adjust what Claude got almost right. Claude sees what you changed and what you have selected, so "make this text bigger" and "cut here" just work. `render.py` turns the result into an mp4.

"Kurgu" is Turkish for "edit / montage".

<p align="center"><img src="docs/assets/hero.gif" alt="Kurgu: one prompt, fourteen styles, edited in its own editor" width="720"></p>

## How it works

```
  Claude Code  <------------>  project.json  <------------>  editor (your browser)
  (skill + /kurgu)             (the timeline)                 layers, preview, inspector
       |                            |                                |
       |   reads changes.md         |                                |  writes .kurgu/state.json
       |   and .kurgu/state.json    v                                |  (selection, playhead)
       |                       render.py  ----> output.mp4 <---------+
       +--------------------------------------------------------------
                 everything runs on 127.0.0.1, nothing is uploaded
```

- **project.json** is the single source of truth: canvas size, fps, and a list of layers (`video`, `image`, `text`, `color`, `audio`) with times, positions, fades, effects and volume envelopes.
- The **editor** is a small local web app served by `server.py`. Every manual edit is saved to `project.json` and logged in `changes.md`, so Claude can learn what you changed.
- **Selection and playhead** are written to `.kurgu/state.json`, which is how Claude resolves "this text" and "here".
- **render.py** composes frames in numpy, pipes them to ffmpeg and mixes sound.

## Install

Kurgu needs Python 3.10+ and ffmpeg (with ffprobe) on your PATH.

| OS | ffmpeg |
|---|---|
| macOS | `brew install ffmpeg` |
| Debian / Ubuntu | `sudo apt install ffmpeg` |
| Windows | `winget install Gyan.FFmpeg`, or without admin rights: unzip a portable build (e.g. BtbN `ffmpeg-master-latest-win64-gpl.zip`) anywhere and set `KURGU_FFMPEG` / `KURGU_FFPROBE` to its `bin\ffmpeg.exe` / `bin\ffprobe.exe` |

Pick one route. Recent Python installs refuse a bare `pip install` into the system Python (PEP 668, "externally-managed-environment"), so use an isolated environment:

**uvx (nothing to install, runs from git):** [uv](https://docs.astral.sh/uv/) fetches Python and the dependencies for you.

```bash
uvx --from git+https://github.com/leodavincs/kurgu kurgu path/to/project
```

**pipx (a permanent `kurgu` command):**

```bash
pipx install git+https://github.com/leodavincs/kurgu
kurgu path/to/project              # editor
kurgu-render path/to/project       # render
```

**Virtualenv (from a clone, for development):**

```bash
git clone https://github.com/leodavincs/kurgu.git && cd kurgu
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python scripts/check_deps.py
```

`check_deps.py` tells you what is missing and how to install it. Optional, for automatic captions (speech-to-text runs locally, the model downloads once on first use): `pipx inject kurgu faster-whisper` (or `mlx-whisper` on Apple Silicon), or `pip install faster-whisper` inside your virtualenv.

### As a Claude Code plugin

```
/plugin marketplace add leodavincs/kurgu
/plugin install kurgu@kurgu
```

The plugin registers its MCP server through [`uvx`](https://docs.astral.sh/uv/), which works the same on macOS, Linux and Windows and installs the Python dependencies itself; install uv first (`brew install uv`, `winget install astral-sh.uv`, or `pipx install uv`). You still need ffmpeg. Claude Code namespaces plugin commands, so the editor command is **`/kurgu:kurgu path/to/project`** (typing `/kurgu` may complete to it). Without uv, install Kurgu with pipx and add the MCP server by hand: `claude mcp add kurgu -- kurgu-mcp`.

## Usage

1. **Ask Claude.** In Claude Code: "Make a 30 second vertical teaser from `clip.mp4`, add a title at the start and Turkish subtitles, put the music at the back." Claude copies assets into `media/` and writes `project.json`.
2. **Open the editor.** Run `/kurgu:kurgu path/to/project` in Claude Code, or without the plugin: `kurgu path/to/project`. Your browser opens at `http://127.0.0.1:8765`.
3. **Fine-tune.** Drag text in the preview, trim clips, change fonts, add effects, drop a note where the sound is too loud.
4. **Ask for more.** Select a layer or park the playhead and tell Claude: "shorter", "move this up", "cut here". Claude re-reads `project.json`, `changes.md` and your selection first, and keeps your manual edits.
5. **Render.** Use the Render button in the editor, or:

```bash
kurgu-render path/to/project --draft              # half resolution, fast
kurgu-render path/to/project                      # full quality
kurgu-render path/to/project --frame 12.3 --output frame.png
```

Draft first, full when it looks right. (From a clone, `python render.py ...` does the same.)

## Use with other agents

Kurgu ships an MCP server (`kurgu-mcp`, stdio; the same code as `mcp_server.py`), so any MCP-capable agent gets the same powers: open the editor, see what you selected, edit the project in validated batches, look at rendered frames, render. `AGENTS.md` in the repo root tells agents that read that file how to work with Kurgu.

Tools: `open_editor`, `get_state`, `get_project`, `edit_project`, `render_frame`, `render`, `get_changes`, `list_fonts`, `list_media`, `import_file`. Each takes an optional `project_dir` (default: the server's working directory, or the `KURGU_PROJECT_DIR` environment variable). The snippets below assume `pipx install git+https://github.com/leodavincs/kurgu`, which puts `kurgu-mcp` on your PATH (on Windows, use the full path printed by `pipx` if the agent cannot find it). From a clone, use your virtualenv's Python and the full path to `mcp_server.py` instead.

**Claude Code.** Install the plugin (see above). The MCP server is registered automatically through the plugin's `.mcp.json` (via `uvx`); nothing to configure. Without the plugin: `claude mcp add kurgu -- kurgu-mcp`.

**Experimental:** the in-editor Ask AI box falls back to Codex when Claude Code is missing, but that path is not yet tested end to end. A custom agent command is read only from `KURGU_AGENT_CMD` or `~/.config/kurgu/config.json`, never from a project folder.

**OpenAI Codex CLI.** Add to `~/.codex/config.toml` (or run `codex mcp add kurgu -- kurgu-mcp`):

```toml
[mcp_servers.kurgu]
command = "kurgu-mcp"
tool_timeout_sec = 900   # default is 60 s; full renders take longer
```

**Cursor.** `.cursor/mcp.json` in your project (or `~/.cursor/mcp.json` for all projects):

```json
{
  "mcpServers": {
    "kurgu": {
      "command": "kurgu-mcp",
      "env": { "KURGU_PROJECT_DIR": "${workspaceFolder}" }
    }
  }
}
```

**Gemini CLI.** `~/.gemini/settings.json` (or `.gemini/settings.json` in the project):

```json
{
  "mcpServers": {
    "kurgu": {
      "command": "kurgu-mcp",
      "timeout": 900000
    }
  }
}
```

Nothing here changes how Kurgu works: the agent edits through the running editor when there is one (you see changes live and they are logged in `changes.md` as `[agent]`), and writes `project.json` directly when there is not.

## Editor tour

- **Layer list.** Front-most layer on top, grouped (Picture, Titles, Sound). Hide, lock, reorder, rename.
- **Timeline.** Filmstrips for video, waveforms for audio, note markers, playhead, magnetic snapping.
- **Preview with direct manipulation.** Drag layers, scale and position them on the canvas; arrow keys nudge.
- **Inspector.** Every field of the selected layer: timing, fades, position, scale, opacity, crop, fit, volume, text settings and the volume envelope.
- **Effects.** Grayscale, brightness, contrast, saturation, blur, slow zoom, freeze frame; global grain, vignette and fade-out.
- **Notes.** Pin a comment to a moment on the timeline. Claude reads them as instructions.
- **Real-frame compare.** The preview is a fast approximation. One click shows the exact frame rendered by `render.py` so you can compare.
- **Import.** Drag files onto the window or use Import. Video, images and audio go to `media/`, fonts to `fonts/` and can be applied to the selected text at once.
- **Fonts.** Project, built-in and system fonts with live preview. Preview and render use the same font file. Turkish characters are supported.
- **EN / TR switch** in the top bar; the editor starts in your browser's language.

### Shortcuts

**Cmd** on macOS, **Ctrl** on Windows and Linux.

| Key | Action |
|---|---|
| Space | Play / pause (hold Space in the preview and drag to pan) |
| Left / Right | Previous / next frame |
| Shift + Left / Right | Back / forward 1 s |
| `[` / `]` | Previous / next frame |
| Home / End | Jump to start / end |
| Arrows (preview focused) | Nudge the selected layer 1 px (Shift: 10 px) |
| Cmd/Ctrl + K | Split the selected layer at the playhead |
| Cmd/Ctrl + D | Duplicate the selected layers |
| Cmd/Ctrl + A | Select all layers |
| Delete / Backspace | Delete the selected layers |
| Cmd/Ctrl + Z / Shift + Cmd/Ctrl + Z | Undo / redo |
| Cmd/Ctrl + S | Save now (edits also save automatically) |
| Cmd/Ctrl + E | Open / close the export dialog |
| S | Toggle snapping |
| N | Add a note at the playhead |
| Shift + N | Show / hide the Notes panel |
| Alt + Left / Right | Previous / next note |
| `\` | Show / hide the media panel |
| Cmd/Ctrl + `+` / `-` | Zoom the preview in / out (Cmd/Ctrl + scroll also zooms) |
| Cmd/Ctrl + `0` / `1` | Zoom to fit / 100 % |
| Cmd/Ctrl + Enter | Send the request in the AI box |
| Esc | Close dialog or menu, otherwise clear the selection |

Shortcuts are ignored while you type in a text field.

## Project format

Everything is in [SPEC.md](SPEC.md): layer types and fields, effects, fonts, the render command line and the local server API. Claude gets a condensed version through the bundled skill (`skills/kurgu/`).

## FAQ

**Does it upload anything?** No, see [Privacy](#privacy).

**Which operating systems?** macOS, Linux and Windows, with Python 3.10+ and ffmpeg on PATH.

**How is this different from Remotion?** Remotion videos are React code. Kurgu videos are a JSON timeline that Claude and a human co-edit: the human uses a Premiere-style editor instead of reading code, and Claude can see and respect every manual change.

**Can I use my own fonts?** Yes: drop a `.ttf`, `.otf` or `.ttc` into the editor or into `<project>/fonts/`.

**Where are my files?** `project.json`, `media/`, `fonts/` and `changes.md` in your project folder. `.kurgu/` holds caches, backups (last 50 versions of `project.json`) and `state.json`.

## Privacy

Nothing leaves your machine, and there is no telemetry, analytics or update check. The server binds to `127.0.0.1` only; the editor loads no CDN scripts, fonts or images; fonts, proxies, thumbnails and renders stay in your project folder or in a local cache folder (`~/Library/Caches/kurgu` on macOS, `$XDG_CACHE_HOME/kurgu` or `~/.cache/kurgu` on Linux, `%LOCALAPPDATA%\kurgu\Cache` on Windows; set `KURGU_CACHE` to move it). Two things are worth knowing:

- The coding agent you connect (Claude Code, Codex, Gemini...) talks to its own provider the way it always does. Kurgu only starts it and hands it your request and the project.
- Automatic captions download a speech model from Hugging Face once, the first time you use them. Kurgu makes no other outbound connection.

## License

MIT, see [LICENSE](LICENSE). Bundled fonts keep their own OFL licences in `fonts/licenses/`. Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
