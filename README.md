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

Then open a **new** terminal and run `kurgu path/to/project`. `kurgu --help` lists every command. `kurgu doctor` checks the machine (Python, ffmpeg, hardware encoders, free disk space; `--json` for agents, `--fix` downloads a missing ffmpeg).

**From a checkout** (to try a branch or your own changes): `KURGU_SOURCE=/path/to/your/kurgu sh install.sh`. `KURGU_SOURCE` can be a folder, a URL or `git+https://...`; run it again after you change the code.

Chrome (or Chromium) is only needed for the code-video tools, `kurgu elements` and `kurgu rerender`; the editor itself runs without it. Set `KURGU_CHROME=/path/to/chrome` if Kurgu does not find yours.

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

**FFmpeg compatibility.** CI runs the ffmpeg-heavy tests (export, proxies, waveforms, loudness, strips, frame parity) against ffmpeg 6.1, 7.1 and 8.1 on Linux, and the full suite on whatever each runner ships. `kurgu doctor` reports your version and warns below 6.1.

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

## Review: watch, note, send to your agent

Kurgu opens in **Review mode**: a big preview, the audio as one readable waveform with the words under it, and your notes on the right. Everything you only need when editing by hand sits in the ☰ menu (or press `Shift+E` for the full editor).

1. **Watch.** Press *Watch + note* for full screen. Start typing and the video pauses; `Enter` adds the note at that second and playback continues.
2. **Point.** Can't describe where? Draw on the frame: ellipse, rectangle or arrow, in five colours (`Alt+O/B/A`, `Alt+1…5`). Shapes are stored in project pixels, so the agent gets exact coordinates, not "somewhere top right".
3. **Send.** One click on *Send to agent*. Each note goes out with its timecode, the scene and layers under it, the drawn marks (colour, position, size, the layers beneath) and a JPEG of the frame with the marks burned in. Everything lands in `NOTLAR.md` and `.kurgu/inbox.jsonl` in the project folder.
4. **The agent fixes it.** Claude Code runs `kurgu notes wait <project>` in the background and wakes up on your send; Codex calls the `notes_wait` MCP tool (see *Use with other agents*). Or let Kurgu wake the agent itself: `kurgu setup-agent --claude --wake` / `--codex --wake` starts it on every send, headless, so you don't have to look at a terminal at all.
   Every note also carries the rendered `bounds` (left/top/right/bottom, px) and an `anchor` line for each layer on screen, so the agent can line edges up with what is around it; it then checks the result with `kurgu frame <project> <seconds>` (a JPEG of the project as it is now, no browser needed) before closing the note.
   If the note gives a direction or target, apply the best change in that direction and state the values and any remaining compromise in the reply. Ask only when no reading satisfies the note's core request, or the change would be destructive. A vague note with no direction gets the smallest clear reading, stated with its values. When it does ask, the note turns into *asked*, you type the answer in the editor and it goes back to the agent.
   To check a change over time the agent makes one `kurgu strip <project> <t0> <t1>` call (a labelled contact sheet) instead of many single frames.
   Clips made with code (cinecode, Remotion, HyperFrames, Manim, plain HTML) can declare a `source` and an element map: the note then names the source file and frame, and a mark lists the on-screen elements under it with measured boxes and `file:line`, so the agent fixes the code instead of patching the timeline.
   Each note also carries measured audio facts (peak and RMS level, silences, word timings when a transcript exists) around its time.
5. **See the reply.** Fixed notes turn into *fixed* with the agent's one-line answer in the notes list. A send made while the agent is still working is never lost. While the agent works the sent notes say *Agent is working…*; if it could not start (for example it is not logged in) a red message says why, with a link to `.kurgu/hook.log`; if it finished but a note is still open, you are told that too. To compare the old and the new picture of a re-rendered clip, press `B` (a short message says so when there is no earlier version yet).
   Keys in the editor: `W` or `F` opens watch mode full screen, `N` jumps into the note box, `Enter` adds the note, `Esc` leaves, `B` flips before / after. Anything you type while watch mode is still opening is kept. A drawing shows on the frame only for the note you select or hover (notes with a drawing carry a small circle icon).

