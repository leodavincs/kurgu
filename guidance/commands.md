@@ help
notes :: list | wait | done | ask | add the notes you sent from the editor (the agent's side of Send)
frame :: <project> <seconds>  one JPEG of the project as it is now (no browser needed)
strip :: <project> <t0> <t1>  n frames as one labelled contact sheet
rerender :: <project> <layer|unit>  regenerate one code-made clip from its source
elements :: <page.html> --frames 0:100:5  where the text and shapes are in a frame-function page
import :: <folder>  turn a scene list (scenes.json, cinecode shots.json) into a Kurgu project
probe :: <project> [layer|file]  duration, size, frame rate and streams of the project's media (no ffprobe needed)
help :: [topic]  explain a topic: audio = volume, envelope for ducking, cinecode mix.json
detect :: <project> <seconds>  find text and shapes burned into a clip (alias: ocr)
setup-agent :: register Kurgu with Claude Code / Codex (add --wake to start the agent on every Send)
wake :: <claude|codex> <project> [seq]  start the agent for sent notes (what the on_send hook runs)
report :: [project]  write an error report (log, wake status, versions) you can paste into an issue
doctor :: [project] check Python, ffmpeg, Chrome and the project (--fix repairs what it can)
setup-ffmpeg :: download a static ffmpeg for Kurgu
@@ agents
python3 server.py <dir> [--port 8765] [--no-open]                  # editor
python3 render.py <dir> [--draft] [--output f.mp4]                 # video
python3 render.py <dir> --frame 12.3 --output f.png [--draft]      # one frame
python3 mcp_server.py                                              # MCP server on stdio
kurgu notes wait|list|done|ask|add <dir> ...                           # watch + note loop (python3 server.py notes ...)
kurgu frame <dir> <seconds> [-o out.jpg] [--full]                  # the project as it is now at that time -> JPEG, prints the path
kurgu strip <dir> <t0> <t1> [--n 6] [-o out.jpg] [--full]          # n frames over time -> ONE labelled contact-sheet JPEG
kurgu probe <dir> [layer-id|file]                                   # duration, size, fps, streams of the project's media (JSON; no URLs)
kurgu detect <dir> <seconds> [--layer ID] [--json]                 # text and shapes burned into a clip (alias: ocr)
kurgu rerender <dir> <layer|unit> [--dry-run]                      # regenerate one code-made clip from its source
kurgu elements <page.html> --frames 0:100:5                        # element map of a frame-function page
kurgu import <folder> [--elements]                                 # scene list -> Kurgu project
kurgu report [<dir>]                                               # plain-text error report to paste
python3 -m pytest -q
@@ readme
Agent side: `kurgu notes wait | list | done | ask | add`, `kurgu frame <project> <seconds> [-o out.jpg] [--full]`, `kurgu strip <project> <t0> <t1> [--n 6]`, MCP `notes_wait`, `notes_list`, `notes_done`, `notes_ask`, `render_frame`, `render_strip`. `kurgu detect <project> <seconds> [--layer ID] [--json]` (alias `kurgu ocr`, MCP `detect`) finds text and shapes burned into a clip, which have no layer bounds; `kurgu rerender <project> <layer|unit>` (MCP `rerender`) regenerates a code-made clip from its source. Say "Wait for my Kurgu notes" and the agent stays in the wait, fix, done loop; Kurgu records which agent session works on the project in `.kurgu/agents.json` so the wake hook resumes that one. Details in `AGENTS.md` and `SPEC.md`.
@@ readme-tr
Ajan tarafı (teknik): her notta ayrıca `bounds` (kutu) ve `anchor` (konum açıklaması) alanları gider. Komutlar: `kurgu notes wait | list | done | ask | add`, `kurgu frame <proje> <saniye> [-o cikti.jpg] [--full]`, `kurgu strip <proje> <t0> <t1> [--n 6]`, MCP `notes_wait`, `notes_list`, `notes_done`, `notes_ask`, `render_frame`, `render_strip`. Klibe gömülü yazı ve şekiller için `kurgu detect <proje> <saniye> [--layer ID] [--json]` (takma ad `kurgu ocr`, MCP `detect`); kodla üretilmiş klibi kaynağından yeniden üretmek için `kurgu rerender <proje> <katman|birim>` (MCP `rerender`). "Kurgu notlarımı bekle ve gelince uygula" dersen ajan bekle, düzelt, kapat döngüsünde kalır; Kurgu projede hangi ajan oturumunun çalıştığını `.kurgu/agents.json` içine yazar, uyandırma kancası o oturumu sürdürür. Ayrıntı `AGENTS.md` ve `SPEC.md` içinde.
