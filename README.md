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

One command. It installs [uv](https://docs.astral.sh/uv/) if needed, a private Python 3.12 (no system Python required), Kurgu, and a static ffmpeg; no admin rights. Run it again any time to upgrade.

| OS | Command |
|---|---|
| macOS / Linux | `curl -LsSf https://raw.githubusercontent.com/leodavincs/kurgu/main/install.sh \| sh` |
| Windows (PowerShell) | `powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/leodavincs/kurgu/main/install.ps1 \| iex"` |

Then open a **new** terminal and run `kurgu path/to/project`. `kurgu doctor` checks the machine (Python, ffmpeg, hardware encoders, free disk space; `--json` for agents, `--fix` downloads a missing ffmpeg).

### Install for your user (for a coding agent: Claude Code, Codex, ...)

When the user says "install Kurgu and open my project", follow this verbatim:

1. **Detect the OS** (`uname -s` on macOS/Linux, `$env:OS` / `ver` on Windows).
2. **Run the one-liner for that OS** (table above). Read its output to the end; it ends with `Done. Next step:  kurgu <your project folder>`.
3. **Check:** `kurgu doctor --json` (use `%USERPROFILE%\.local\bin\kurgu.exe` or `~/.local/bin/kurgu` if the new command is not on PATH yet in your shell). `"ok": true` means ready.
4. **Open the project:** run `kurgu "<project folder>"` as a background process (it prints the URL and opens the browser; add `--no-open` if the user has no desktop browser) and tell the user the URL. Quote the path: folders with spaces, Turkish letters (İ ı ş ğ) or emoji are fine.

When something goes wrong:

- **PowerShell says "running scripts is disabled" / execution policy.** The one-liner already uses `-ExecutionPolicy Bypass` for that one process; use it exactly as written, from a normal (non-admin) PowerShell. If the shell is locked by a company policy, ask the user to run it from their own PowerShell window.
- **Company proxy / no download.** Set `HTTPS_PROXY` (and `HTTP_PROXY`) to the proxy URL, e.g. `$env:HTTPS_PROXY="http://proxy.company:8080"` (PowerShell) or `export HTTPS_PROXY=...`, and run the one-liner again. If ffmpeg alone cannot be fetched, Kurgu still installs; download any ffmpeg build by hand and set `KURGU_FFMPEG` and `KURGU_FFPROBE` to the full paths of `ffmpeg` and `ffprobe`, then run `kurgu doctor`.
- **`kurgu` not found right after install.** The terminal that ran the installer has the old PATH. Open a new terminal, or call it by its full path (printed by the installer; `uv tool dir --bin` shows the folder).
- **ffmpeg only:** `kurgu setup-ffmpeg` (`--force` re-downloads, `--json` for agents).
- **Windows Defender / SmartScreen.** Files that a script downloads carry no "from the internet" mark, so SmartScreen normally stays quiet. Defender may scan `ffmpeg.exe` for a few seconds on the first start; that is normal.
- **Uninstall:** `uv tool uninstall kurgu`, then delete the data folder (Windows `%LOCALAPPDATA%\Kurgu`, macOS `~/Library/Application Support/Kurgu`, Linux `~/.local/share/kurgu`). Optional: `uv cache clean`.

### Where ffmpeg comes from

Order: `KURGU_FFMPEG` / `KURGU_FFPROBE` (a file or a folder) → `ffmpeg` on PATH (a system ffmpeg always wins) → the copy `kurgu setup-ffmpeg` downloaded into Kurgu's data folder (`%LOCALAPPDATA%\Kurgu\ffmpeg`, `~/Library/Application Support/Kurgu/ffmpeg`, `~/.local/share/kurgu/ffmpeg`) → usual install folders (Homebrew, winget, Chocolatey, Scoop, ...). The editor never downloads anything by itself; only the install script, `kurgu setup-ffmpeg` or `kurgu doctor --fix` do. Downloads are verified by SHA-256 and by running `-version`.

Static builds used (all GPL builds of FFmpeg, free software, fetched on your machine, not shipped inside Kurgu; see https://ffmpeg.org/legal.html): Windows and Linux from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), macOS from [ffmpeg.martin-riedl.de](https://ffmpeg.martin-riedl.de/). Prefer your own? `brew install ffmpeg`, `sudo apt install ffmpeg` or `winget install Gyan.FFmpeg` also work.

### Other routes

Python 3.10+ and ffmpeg/ffprobe are the only requirements; the one-liner just provides both. Recent Python installs refuse a bare `pip install` into the system Python (PEP 668), so use an isolated environment:

```bash
uvx --from git+https://github.com/leodavincs/kurgu kurgu path/to/project    # no install, runs from git
pipx install git+https://github.com/leodavincs/kurgu                         # permanent command (needs ffmpeg: kurgu setup-ffmpeg)
```

**Virtualenv (from a clone, for development):**

```bash
git clone https://github.com/leodavincs/kurgu.git && cd kurgu
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python scripts/check_deps.py        # same as `kurgu doctor`
```

Optional, for automatic captions (speech-to-text runs locally, the model downloads once on first use): `uv tool install --force --python 3.12 --with faster-whisper git+https://github.com/leodavincs/kurgu` (Apple Silicon: `--with mlx-whisper`), or `pipx inject kurgu faster-whisper`, or `pip install faster-whisper` inside your virtualenv.

### As a Claude Code plugin

```
/plugin marketplace add leodavincs/kurgu
/plugin install kurgu@kurgu
```

The plugin registers its MCP server through [`uvx`](https://docs.astral.sh/uv/), which works the same on macOS, Linux and Windows and installs the Python dependencies itself; install uv first (`brew install uv`, `winget install astral-sh.uv`, or `pipx install uv`), or just run the one-line installer above, which also sets up ffmpeg. Without it you still need ffmpeg (`uvx --from git+https://github.com/leodavincs/kurgu kurgu setup-ffmpeg`). Claude Code namespaces plugin commands, so the editor command is **`/kurgu:kurgu path/to/project`** (typing `/kurgu` may complete to it). Without uv, install Kurgu with pipx and add the MCP server by hand: `claude mcp add kurgu -- kurgu-mcp`.

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

Tools: `open_editor`, `get_state`, `get_project`, `edit_project`, `render_frame`, `render`, `get_changes`, `list_fonts`, `list_media`, `import_file`. Each takes an optional `project_dir` (default: the server's working directory, or the `KURGU_PROJECT_DIR` environment variable). The snippets below assume the one-line installer above (or `pipx install git+https://github.com/leodavincs/kurgu`), which puts `kurgu-mcp` on your PATH (if the agent cannot find it, use the full path: `uv tool dir --bin` or `pipx` print the folder). From a clone, use your virtualenv's Python and the full path to `mcp_server.py` instead.

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