<!-- kurgu:generated:agent-side -->
Agent side: `kurgu notes wait | list | done | ask | add`, `kurgu frame <project> <seconds> [-o out.jpg] [--full]`, `kurgu strip <project> <t0> <t1> [--n 6]`, MCP `notes_wait`, `notes_list`, `notes_done`, `notes_ask`, `render_frame`, `render_strip`. `kurgu detect <project> <seconds> [--layer ID] [--json]` (alias `kurgu ocr`, MCP `detect`) finds text and shapes burned into a clip, which have no layer bounds; `kurgu rerender <project> <layer|unit>` (MCP `rerender`) regenerates a code-made clip from its source. Say "Wait for my Kurgu notes" and the agent stays in the wait, fix, done loop; Kurgu records which agent session works on the project in `.kurgu/agents.json` so the wake hook resumes that one. Details in `AGENTS.md` and `SPEC.md`.
<!-- kurgu:generated:end -->

### Clips made by code: `kurgu rerender`

A layer can say which code made it (`source`: `kind`, `unit`, `fps`, `unit_start_frame`, `render`). When a note on such a layer is fixed in the source file, `kurgu rerender <project> <unit-or-layer-id>` regenerates only that unit: which frames it covers comes from the scene list (`source.list`, e.g. `shots.json`, read fresh) or `source.unit_start_frame` + `source.frames`, never from the old media's length. A scene of a film gets its OWN file (`film.<unit>.mp4`, written atomically; the film and the other scenes are never rewritten) and its layers are re-pointed at it, with the new `end` when the length changed; the output names the later scene layers you must shift. `--dry-run` prints what would run. Audio is the matching slice of the old file. Inside Codex's macOS sandbox Chrome cannot start and the command says so. Editor and project.json otherwise stay as they are. The agent then checks with `kurgu frame` / `kurgu strip` before closing the note.

`source.render` is an adaptor name, never a command, so a downloaded project cannot run anything. Built in: `html-frames` (any page exposing `window.renderFrame(i)` or `window.captureFrame(i)`, rendered in headless Chrome with the network blocked and encoded with ffmpeg; set `source.page`, optionally `source.frames` and `source.params`) and `cinecode-shot` (uses cinecode's `capture.mjs` when you point `cinecode_dir` in your config at a cinecode checkout, otherwise `html-frames` over the shot's frames). Your own adaptor goes in `~/.config/kurgu/config.json` only: `"render_adaptors": {"mine": ["my-tool", "--unit", "{unit}", "--from", "{start_frame}", "--to", "{end_frame}", "--fps", "{fps}", "--out", "{out}"]}` (or env `KURGU_RENDER_MINE` as a JSON list). Placeholders `{root} {unit} {start_frame} {end_frame} {fps} {out}`; no shell. A `source.root` outside the project folder must be listed in `render_trusted_roots` in the same file. After a built-in re-render the unit's `source.elements` boxes are re-measured (same page, params and step; a failure keeps the old map and marks it `"stale": true`, shown as `elements_error`). `kurgu import <folder>` adds that folder to `render_trusted_roots` in your user config (one line printed; `--no-trust` skips it); a project file can never trust a folder itself.

## Use with other agents

Kurgu ships an MCP server (`kurgu-mcp`, stdio; the same code as `mcp_server.py`), so any MCP-capable agent gets the same powers: open the editor, see what you selected, edit the project in validated batches, look at rendered frames, render. `AGENTS.md` in the repo root tells agents that read that file how to work with Kurgu.

Tools:
<!-- kurgu:generated:tool-names -->
`open_editor`, `get_state`, `get_project`, `edit_project`, `render_frame`, `render_strip`, `rerender`, `detect`, `render`, `get_changes`, `get_brief`, `update_brief`, `get_notes`, `update_note`, `add_note`, `notes_list`, `notes_wait`, `notes_done`, `notes_ask`, `list_fonts`, `list_media`, `import_file`, `transcribe`, `make_captions`
<!-- kurgu:generated:end -->

