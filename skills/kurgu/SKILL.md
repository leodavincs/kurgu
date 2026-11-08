---
name: kurgu
description: Make or edit a video as code with Kurgu. Use when the user asks you (Claude Code, Codex or another coding agent) to create, edit, cut, trim, caption, subtitle, add titles or text, music, voice-over, fades, effects, colour changes or a logo, or to render or export a video, or mentions project.json, the Kurgu editor, "this text", "here" or "right now" in a video. The agent writes project.json (a JSON timeline); the user fine-tunes it in a local browser editor; render.py produces the mp4.
---

# Kurgu: video as code

You (the coding agent) write `project.json`. The user opens the same file in a local browser editor (started with `/kurgu`), adjusts it by hand, and you keep iterating together. `render.py` turns it into an mp4. Everything is local. Full field reference: `reference.md` in this skill folder (read it before writing a project from scratch). The formal contract is `SPEC.md` in the plugin root.

**MCP tools (prefer them when available).** This plugin also ships an MCP server (`kurgu`). If you can call `open_editor`, `get_state`, `get_project`, `edit_project`, `render_frame`, `render`, `get_changes`, `get_brief`, `update_brief`, `get_notes`, `update_note`, `add_note`, `list_fonts`, `list_media`, `import_file`, `transcribe` or `make_captions` (their names carry a `mcp__...kurgu__` prefix), use them instead of editing files by hand: `get_state` and `get_changes` are the re-read routine below in two calls, `edit_project` applies an atomic validated batch (`update`/`add`/`remove`/`project` + a one-line `summary`) through the running editor so it reloads live and logs `[agent]` lines in `changes.md`, and `render_frame` returns the still as an image you can look at directly. Every tool takes an optional `project_dir`. If the tools are not there, follow the file-based workflow below unchanged.

Paths: the plugin root is `${CLAUDE_PLUGIN_ROOT}`. `<dir>` is the project directory (the one holding `project.json`).

## Install Kurgu for the user (if `kurgu` is not installed yet)

No Python, ffmpeg or admin rights needed; one command, safe to repeat (it upgrades). Detect the OS, run the matching line, then check and open:

- macOS / Linux: `curl -LsSf https://raw.githubusercontent.com/leodavincs/kurgu/main/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/leodavincs/kurgu/main/install.ps1 | iex"`
- Check: `kurgu doctor --json` (`"ok": true` = ready; if the command is not found yet use `~/.local/bin/kurgu` or `%USERPROFILE%\.local\bin\kurgu.exe`). Missing ffmpeg only: `kurgu setup-ffmpeg`.
- Open: `kurgu "<project folder>"` in the background, tell the user the URL. Quote the path (spaces, Turkish letters, emoji are fine).
- Blocked? Execution policy: keep `-ExecutionPolicy Bypass` and use a normal PowerShell. Proxy: set `HTTPS_PROXY` and rerun. No download possible: get any ffmpeg by hand and set `KURGU_FFMPEG` / `KURGU_FFPROBE`.
- Uninstall: `uv tool uninstall kurgu`, then delete the data folder (`%LOCALAPPDATA%\Kurgu`, `~/Library/Application Support/Kurgu` or `~/.local/share/kurgu`).

## Workflow

1. **Set up.** Pick or create `<dir>`. Put every new asset in `<dir>/media/` (copy or generate it there; fonts in `<dir>/fonts/`). Reference it relatively: `"src": "media/clip.mp4"`.
2. **Probe sources.** `ffprobe -v error -show_entries format=duration:stream=width,height,codec_type -of json <file>` so `start`/`end`/`duration` match reality.
3. **Write `project.json`.** Layers array: first element is front-most. Give every layer a short stable `id` and a readable `name`.
4. **Start the editor** with `/kurgu <dir>` (or `uvx --from "${CLAUDE_PLUGIN_ROOT}" kurgu <dir> --no-open` in the background; plain `kurgu <dir> --no-open` if Kurgu is installed with pipx). Tell the user the URL.
5. **Look before you show.** Render a still and view it with the Read tool:
   `uvx --from "${CLAUDE_PLUGIN_ROOT}" kurgu-render <dir> --frame 12.3 --output <scratch>/f.png --draft` (write the PNG outside the project dir). Check several moments: first frame, a title, the end. Fix what is off (cropped text, wrong position, unreadable contrast) before reporting.
6. **Iterate.** The user edits in the browser and asks for changes. Before EVERY follow-up edit, do the re-read routine below.
7. **Render.** Draft first, then full (see Rendering).

