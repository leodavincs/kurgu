---
name: kurgu
description: Make or edit a video as code with Kurgu. Use when the user asks Claude to create, edit, cut, trim, caption, subtitle, add titles or text, music, voice-over, fades, effects, colour changes or a logo, or to render or export a video, or mentions project.json, the Kurgu editor, "this text", "here" or "right now" in a video. Claude writes project.json (a JSON timeline); the user fine-tunes it in a local browser editor; render.py produces the mp4.
---

# Kurgu: video as code

You write `project.json`. The user opens the same file in a local browser editor (started with `/kurgu`), adjusts it by hand, and you keep iterating together. `render.py` turns it into an mp4. Everything is local. Full field reference: `reference.md` in this skill folder (read it before writing a project from scratch). The formal contract is `SPEC.md` in the plugin root.

**MCP tools (prefer them when available).** This plugin also ships an MCP server (`kurgu`). If you can call `open_editor`, `get_state`, `get_project`, `edit_project`, `render_frame`, `render`, `get_changes`, `get_brief`, `update_brief`, `get_notes`, `update_note`, `add_note`, `list_fonts`, `list_media`, `import_file`, `transcribe` or `make_captions` (their names carry a `mcp__...kurgu__` prefix), use them instead of editing files by hand: `get_state` and `get_changes` are the re-read routine below in two calls, `edit_project` applies an atomic validated batch (`update`/`add`/`remove`/`project` + a one-line `summary`) through the running editor so it reloads live and logs `[agent]` lines in `changes.md`, and `render_frame` returns the still as an image you can look at directly. Every tool takes an optional `project_dir`. If the tools are not there, follow the file-based workflow below unchanged.

Paths: the plugin root is `${CLAUDE_PLUGIN_ROOT}`. `<dir>` is the project directory (the one holding `project.json`).

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

**Notes.** `notes` in project.json are the user's timeline comments ("sound a bit loud here" at `t`), shown in the editor's Notes panel. Each has `done` (bool), `author` (`user`/`agent`), an optional one-line `reply`. When the user says "apply my notes" / "notlarımı uygula" (or asks you to handle a note): call `get_notes` (open notes, each with the layers on screen at its time), handle each one at its time with `edit_project`, look at the frame (`render_frame`), then `update_note` it with `done: true` and a one-line `reply` saying what you changed (e.g. "Blur reduced 22→8"; the user reads it under the note). Do not delete notes; resolve them. Use `add_note` to flag something you noticed but did not change. Without MCP: set `"done": true` and `"reply": "..."` on the note in project.json.

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
