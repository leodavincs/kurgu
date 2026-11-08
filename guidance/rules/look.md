@@ block
Before `notes_done` LOOK: `kurgu frame <dir> <seconds>` (MCP `render_frame`) at the note time, view it, correct, then close.
@@ wake
düzelttikten sonra `kurgu frame {dir} <saniye>` ile kareye bak {{rules/strip#wake}}, ancak sonra `kurgu notes done {dir} <id> --reply ...` ile kapat.
@@ full
**Look before you close.** After the edit run `kurgu frame <dir> <seconds> -o /tmp/look.jpg` (MCP `render_frame`) at the note's time, view the image, adjust, and only then `notes_done`. The default frame is the half-size draft (`--full` for project size), so image px x2 = project px.
@@ tool
Look at the result with render_frame (`kurgu frame`) before notes_done.