## Project brief (`brief.md`)

`<dir>/brief.md` is the video's creative brief: at most 250 words of short bullets (what the video is; audience and platform; tone and style; hard rules such as "no orange", "real footage only"; open decisions). The editor's Ask AI agent reads it, so it is how the user's terminal conversation reaches it. **Read it before editing** (`get_brief`, or the file). **Whenever the user states intent, style, audience or a rule about the video, update it** (`update_brief` replaces the whole text, max 2000 characters; without MCP, edit the file): read the old one, merge the new fact, keep it short. Only facts about this video: no secrets, paths or personal data. The user can also edit it in the editor or generate it from the terminal conversation.

## Before every edit: re-read routine

The user may have changed things since you last wrote. (With MCP: `get_state`, `get_changes`, and `get_project` with `summary: true`, then skip to the last step.)
1. Read `<dir>/project.json` again. Never edit from memory or from an old copy.
2. Read the tail of `<dir>/changes.md`: one line per manual edit, `- HH:MM:SS line` under `## YYYY-MM-DD`. It tells you what the user changed by hand. **Respect those changes**: do not revert values they set (positions, fonts, colours, timings) unless they ask. If your plan conflicts with a manual edit, keep theirs and mention it.
3. Read `<dir>/.kurgu/state.json`: `{"time", "selected": [layer ids], "playing", "language", "updated"}`. Resolve deictic words with it:
   - "this text", "this clip", "it" -> the layer(s) in `selected`
   - "here", "now", "at this point", "cut here" -> `time` (seconds on the timeline)
   - "before/after this" -> relative to `time`
   Check `updated` is recent; if it is stale or `selected` is empty for an ambiguous request, ask one short question.
   `language` is the UI language (`en`/`tr`): answer the user in it.
4. Make the smallest edit that does the job (`edit_project` if you have it; otherwise the Edit tool on the JSON, or a small script), keep valid JSON, keep every untouched layer and its `id` byte-for-byte. The editor polls the file and reloads; a 409 on its side means it saw your write, nothing for you to do.

**Notes.** `notes` in project.json are the user's timeline comments ("sound a bit loud here" at `t`), shown in the editor's Notes panel. Each has `done` (bool), `author` (`user`/`agent`), an optional one-line `reply`. When the user says "apply my notes" / "notlarımı uygula" (or asks you to handle a note): call `get_notes` (open notes, each with the layers on screen at its time), handle each one at its time with `edit_project`, look at the frame (`render_frame`) and check that what you moved lines up with its surroundings, then `update_note` it with `done: true` and a one-line `reply` saying what you changed (e.g. "Blur reduced 22→8"; the user reads it under the note). Do not delete notes; resolve them. If the user watches and sends notes from the editor, loop **`notes_wait` (CLI `kurgu notes wait <dir>`; how to wait depends on your agent, see "Watch + note loop: rules" below) → fix → `notes_done` (`kurgu notes done <dir> <id...> --reply "..."`) → `notes_wait` again**: a saved cursor makes the next wait return a send that happened while you were fixing; `NOTLAR.md` shows each note with its frame picture and any marks he drew (with hint + layers under the mark). Use `add_note` to flag something you noticed but did not change. Without MCP: set `"done": true` and `"reply": "..."` on the note in project.json.

## Watch + note loop: rules

<!-- kurgu:generated:rules -->
**Placing or moving something for a note.** Each note lists the layers then on screen with `bounds` (`left/top/right/bottom/w/h`, project px, the rendered box) and an `anchor` line saying what the layer's x/y mean. For a text layer x/y is the block CENTRE even with `align=left` (align only aligns lines inside the block), so the left edge is `x - w/2`. Compute x/y from the anchor and bounds so an edge lines up with nearby content: another layer's bounds, or something visible in the note frame (text burned into a video clip is not a layer, so read its position off the picture; `kurgu detect` gives approximate boxes). Keep the box inside the frame.

**Look before you close.** After the edit run `kurgu frame <dir> <seconds> -o /tmp/look.jpg` (MCP `render_frame`) at the note's time, view the image, adjust, and only then `notes_done`. The default frame is the half-size draft (`--full` for project size), so image px x2 = project px.

