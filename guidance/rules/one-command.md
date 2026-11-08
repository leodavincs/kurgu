@@ block
One command per call, no `;`/`&&`. Help: `kurgu --help`, `kurgu help audio`. Details: skill `kurgu`.
@@ wake
Komutları tek tek çalıştır; `;`/`&&` zinciri, python, ffmpeg yok.
@@ full
**One command per call.** When woken headlessly, run one command per call (no `;` / `&&` chains) and use only: `kurgu notes wait|list|done|ask|add <dir> ...`, `kurgu frame <dir> <sec>`, `kurgu strip <dir> <t0> <t1>`, `kurgu rerender <dir> <unit>`, `kurgu detect <dir> <sec>`, `kurgu probe <dir> [layer]` (media facts: use it, not ffprobe), and read-only discovery: `kurgu --help`, `kurgu <command> --help`, `kurgu help <topic>` (topics: `audio` = volume, envelope for ducking, cinecode mix.json); no python, ffmpeg, ffprobe or render.py. If something does not work, `kurgu report <dir>` prints one plain-text report to paste into an issue (nothing is sent anywhere).
