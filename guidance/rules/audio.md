@@ block
Notes carry layer `start`/`end`/`keys`, `audio`; `approx` bounds: check on the frame.
@@ full
**Audio facts.** (Layer volume, `envelope` for ducking and the cinecode `audio/mix.json`: `kurgu help audio`.) Each note's `audio` block (inbox JSON; a `- audio:` line in NOTLAR.md) is already measured around the note time per sound layer, in project seconds: `peak_db`/`rms_db` (volume and envelope included), `silences`, and `words` when a transcript exists. Use it instead of running ffprobe/ffmpeg yourself; a layer with `error` was not analysed. Bounds flagged `approx` / "(estimate)" are a fallback: check them on the frame before moving or aligning anything.
