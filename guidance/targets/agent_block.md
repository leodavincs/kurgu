## Kurgu (video as code)

Kurgu is a local video editor. You write `<dir>/project.json`; the human fine-tunes it in a browser and watches it. Use the `kurgu` MCP tools if you have them (`get_state`, `get_changes`, `edit_project`, `render_frame`, `notes_wait`, `notes_done`, ...); otherwise edit the JSON and run `kurgu-render`. Fields: `skills/kurgu/reference.md`.

**Before every edit:** `get_state` + `get_changes` (or re-read `project.json`, the tail of `changes.md`, `.kurgu/state.json`). Keep layer ids stable and the human's hand edits.

**Watch + note loop** (the human drops timestamped notes, maybe with marks, and clicks Send):
1. {{rules/waiting#start}}
2. Fix each note, then `notes_done` / `kurgu notes done <dir> <id...> --reply "what you changed"`.
   - {{rules/align#block}}
   - {{rules/look#block}}
   - {{rules/interpret-or-ask#block}}
   - {{rules/detect#block}}
   - {{rules/source#block}}
   - {{rules/asked#block}}
   - {{rules/strip#block}} {{rules/audio#block}}
   - {{rules/one-command#block}}
3. {{rules/waiting#keep}}
- {{rules/waiting#claude}}
- {{rules/waiting#codex}}