**Vague notes (interpret or ask).** If the note gives a direction or target, apply the best change in that direction and state the values and any remaining compromise in the reply. Ask only when no reading satisfies the note's core request, or the change would be destructive. A vague note with no direction gets the smallest clear reading, stated with its values. To ask, leave the note open with `kurgu notes ask <dir> <id> --question "..."` (MCP `notes_ask`). After `notes ask` the note is `asked`: the editor shows your question with an answer box, and the answer returns as a new send (`question` + `answer` on the note). Every `--reply` states the concrete values you chose and how to change them ("start moved to 4.6 s; lower `start` for earlier") plus every compromise or unchecked part: the human reads the reply, not your output. Never copy a template or plugin file in as a stand-in for a requested asset (a subscribe animation, a logo): if it is missing, ask. Text burned into the footage cannot be edited: say what is possible (cut, speed, an overlay of a frozen frame) and that such overlays are fragile when the layer under them has keys.

**Burned-in text and shapes.** Pixels Kurgu did not draw (text or shapes in footage, 3D or other tools' renders) have no layer `bounds` and no element map. `kurgu detect <dir> <seconds> [--layer ID]` (alias `kurgu ocr`; MCP `detect`) finds text lines and shapes with boxes in PROJECT px. They are approximate (`approx: true`): still look at the frame before aligning anything to them.

**Code-made clips (`source`).** A layer in a note's `layers` that has `source` (`kind`, `unit`, `file`, `frame`, `time_in_unit`, `render`) was produced by code. Fix the SOURCE file (and line, when given), at the source `frame` shown, instead of adding overlay layers or patching keys on the timeline. For alignment use `elements_under` on a mark (`under mark:` in NOTLAR.md): project-px boxes plus `file:line`, measured from the clip's element map, not estimated. After a change in the source, run `kurgu rerender <dir> <unit>` (MCP `rerender`; the unit is `source.unit`, or give the layer id): it regenerates only that unit with the adaptor named in `source.render`; the editor reloads it. Which frames belong to the unit comes from the scene list (`source.list`, e.g. `shots.json`, read fresh each time; `source.unit_start_frame` + `source.frames` when there is no list): change a scene's length THERE, then rerender. When several scenes share one film file the unit gets its own file (`film.<unit>.mp4`) and its layer is pointed at it; the film and the other scenes are never rewritten. The unit's layers get the new end when the length changed, and the output names the later scene layers you must shift (by how many seconds); `project.json` is otherwise not touched. Audio is the matching slice of the old file. Then LOOK (`kurgu frame` / `kurgu strip`) before `notes done`. Chrome cannot start inside Codex's macOS sandbox: there `kurgu rerender` says so. The video is then UNCHANGED, so do NOT mark the note done: fix the source, then ask (`kurgu notes ask <dir> <id> --question "Source fixed; please run: kurgu rerender <project> <unit>"`), or leave it sent and say so in your reply. For a cinecode film (`source.kind` cinecode) the sound mix also lives in `<film folder>/audio/mix.json` (`gain_db` per bus): when you change volume or envelope in project.json, change it there too, or a re-import brings the old mix back (`kurgu help audio`). If the Kurgu in front of you has no `rerender`, say in your reply that a re-render is needed. Layers without `source`: all the rules above stay as they are. The element map comes from `kurgu elements <page> --frames ...` and `kurgu import <folder> [--elements]` builds a project from a scene list (see SPEC.md section 11).

**Notes follow their content.** A note may carry an `anchor` (the code-made unit and source frame, the layer under its mark, or the scene layer it was written on). When you lengthen a scene and shift the later layers, or after `kurgu rerender`, do NOT move the notes yourself: Kurgu re-derives each anchored note's `t` on every save and read. `anchor.stale: true` means its layer is gone, was replaced (same id, another type or clip) or no longer covers that moment; its `t` was left as it was. To move a note on purpose change its `t` (MCP `update_note`); it is anchored again there.

**Checking over time.** For a fade, a move or timing against other layers make ONE call, `kurgu strip <dir> <t0> <t1> --n 6` (MCP `render_strip`): a single labelled contact sheet, so view one JPEG instead of making many `kurgu frame` calls (frame is for one moment or full detail). Each layer in a note's `layers` has `start`, `end` and, if animated, `keys`; read them first to know when to look, then confirm with one strip over [start, end]. Tile px x the printed scale = project px.

**Audio facts.** (Layer volume, `envelope` for ducking and the cinecode `audio/mix.json`: `kurgu help audio`.) Each note's `audio` block (inbox JSON; a `- audio:` line in NOTLAR.md) is already measured around the note time per sound layer, in project seconds: `peak_db`/`rms_db` (volume and envelope included), `silences`, and `words` when a transcript exists. Use it instead of running ffprobe/ffmpeg yourself; a layer with `error` was not analysed. Bounds flagged `approx` / "(estimate)" are a fallback: check them on the frame before moving or aligning anything.

**One command per call.** When woken headlessly, run one command per call (no `;` / `&&` chains) and use only: `kurgu notes wait|list|done|ask|add <dir> ...`, `kurgu frame <dir> <sec>`, `kurgu strip <dir> <t0> <t1>`, `kurgu rerender <dir> <unit>`, `kurgu detect <dir> <sec>`, `kurgu probe <dir> [layer]` (media facts: use it, not ffprobe), and read-only discovery: `kurgu --help`, `kurgu <command> --help`, `kurgu help <topic>` (topics: `audio` = volume, envelope for ducking, cinecode mix.json); no python, ffmpeg, ffprobe or render.py. If something does not work, `kurgu report <dir>` prints one plain-text report to paste into an issue (nothing is sent anywhere).

**"Kurgu notlarımı bekle" / "Wait for my Kurgu notes".** The editor's *Send* box offers a copyable phrase (Turkish: "Kurgu notlarımı bekle ve gelince uygula"). When the user says it, enter the loop and stay in it: `notes_wait` (or `kurgu notes wait <dir>`, see below for how to wait on your agent) → fix → `notes_done` → `notes_wait` again, until the user tells you to stop. The editor shows that an agent is listening (a heartbeat in `.kurgu/listening.json` while `notes wait` blocks). "Apply my Kurgu notes" without "wait" means one pass over what is already sent.

**Which session gets woken.** Kurgu records the agent session that runs its commands (or the MCP server) in this project in `.kurgu/agents.json`, from the environment only, never from project files. The wake hook (`kurgu setup-agent --claude|--codex --wake`) then resumes exactly that session, not just "the last one in the folder"; a session older than a day, or one with no recorded id, falls back to a new headless run. You do nothing for this beyond running `kurgu` commands from the session the user talks to.

**Which agent are you? (waiting for the user's notes).** The loop is always: check for sends, fix each note, `notes_done`, check again. Only the waiting differs. At the **start of every turn**, look once whether notes arrived: `notes_wait` with `timeout: 0`, or `kurgu notes wait <dir> --timeout 0` (exit 3 = nothing new).

- **Claude Code:** run `kurgu notes wait <dir>` (no timeout) as a background command; you are re-invoked when it exits.
- **Codex (CLI / IDE / desktop) and other agents:** a background process does not wake you, and long shell calls may be yielded or cut (~30 s observed). Wait with the MCP `notes_wait` tool (`timeout: 120`, or 3000 when the user says they are watching live; register it with `kurgu setup-agent --codex`, which raises Codex's 60 s tool timeout to 3600 s) and call it again when it times out. Without MCP: `kurgu notes wait <dir> --timeout 20`, repeated on exit 3. Or let Kurgu start you: `kurgu setup-agent --codex --wake` makes every Send run `codex exec resume` in the project folder (see AGENTS.md). If you cannot loop, say "send your notes, then tell me 'check notes'" and stop.
- Codex needs write access to the project folder and to run `ffmpeg`/`kurgu-render`: use sandbox `workspace-write` (start Codex inside the project folder or add it with `--add-dir`), and allow the editor's localhost server (`127.0.0.1`) if your approval policy asks.
<!-- kurgu:generated:end -->

## Rules

- Keep ids stable. Never renumber or regenerate ids of existing layers. New layers get new unique ids.
- Do not overwrite `project.json` with a freshly generated whole file when the user has been editing; patch it.
- Do not touch `.kurgu/` except reading `state.json`. Do not commit it.
- Layer order = z-order; `group` is display only.
- Times are seconds on the timeline. `end` is exclusive. `duration` must cover the last layer's `end`.
- Text `font` is a font **id** (slug), not a file name. List them with the running editor: `curl -s http://127.0.0.1:<port>/api/fonts`, or use the bundled ids in reference.md. Unknown ids fall back to `inter-regular` with a `WARN` line.
- Turkish text needs a font with ş ğ ı İ ö ü ç: all bundled fonts have them.
- No personal or private data in `project.json`; it may be shared.
- **Linked pairs:** layers that share the same `link` string are one clip in the editor (usually a `video` layer with `muted: true` plus an `audio` layer of the same `src`/`src_in`/`start`/`end`). The user moves, trims, splits and deletes them together. When you edit one, edit its partner in the same `edit_project` call (same `start`/`end`, and `src_in` shifted alike), or detach it first with `link: null` (the unlink). `get_project` summary lists linked pairs and marks OUT OF SYNC ones; `edit_project` warns when you leave one out of sync. Split a linked clip by changing both halves' partners too: new second halves get a NEW shared `link` id. Do not unmute a muted picture layer whose sound lives on its linked audio layer (double sound). `link` never changes the render.
- Sound: prefer `volume_db` and `envelope` over re-encoding; loudness is normalised on render by `global.loudness_lufs`.

## Animating (keyframes)

Animate with `keys` (values over layer-local time), `mods` (seeded wiggle) and `use` (presets); details in `reference.md` > Animation. Habits that keep motion tasteful: entrances `out-*` easing 0.25-0.5 s, exits `in-*` 0.2-0.35 s, at most one emphasis at a time, do not animate everything, keep text on screen at least ~1 s. Three patterns:
1. **Title fade-up (ease-out):** `"keys":{"y":[{"t":0,"v":<y+60>,"ease":"out-cubic"},{"t":0.4,"v":<y>}],"opacity":[{"t":0,"v":0,"ease":"out-quad"},{"t":0.3,"v":1}]}`.
2. **Punch-in on a beat:** `"use":[{"preset":"cam.punch","at":[4.2,8.4],"params":{"amount":1.1,"settle":0.25}}]` (`at` = timeline seconds; baked into scale keys by `edit_project`).
3. **Shaky cam on an impact:** `"use":[{"preset":"cam.shake","at":12.4,"dur":0.5,"params":{"amp":18,"decay":5}}]`; same seed = same shake in preview and render.
Set a whole track with one `edit_project` `update`: `{"id":"title","set":{"keys":{"opacity":[...]}}}` replaces just that track (`null` removes it). Look at the result with `render_frame` at 2-3 moments inside the motion, not only at the ends. Keyframes are layer-local: when you move a layer's `start`, its animation moves with it.

## Rendering

If `kurgu-render` is not on the PATH, prefix it with `uvx --from "${CLAUDE_PLUGIN_ROOT}"`.

```
kurgu-render <dir> --draft              # half-res preview -> <output>_draft.mp4
kurgu-render <dir>                      # full quality -> project.output
kurgu-render <dir> --frame 12.3 --output f.png [--draft]   # one PNG
```
stdout is line-based: `PROGRESS 0.42`, `DONE <path>`, `ERROR <message>` (non-zero exit). Run long renders in the background and poll. **Draft-then-full habit:** render `--draft` (or a few `--frame`s) while iterating, full only when the user says it is right. On `ERROR layer "<name>" (<id>): file not found`, fix the `src`.

## Common recipes

- **Captions from speech (local, optional):** `transcribe` `{layer_id}` (words in timeline time; first run downloads the Whisper model, so say so) -> `make_captions` `{layer_id}` (one `text` layer per chunk, group "Captions", ids `cap_1..`; defaults by aspect: vertical 3 words at 66% height, 16:9 7 words at 86%) -> `render_frame` at a few caption times -> adjust with `edit_project` `update` on the `cap_*` layers (font, size, colour, `y`; or re-run `make_captions` with options, which replaces the old `cap_*`). If the tool says speech-to-text is not installed, give the user its one-line pip hint (`pip install mlx-whisper` on Apple Silicon, `pip install faster-whisper` elsewhere). Fix misheard words by editing the layer `text`, not by re-running.
- **Subtitles (by hand):** one `text` layer per line/phrase, `group: "Subtitles"`, bottom third (`y` about 0.85 x height), `shadow` on, `fade_in`/`fade_out` 0.1-0.15 s.
- **Title card:** `color` layer + big `text`; or text over video with a slow `zoom` on the video.
- **Music bed:** `audio` layer spanning the video, `volume_db` -18 to -12; duck under speech with `envelope` points.
- **Cut / trim:** change `start`/`end`, and `src_in` to pick the source moment. To remove a middle part, split into two video layers.
- **Crop to vertical:** project `width: 1080, height: 1920`, video `fit: "cover"`, shift with `x`.
- **Fade to black at the end:** `global.fade_out`.

## Reference

Everything about fields, defaults, effects, envelopes, fonts and one complete example: `reference.md` (same folder).
