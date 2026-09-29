# project.json reference (v2)

Authoritative contract: `SPEC.md` at the plugin root. This is the condensed version.

## Top level
| key | default | notes |
|---|---|---|
| `version` | 2 | |
| `name` | | shown in the editor |
| `width`, `height`, `fps` | 1920, 1080, 30 | output size |
| `duration` | | seconds |
| `background` | `#000000` | |
| `output` | `output.mp4` | relative to the project dir |
| `global.grain` | 0 | 0-10 film grain |
| `global.vignette` | 0 | 0-1 |
| `global.loudness_lufs` | -14 | `null` = no loudness normalisation |
| `global.fade_out` | 0 | seconds; picture to black and sound to silence at the end |
| `layers` | `[]` | FIRST element = front-most |
| `notes` | `[]` | `{id, t, text, created, done, author, resolved_at, layer_ids, reply}` comments pinned to the timeline (only id/t/text needed; `done` bool, `author` user/agent, `reply` one-line agent answer) |

## Layer fields (all types)
`id` (required, unique) · `name` · `type` (`video|image|text|color|audio`, required) · `group` (display heading only) · `link` (string, optional: layers sharing it are a linked clip, e.g. video + its sound; editor-only, no effect on render; remove with `null`) · `hidden` · `locked` · `start`, `end` (s, required, active in `[start, end)`) · `fade_in`, `fade_out` (s, linear; opacity for visual layers, gain for audio).

## Visual layers (video, image, text, color)
`x`, `y` centre of the layer in output pixels (default width/2, height/2) · `scale` (1) · `opacity` (0-1, 1) · `effects` (list) · motion: `rotation`, `scale_x`, `scale_y`, `anchor_x`, `anchor_y`, `keys`, `mods`, `use` (see Animation below).
`color` layers fill the frame and ignore x/y/scale.

## video
`src` (relative to project dir, or absolute) · `src_in` (source time at `start`, 0; source time at t = `src_in + (t - start)`) · `fit` (`cover` default | `contain` | `native`) · `crop` (`{top,bottom,left,right}` fractions 0-0.5, applied before fit) · `volume_db` (0) · `muted` (false) · `envelope`.
Shorter source: last frame is held.

## image
`src`, `fit` (default `contain`), `crop`. No sound.

## text
`text` (`\n` = line break) · `font` (id, default `inter-regular`) · `size` (px, 48, times `scale`) · `color` (`#f1efe9`) · `letter_spacing` (fraction of size, 0) · `line_height` (1.25) · `align` (`center|left|right`, lines within the block) · `shadow` (`{"enabled": true, "blur": 9, "opacity": 0.7}`).
The text block is centred on `(x, y)`. Missing glyphs fall back to `inter-regular`.

## audio
`src`, `src_in`, `volume_db`, `muted`, `envelope`.
A separated video: the `video` layer has `muted: true`, the `audio` layer copies `src`, `src_in`, `start`, `end` and holds the volume/envelope; both carry the same `link`. Keep such a pair in sync (a pair is in sync when `start - src_in` is equal).
`envelope`: `[[t, dB], ...]`, `t` in timeline seconds, linear in dB between points, held outside, empty = 0 dB. Gain = `10^((volume_db + envelope(t))/20) x fade`.

## color
`color`: `"#000000"`.

## Effects
`effects` is an ordered list of `{"type": ..., "enabled": true, ...params}`:
| type | params | meaning |
|---|---|---|
| `grayscale` | `amount` 0-1 (1) | |
| `brightness` | `value` -1..1 | |
| `contrast` | `value` -1..1 | |
| `saturation` | `value` -1..1 | |
| `blur` | `radius` px | Gaussian |
| `zoom` | `from` (1), `to` (1.06) | slow push-in over the layer's duration (video/image only) |
| `freeze` | | hold the `src_in` frame for the whole layer (video/image only) |
Colour effects also work on text. Colour maths matches CSS filter functions.