Each takes an optional `project_dir` (default: the server's working directory, or the `KURGU_PROJECT_DIR` environment variable). The snippets below assume the one-line installer above (or `pipx install git+https://github.com/leodavincs/kurgu`), which puts `kurgu-mcp` on your PATH (if the agent cannot find it, use the full path: `uv tool dir --bin` or `pipx` print the folder). From a clone, use your virtualenv's Python and the full path to `mcp_server.py` instead.

**Claude Code.** Install the plugin (see above). The MCP server is registered automatically through the plugin's `.mcp.json` (via `uvx`); nothing to configure. Without the plugin: `claude mcp add kurgu -- kurgu-mcp`.

**Experimental:** the in-editor Ask AI box falls back to Codex when Claude Code is missing, but that path is not yet tested end to end. A custom agent command is read only from `KURGU_AGENT_CMD` or `~/.config/kurgu/config.json`, never from a project folder.

**OpenAI Codex (CLI, IDE, desktop app).** `kurgu setup-agent --codex` (add `--project <dir>` to put a short Kurgu section in that project's AGENTS.md; `--dry-run` shows what changes). It registers the MCP server in `~/.codex/config.toml` with `tool_timeout_sec = 3600` (Codex's default is 60 s, which would cut off `notes_wait`) and installs the skill to `~/.agents/skills/kurgu`. Restart Codex. Codex cannot be woken by a background process, so the watch + note loop uses repeated `notes_wait` calls (or you press Send and tell it "check notes"); with `--wake` Kurgu can start the agent itself when you press Send (see the "Wake the agent" notes in AGENTS.md). Run Codex inside the project folder with the `workspace-write` sandbox.

**What a woken Codex may run.** `kurgu wake codex` starts Codex in its own `CODEX_HOME` (`~/.config/kurgu/codex-home`, `%APPDATA%\kurgu\codex-home`) with a generated execpolicy file, `rules/kurgu.rules`; your own `~/.codex` and your other Codex sessions are untouched (your model settings are copied, your login is linked, not copied). Codex rules can only deny, so the file forbids network tools (`curl`, `wget`, `ssh`), `rm`/`mv`/`cp`/`tee` and other file writers, `python`/`node`/`perl`/`awk`/`find`/`env`/`xargs`/`ffmpeg`, package managers, `git push|clone|fetch|pull`, `sed -i`, `rg --pre`, any shell started again with `-c`, and the `kurgu` subcommands that are not note work. Everything else (`kurgu notes|frame|strip|rerender|detect|probe`, `ffprobe`, `cat`, `ls`, `head`, `sed -n`, `grep`, `rg`, `jq`) runs inside the `workspace-write` sandbox: writes only in the project folder, no network. Edits through Codex's patch tool, which is how it changes `project.json`, stay allowed. The Claude equivalent is the `--allowedTools` list (`kurgu notes|frame|strip`, `ffprobe`), which is stricter because Claude supports an allow list. To run without the rules, set `"codex_policy": "off"` in the user config file (`~/.config/kurgu/config.json`); a project folder cannot do that. If your Codex login lives in the OS keyring instead of `auth.json`, the wake refuses to start rather than run unrestricted. `kurgu setup-agent --codex --wake --dry-run` prints the policy.

**What a woken Claude Code may run.** `claude -p ... --allowedTools` gets exactly: `kurgu notes|frame|strip|rerender|detect|probe`, `kurgu --help`, `kurgu <command> --help` and the `kurgu` MCP tools; no general Bash, no `ffprobe` (it can read URLs: use `kurgu probe <project> [layer|file]`), and `--setting-sources user`, so a project's own `.claude/settings*.json` hooks and `.mcp.json` servers never load.

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

## If you make videos with code

If your video is rendered by code (cinecode, a page of your own, Remotion), a note on the video is best fixed in that code, not on top of the finished mp4. `kurgu import` turns your scene list into a Kurgu project where every scene is a clip that remembers where it came from (`source` in `project.json`); `kurgu elements` measures where the text of a scene sits on screen. Both are zero-dependency (headless Chrome is the only requirement for `elements`).

**cinecode** ([sarpstar/cinecode](https://github.com/sarpstar/cinecode), MIT). Render a draft or final as usual, then in the film folder: `kurgu import . --elements`. It reads `shots.json` (frames, `fps`, `text_on_screen`, `vo`), the newest `renders/film-vN.mp4` or `renders/draft-vN.mp4` and `audio/mix.json`, and writes `kurgu/project.json` (one clip per shot, cut at the shot frames; `scenes.md` lists each shot's text and voice-over). Nothing that exists in the film folder is changed. Text drawn into the canvas cannot be measured; the command says so.

**A plain HTML page that draws frames.** Any page that exposes `window.renderFrame(i)` (or `captureFrame(i)`) as a pure function of the frame works: `kurgu elements page.html --frames 0:240:6 -o scene.kurgu.json`. Text and elements in the DOM get boxes per frame, and so do text, shapes and images drawn with the 2D canvas API (`fillText`, `fill`/`stroke`, `fillRect`, `Path2D`, `drawImage`): `kurgu elements` records them automatically, the page needs no change. Canvas text gets `file:line` when its literal string is in the page's own sources (`--source-root` adds a folder; `line_ambiguous` when it appears more than once), nearby shapes of one colour merge into one element, and `--no-canvas` turns this off. WebGL / three.js output is pixels and is not captured: use the hook below or the OCR / vision fallback (coming).

Mark an element with `data-kurgu-id="title"` (and optionally `data-kurgu-src="scene.js:41"`) to give it a stable name and its source line.

**For engine authors (WebGL / 3D pages).** Anything a 2D canvas does not draw is pixels, so there is nothing to measure from the page; for the finished video, `kurgu detect <project> <seconds>` (OCR and shape detection, approximate) finds text and shapes in the frame. For exact boxes, if your page defines `window.kurguElements(i)`, `kurgu elements` calls it right after `renderFrame(i)` and merges the answer with what it finds in the DOM. It returns the boxes in page pixels:

```js
window.kurguElements = (i) => [
  { id: "title", label: "Fena değil", kind: "text", l: 116, t: 160, r: 640, b: 250,
    file: "engine/scene-s03.js", line: 41 },        // file and line are optional
];
```

**Anything else.** Render an mp4 and write a `scenes.json` next to it: `{"fps": 30, "video": "out/film.mp4", "scenes": [{"id": "intro", "start_frame": 0, "end_frame": 90, "file": "scenes/intro.html"}]}`, then `kurgu import <folder>`. A Remotion project is recognised but not imported yet: the command tells you what to write. An existing `project.json` is never overwritten without `--force`.

### A tiny scene from scratch

Three files in an empty folder show the whole loop. `scenes/hello.html` draws frame `i` as a pure function of `i`:

```html
<canvas id="c" width="1280" height="720"></canvas>
<script>
const g = document.getElementById("c").getContext("2d");
window.renderFrame = (i) => {                      // everything on screen depends only on the frame number
  g.fillStyle = `hsl(${i * 3}, 70%, 45%)`;
  g.fillRect(0, 0, 1280, 720);
  g.fillStyle = "#fff";
  g.font = "bold 96px sans-serif";
  g.fillText("Hello Kurgu", 100 + i * 4, 380);
};
</script>
```

`scenes.json` lists the scene (frames 0 to 60 at 30 fps) and names the film it belongs to:

```json
{"fps": 30, "video": "out/film.mp4",
 "scenes": [{"id": "hello", "start_frame": 0, "end_frame": 60, "file": "scenes/hello.html"}]}
```

Any 2 s mp4 will do as the first `out/film.mp4`, for example `mkdir out && ffmpeg -f lavfi -i color=black:s=1280x720:d=2:r=30 -pix_fmt yuv420p out/film.mp4`. Then:

```bash
kurgu import .                       # 1. scenes.json -> ./kurgu/project.json (one clip per scene that remembers its page)
kurgu kurgu                          # 2. open it; write a note ("text bigger, slower colour change"), press Send; the agent edits scenes/hello.html
kurgu rerender kurgu hello           # 3. render only that scene with headless Chrome and swap it in; the editor shows it at once
```

`kurgu frame kurgu 1.0` shows the result without a browser. Chrome must be installed for step 3.

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
| W / F | Watch + note, full screen (typing straight away works) |
| B | Flip between before and after of a re-rendered clip |
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

## Something wrong? / Sorun mu var?

Run `kurgu report` in a terminal (add the project folder, or `-o` to save it as `.kurgu/report-<time>.txt`), or open the editor's menu and choose **Hata raporu / Error report**. You get ONE plain-text report: versions of Kurgu, your system, ffmpeg and Chrome, the doctor check, whether an agent is set up and listening (never the command itself), the last events from `.kurgu/log.jsonl` and Kurgu's own lines from `.kurgu/hook.log`. Your home folder is shown as `~`, e-mails as `<email>`, anything that looks like a key or token as `<redacted>`; note texts and project content are never in it, and neither is the agent's output (it can quote your notes): add it only if you choose to, with `kurgu report --include-agent-output` or the checkbox in the editor's window. The editor shows the text first and only copies it when you press Copy; nothing is sent anywhere. Paste it to whoever is helping you.

## Privacy

Nothing leaves your machine, and there is no telemetry, analytics or update check. The server binds to `127.0.0.1` only; the editor loads no CDN scripts, fonts or images; fonts, proxies, thumbnails and renders stay in your project folder or in a local cache folder (`~/Library/Caches/kurgu` on macOS, `$XDG_CACHE_HOME/kurgu` or `~/.cache/kurgu` on Linux, `%LOCALAPPDATA%\kurgu\Cache` on Windows; set `KURGU_CACHE` to move it). Two things are worth knowing:

- The coding agent you connect (Claude Code, Codex, Gemini...) talks to its own provider the way it always does. Kurgu only starts it and hands it your request and the project.
- Automatic captions download a speech model from Hugging Face once, the first time you use them. Kurgu makes no other outbound connection.

## License

MIT, see [LICENSE](LICENSE). Bundled fonts keep their own OFL licences in `fonts/licenses/`. Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
