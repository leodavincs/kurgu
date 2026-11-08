Audio in a Kurgu project.json (each `audio` layer, and a `video` layer's own sound unless `muted`):

  volume_db   number, default 0. Constant gain in dB for the whole layer.
  envelope    [[t, dB], ...]  gain over TIMELINE seconds on top of volume_db: linear in dB between points, held at the first/last value outside them,
              empty = 0 dB. Example, music ducked 6 dB under speech from 1.5 s to 6 s: [[0, 0], [1, 0], [1.5, -6], [6, -6], [7, 0]]
              Total gain = 10^((volume_db + envelope(t)) / 20) x fade.
  fade_in, fade_out   seconds, linear gain ramps at the layer's start / end.
  muted       true silences a layer (a separated video keeps muted: true; its `audio` layer holds the volume).
  start, end  timeline seconds the layer plays; src_in = media time at `start`.
  link        a video + audio pair made from one clip share a link id; keep their start - src_in equal.
global.loudness_lufs (default -14) normalises the whole mix; global.fade_out fades the end.

Each note carries an `audio` block (peak_db / rms_db / silences / words per sound layer around the note time, with the layer's own volume and envelope
applied): read it instead of measuring. `kurgu probe <project> [layer-id]` gives a media file's duration and streams.

Imported cinecode films keep their mix in <film folder>/audio/mix.json ({"layers": [{"file", "at", "bus", "gain_db", "fade_in", "fade_out"}]}): after you change
volume or envelope in project.json for such a film, change gain_db / fades there too, or a later `kurgu import` brings the old mix back.