## Animation (keys, easing, wiggle, presets)
Everything below is optional; a layer without it is static. Full contract: SPEC §1.8.
- **Transform fields:** `rotation` (deg, clockwise) · `scale_x`, `scale_y` (x `scale`; negative mirrors) · `anchor_x`, `anchor_y` (0-1 of the layer box, 0.5 = centre; `x`,`y` is where the anchor sits, so `anchor_x: 0` scales/rotates from the left edge).
- **`keys`:** `{"<prop>": [{"t": s, "v": n, "ease": "..."}, ...]}`. `t` is **layer-local** seconds (0 = the layer's `start`). Animatable props: `x y scale scale_x scale_y rotation anchor_x anchor_y opacity volume_db` and effect params `fx.<effect id>.<param>` (give the effect an `"id"`). Value is held before the first / after the last key. `ease` belongs to the segment leaving that key; `"hold": true` = jump at the next key. Times must not decrease.
- **Eases:** `linear` `hold` `ease` `ease-in` `ease-out` `ease-in-out`, `in|out|in-out` + `sine quad cubic quart quint expo circ back elastic bounce` (`out-back`, `out-expo`, `in-out-sine`...), `[x1,y1,x2,y2]` / `"cubic-bezier(.7,0,.84,0)"`, `"steps(6)"`. Entrances: `out-*` (0.25-0.5 s); exits: `in-*` (0.2-0.35 s); pop: `out-back`; snappy: `out-expo`; soft: `in-out-sine`.
- **`mods`:** `{"<prop>": [{"type": "wiggle", "amp": 8, "freq": 5, "octaves": 2, "seed": 1, "t0": 0, "t1": 0.5, "decay": 6}]}` adds seeded smooth noise to the value (`amp` in the prop's units, `freq` Hz, window `t0`-`t1` layer-local, `decay` fades out from `t0`). `"noise"` = harsh sample-and-hold jitter. Same seed = same shake, every time, in preview and render.
- **Presets (`use`)**, `at` in TIMELINE seconds: `cam.punch` (`amount` 1.12, `settle`, `hold`), `cam.ken-burns` (`from`/`to` `{scale,x,y}`), `cam.shake` (`amp` px, `rot` deg, `freq`, `decay`, `dur`). `edit_project` bakes them into keys/mods (the file then has plain keys you and the user can edit).
```json
{"id":"title","type":"text","text":"GO","start":1,"end":5,"y":540,"size":140,
 "keys":{"y":[{"t":0,"v":600,"ease":"out-cubic"},{"t":0.4,"v":540}],"opacity":[{"t":0,"v":0,"ease":"out-quad"},{"t":0.3,"v":1}]}}
{"id":"clip","type":"video","src":"media/a.mp4","start":0,"end":20,
 "use":[{"preset":"cam.punch","at":[4.2,8.4,12.6],"params":{"amount":1.1,"settle":0.25}}]}
{"id":"clip2","type":"video","src":"media/b.mp4","start":20,"end":26,
 "use":[{"preset":"cam.shake","at":22.4,"dur":0.5,"params":{"amp":18,"decay":5}}]}
```
Values are additive on top of the layer: static `x`,`y`,`scale` are ignored for a property while it has keys. Effect example: `"effects":[{"type":"blur","id":"b1","radius":0}]` + `"keys":{"fx.b1.radius":[{"t":0,"v":24,"ease":"out-cubic"},{"t":0.4,"v":0}]}`.

## Fonts
Ids are `slug(family + " " + style)`, lowercase, spaces to `-`. Sources: project `fonts/`, bundled, system. Bundled ids: `inter-light`, `inter-regular`, `inter-medium`, `inter-semibold`, `inter-bold`, `bebas-neue-regular`, `jetbrains-mono-regular`, `jetbrains-mono-bold`, `space-grotesk-regular`, `space-grotesk-bold`, plus a cinematic serif (`cormorant-garamond-*` or `playfair-display-*`; confirm via `/api/fonts`). System fonts appear as e.g. `helvetica-neue-regular`, `gill-sans-light`. To use a custom font drop a .ttf/.otf/.ttc in `<dir>/fonts/`; its id follows the same rule.
List the real ids from a running editor: `curl -s http://127.0.0.1:8765/api/fonts`.

## Minimal complete example
```json
{
  "version": 2,
  "name": "Launch teaser",
  "width": 1920, "height": 1080, "fps": 30,
  "duration": 8,
  "background": "#000000",
  "output": "output.mp4",
  "global": { "grain": 1, "vignette": 0.25, "loudness_lufs": -14, "fade_out": 1 },
  "layers": [
    { "id": "title", "type": "text", "name": "Title", "group": "Titles",
      "start": 1, "end": 6, "fade_in": 0.5, "fade_out": 0.5,
      "text": "Made with Kurgu", "font": "inter-bold", "size": 96,
      "color": "#f1efe9", "y": 540,
      "shadow": { "enabled": true, "blur": 9, "opacity": 0.7 } },
    { "id": "sub", "type": "text", "name": "Subtitle", "group": "Titles",
      "start": 2, "end": 6, "fade_in": 0.3,
      "text": "Claude writes it. You fine-tune it.", "font": "inter-regular",
      "size": 40, "y": 680 },
    { "id": "bg", "type": "video", "name": "Background clip", "group": "Picture",
      "src": "media/clip.mp4", "src_in": 3.5, "start": 0, "end": 8,
      "fit": "cover", "muted": true,
      "effects": [
        { "type": "zoom", "enabled": true, "from": 1, "to": 1.08 },
        { "type": "brightness", "enabled": true, "value": -0.25 }
      ] },
    { "id": "music", "type": "audio", "name": "Music", "group": "Sound",
      "src": "media/music.mp3", "start": 0, "end": 8, "volume_db": -12,
      "fade_in": 1, "envelope": [[0, 0], [1, 0], [1.5, -6], [6, -6], [7, 0]] }
  ],
  "notes": []
}
```

## Captions from speech (optional)
`transcribe {layer_id|path, model?, language?}` returns `[start, end, word, prob]` rows (timeline seconds for a layer). `make_captions {layer_id|path, max_words_per_line?, max_chars?, font?, size?, y?, uppercase?, color?, replace?}` adds `text` layers `cap_1, cap_2, ...` in group `Captions` (`x` = width/2, `y` 0-1 = fraction of height, shadow on). Vertical default: 3 words, `y` 0.66 (social safe zone 0.62-0.70), size 7.8% of width; 16:9: 7 words, `y` 0.86. Models: tiny, base, small (default), medium, large-v3, large-v3-turbo. Install: `pip install mlx-whisper` (Apple Silicon) or `pip install faster-whisper`.

## Templates
`templates/index.json` lists ready-made projects (id, EN/TR name, aspect, duration, thumbnail, description, tags): `captions-vertical` (9:16 word-by-word captions), `youtube-intro` (16:9 title card), `product-promo` (1:1, 15 s), `quote-stat-card` (4:5), `lower-thirds` (three styles). Each folder has `project.json`, `README.md` (what to replace, a sample prompt) and `thumbnail.png`.
To start from one: `python3 scripts/examples/new_project.py <id> <dir>` (copies the folder and the shared clips/music/SFX from `templates/_media/` into `<dir>/media/`, rewrites `src`), open it, then **patch** `project.json` (keep ids; edit text, `src`, `start`/`end`, colours). `examples/showcase` is the full 26 s reference piece (same command works). Generated media (clips, music at -16 LUFS, SFX) come from code: `python3 scripts/examples/generate_all.py`.
