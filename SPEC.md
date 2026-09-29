# Kurgu — Specification v2

A local, Premiere/After-Effects-style editor for videos that **Claude Code writes as code**.
Claude writes `project.json`; the user fine-tunes it in the browser; `render.py` turns it into an mp4.
Everything runs on the user's own machine (`127.0.0.1`), nothing leaves it. Published in **English and Turkish**.

This document is the binding contract between parallel workers. Where it is silent, choose the simplest
reasonable behaviour and note it in a comment at the top of the file you touched.
v1 projects (Turkish keys) must keep working through migration (§7).

```
./                         (repo root; working name "kurgu")
  kurgu                    launcher:  kurgu [project_dir]   (default ".")
  server.py                local HTTP server (stdlib + fontTools)
  render.py                project.json -> mp4 / single png (numpy, Pillow, fontTools, ffmpeg)
  schema.py                defaults, normalisation, validation, v1 migration — imported by server.py and render.py
  fonts.py                 font registry: project fonts, bundled fonts, system fonts; face extraction
  fonts/                   bundled OFL fonts (static .ttf, Turkish glyphs required) + their licence files
  web/index.html, web/app.css, web/js/*.js, web/i18n/en.json, web/i18n/tr.json
  examples/basic/          small synthetic example project (was ornek/)
  tests/                   pytest
  .claude-plugin/, skills/, commands/   Claude Code plugin (§8)
  README.md, README.tr.md, LICENSE, SPEC.md, AGENTS.md
```
Code identifiers, comments, file names, JSON keys, API: **English**. User-facing UI text: through i18n only (§6).
Dependencies: Python ≥ 3.10, numpy, Pillow, fontTools, ffmpeg/ffprobe. macOS, Linux, Windows.
ffmpeg discovery (`media_tools.py`, used by every module): env `KURGU_FFMPEG` / `KURGU_FFPROBE` (a file, or a folder holding the tool), then PATH, then the usual install folders (Homebrew `/opt/homebrew/bin` + `/usr/local/bin`, MacPorts, `/snap/bin`; Windows winget Links + Packages, Chocolatey, Scoop, `C:\ffmpeg\bin`). A missing tool gives an actionable error naming the install command and the variable. Every input that comes from a project is opened with `-protocol_whitelist file,pipe`, and playlist / script containers (hls, concat, ffconcat, dash, rtsp, sdp, tee ...) are refused as sources (checked with ffprobe `format_name`).

---

## 1. `project.json`

```jsonc
{
  "version": 2,
  "name": "Go for Orbit",
  "width": 1920, "height": 1080, "fps": 30,
  "duration": 61.5,                 // seconds
  "background": "#000000",
  "output": "output.mp4",           // relative to the project dir
  "global": {
    "grain": 0,                     // 0-10 film grain
    "vignette": 0,                  // 0-1
    "loudness_lufs": -14,           // null = no loudness normalisation
    "fade_out": 0                   // seconds; picture and sound fade to black/silence at the end
  },
  "layers": [ /* FIRST element is FRONT-most (top of the layer list) */ ],
  "groups": { "Cards": {"color": "#c09a55"} },   // optional: timeline colour of a layer group (`layer.group`), "#rrggbb"; display only, absent = automatic
  "notes": [ {"id": "n_ab12", "t": 12.3, "text": "sound a bit loud here", "created": "2026-09-29T15:04:00",
              "done": false, "author": "user", "layer_ids": ["k_muzik"]} ]
}
```

### 1.0 Notes
Timeline comments. Only `id`, `t`, `text` are essential; every other field is optional and filled by `schema.normalize` (old files keep working):
| key | type | default | meaning |
|---|---|---|---|
| `id` | string | `n_<i>` | unique |
| `t` | s | 0 | timeline time the note is pinned to |
| `text` | string | `""` | the comment |
| `created` | local ISO time | `""` | when it was written |
| `done` | bool | false | resolved. Open notes are filled markers, done notes hollow |
| `author` | `user \| agent` | `user` | who wrote it (agents use `add_note`) |
| `resolved_at` | local ISO time | `""` | set when `done` becomes true, cleared when it is reopened |
| `layer_ids` | string[] | `[]` | layers that were active at `t` when the note was created (a snapshot; the panel shows the live set) |
| `reply` | string | `""` | one-line answer from an agent, shown under the note ("Blur reduced 22 to 8") |

### 1.1 Common layer fields
| key | type | default | meaning |
|---|---|---|---|
| `id` | string | required | unique |
| `name` | string | type name | shown in the layer list |
| `type` | `video \| image \| text \| color \| audio` | required | |
| `group` | string | `""` | layer-list heading ("Picture", "Titles", "Sound"); display only, z-order = array order |
| `link` | string | absent | **linked clips** (Premiere style): layers that share the same `link` id move, trim, split, delete, ripple and copy together in the editor; see §1.1a. Absent or `""` = independent. No effect on preview or render |
| `hidden` | bool | false | ignored by preview and render |
| `locked` | bool | false | not editable in the editor |
| `start`, `end` | s | required | timeline placement, active in `[start, end)` |
| `fade_in`, `fade_out` | s | 0 | linear ramp: opacity for visual layers, gain for sound |

Ramp factor `f(t) = clamp((t-start)/fade_in,0,1) · clamp((end-t)/fade_out,0,1)` (a factor is 1 when its fade is 0).

### 1.1a Linked clips (`link`)
A video with sound can be one `video` layer (own `volume_db`/`muted`/`envelope`, the legacy shape, still valid) or a **linked pair**: a `video` layer with `muted: true` in the picture group plus an `audio` layer with the same `src`, `src_in`, `start`, `end` in the sound group, both carrying the same `link` id (e.g. `"lk_ab12c"`). A pair renders exactly like the single layer (the muted video adds no sound; the audio layer mixes as any audio layer): `link` is metadata for editing only.
- The editor treats the layers of one `link` as a unit: selecting one selects all, move / trim / split / delete / ripple / copy-paste apply to all (Alt = one layer only). Vertical moves stay within a layer's own group.
- **Sync**: a pair is in sync when `start - src_in` is equal for its clips (same source alignment, within half a frame; different trims are fine). Otherwise the editor shows a red offset badge ("+0.4 s") and a Re-sync action (the sound follows the picture); MCP `get_project` marks OUT OF SYNC pairs and `edit_project` warns.
- **Import**: a dropped video that has sound becomes such a pair (setting "Import video with separate audio track", default on, `localStorage kurgu.importSeparateAudio`; off = one video layer with its own sound).
- Commands (each one undo step): Unlink (drop `link`), Link (selected video + audio layers get one id), Separate audio (legacy single video with sound becomes a pair, the audio layer takes its `volume_db`/`envelope`/fades and the video is muted), Merge audio back (inverse, when the audio layer is a pure copy).
- Agents: keep a pair in sync when editing (change both layers, or unlink first); to detach set `link` to `null`.

### 1.2 Visual layers (`video`, `image`, `text`, `color`)
| key | default | meaning |
|---|---|---|
| `x`, `y` | width/2, height/2 | position of the layer **centre** in output pixels |
| `scale` | 1 | multiplier (fitted size for video/image, font size for text) |
| `opacity` | 1 | 0-1 |
| `effects` | `[]` | §2 |
| `rotation`, `scale_x`, `scale_y`, `anchor_x`, `anchor_y`, `keys`, `mods`, `use` | see §1.8 | motion (optional; absent = static) |

Final alpha = `opacity(t) × f(t)` (`opacity(t)` is the static value unless keyed, §1.8). Normal source-over blending in sRGB (no linearisation).

### 1.3 `video`
| key | default | meaning |
|---|---|---|
| `src` | required | path, relative to the project dir or absolute |
| `src_in` | 0 | source time at `start`; source time at timeline `t` = `src_in + (t - start)` |
| `fit` | `"cover"` | `cover` fills the frame (overflow cropped) · `contain` fully visible · `native` source pixels 1:1 |
| `crop` | `{"top":0,"bottom":0,"left":0,"right":0}` | fractions 0-0.5 removed from the source **before** fitting |
| `volume_db` | 0 | the clip's own sound |
| `muted` | false | |
| `envelope` | `[]` | §1.6 |

Fit: cropped source size `(sw, sh)`; cover `k = max(W/sw, H/sh)`, contain `k = min(W/sw, H/sh)`, native `k = 1`.
Drawn size `(sw·k·scale·z(t), sh·k·scale·z(t))` centred on `(x, y)`; `z(t)` from the zoom effect (else 1).
If the source is shorter than needed the last frame is held.

### 1.4 `image`
`src`, `fit` (default `contain`), `crop`. Otherwise like video (no sound).

### 1.5 `text`
| key | default | meaning |
|---|---|---|
| `text` | `"Text"` | `\n` breaks lines |
| `font` | `"inter-regular"` | font **id** from the registry (§4) |
| `size` | 48 | px (× scale) |
| `color` | `"#f1efe9"` | |
| `letter_spacing` | 0 | fraction of size added after every character except the last on a line |
| `line_height` | 1.25 | × size |
| `align` | `"center"` | `center \| left \| right` — lines within the block |
| `shadow` | `{"enabled": true, "blur": 9, "opacity": 0.7}` | black copy of the glyph shapes, Gaussian **sigma** = blur px, drawn below |

Layout: block width = widest line; block height = lines × size × line_height; block centred on `(x, y)`.
Line `i` glyph top (font ascent line) = block_top + i·lh + (lh − size)/2, where lh = size × line_height.
With `letter_spacing == 0` a line is drawn as one run (kerning kept); otherwise character by character.
Missing glyphs fall back, per character, to the bundled `inter-regular` face — in **both** preview and render.
Preview and render must load **the same font file** (§4), so metrics match; target ±2 px.

### 1.6 `audio`
`src`, `src_in`, `volume_db`, `muted`, `envelope`.
`envelope`: `[[t, dB], ...]`, `t` in **timeline** time, linear in dB between points, held before the first and after the last, empty = 0 dB.
Linear gain = `10^((volume_db + envelope(t))/20) × f(t)`.

### 1.7 `color`
`color: "#000000"` fills the frame (x/y/scale ignored).

### 1.8 Motion: transform, keyframes, easing, modifiers, presets
Additive to v2: a project without these fields renders exactly as before. Maths lives in `anim.py` and `web/js/anim.js` (twins, same algorithms,
checked against `tests/anim_vectors.json`, tolerance 1e-9); both `render.py` and `preview.js` read every animated property through them.

**Transform fields** (visual layers, all static numbers, all animatable):
| key | default | meaning |
|---|---|---|
| `rotation` | 0 | degrees, clockwise, about the anchor |
| `scale_x`, `scale_y` | 1 | multipliers on top of `scale`; negative = mirror (there is no separate flip flag) |
| `anchor_x`, `anchor_y` | 0.5 | pivot as a fraction 0-1 of the layer's drawn box at scale 1 (0.5, 0.5 = centre = old behaviour). `x`, `y` are where the anchor sits |

Adaptation of ROADMAP B02: anchor is 0-1 (not -0.5..0.5) so that 0.5 = centre reads naturally.
Matrix, box-local point `q` (origin = box centre, unscaled px): `p = (x,y) + Rot(rotation) * diag(scale*scale_x*z, scale*scale_y*z) * (q - ((anchor_x-.5)*w, (anchor_y-.5)*h))`, `z` = the `zoom` effect, `(w,h)` = drawn box at scale 1 (fitted media size, or the text block at `size`).
Text and media are rasterised once at the largest scale the layer reaches (`raster_scale`: max |scale| over the keys times max(1, |scale_x|, |scale_y|)); animation then only shrinks/enlarges that bitmap, so text does not re-flow while scaling.
Sampling: rotated / skewed draws are bilinear with an anti-aliased edge; **axis-aligned draws (rotation exactly 0) snap to whole pixels like a browser canvas**, in both engines, so a slow drift moves in 1 px steps. Effects (blur, colour) apply after the transform, in output pixels.

**`keys`**: `{ "<prop>": [ {"t": 0, "v": 0.6, "ease": "out-back"}, {"t": 0.35, "v": 1} ] }`
- props: `x y scale scale_x scale_y rotation anchor_x anchor_y opacity` (visual), `volume_db` (video/audio), `fx.<effect id>.<param>` for numeric effect params (give the effect an `"id"`: `{"type":"blur","id":"b1","radius":8}` -> `fx.b1.radius`); params: grayscale `amount`, brightness/contrast/saturation `value`, blur `radius`, zoom `from`/`to`.
- `t` = **layer-local seconds** (`time - start`, so moving the layer moves its animation), non-decreasing. Before the first / after the last key the value is held. With keys present the static value of that property is ignored. Two keys with the same `t` make a step.
- `ease` on a key shapes the segment **leaving** that key (default `linear`). `"hold": true` keeps the key's value until the next key.
- opacity is clamped to 0-1 after keys and modifiers. Audio: `volume_db` keys/mods are sampled on a 200 Hz grid, linear in dB between samples.

**Ease specs** (every ease maps 0 -> 0 and 1 -> 1; unknown specs are validation errors): `linear`, `hold`; `in|out|in-out` + `-sine|quad|cubic|quart|quint|expo|circ|back|elastic|bounce` (Penner; `ease-` prefix optional); CSS keywords `ease`, `ease-in`, `ease-out`, `ease-in-out`; `[x1,y1,x2,y2]` or `"cubic-bezier(x1,y1,x2,y2)"` (CSS semantics: x clamped 0-1, y free, Newton x8 then bisection x64); `"steps(n[,end|start|none|both])"` or `{"type":"steps","n":6,"jump":"end"}`; `{"type":"back","overshoot":1.70158,"mode":"out"}`, `{"type":"elastic","amp":1,"period":0.3,"mode":"out"}`, `{"type":"bounce","mode":"out"}` (`mode` in|out|in-out, default out). Spring easing is not in this slice.

**`mods`**: `{ "<prop>": [ {"type":"wiggle","amp":8,"freq":5,"octaves":2,"seed":1,"t0":0,"t1":0.5,"decay":6} ] }` (same prop names as `keys`), added on top of the keyed (or static) value, in list order.
- `wiggle`: smooth seeded value noise, `amp` in the property's units (peak), `freq` Hz, `octaves` 1-6 (each 2x frequency, half amplitude). `noise`: sample-and-hold jitter, a new random value `freq` times a second. Optional window `[t0, t1)` (layer-local, default whole layer), `decay` = `exp(-decay*(t-t0))`.
- Deterministic in JS and Python: integer hash `lowbias32` on uint32; lattice value = `hash32(seed', i, octave) / 2^32 * 2 - 1`; smoothstep interpolation; `seed' = hash32(seed, fnv1a(prop), index of the mod in its list)` so x and y (and two mods) with the same `seed` still move independently. No `Math.random` anywhere.

**`use`** (presets; `presets.py`, `python3 presets.py list`): `[{"preset":"cam.punch","at":[2.4,5.1],"params":{"amount":1.12}}]`. `at` is **timeline seconds** (a number, a list, `"in"` = layer start, `"out"` = ends at the layer end); `dur` overrides the window. Presets are **baked into plain `keys`/`mods`** when a project is loaded (`schema.normalize`), by MCP `edit_project` before saving, or by `python3 presets.py bake <dir>`; the browser only ever sees the baked keys. Longest window is laid down first, so list order does not matter; a preset's keys replace the layer's own keys on that property inside its window, and its base is the value there.
- `cam.punch` (E01): scale x `amount` (1.12) over 0.18 s `out-expo`; `hold`+`settle` > 0 eases back. `cam.ken-burns` (E02): `from`/`to` `{scale,x,y}` (x, y are pixel offsets), `ease` (in-out-sine), whole layer by default. `cam.shake` (E03): wiggle on x, y (`amp` px), rotation (`rot` deg), `freq`, `octaves`, `seed`, `decay`, window `dur` (0.4 s); `overscan` (default on for video/image) scales the layer up inside the window so edges do not show.
- Engine-slice decision: presets are Python builders plus a JSON manifest (`describe()`), not yet the JSON template format of ROADMAP §5.5.

**`requires`** (top level): list of motion features the file uses (`keys`, `mods`, `transform`); `schema.normalize` and `edit_project` stamp it automatically, `render.py` refuses files that require unknown features. **The editor does not stamp it: `PUT /api/project` does** (`schema.stamp_requires`, single place), so a project saved from the browser always carries the right list.

**Scale composition**: the drawn scale on the x axis is `scale × scale_x × z`, on the y axis `scale × scale_y × z` (`z` = the `zoom` effect); `scale` is the uniform size, `scale_x`/`scale_y` are stretch multipliers (default 1, negative mirrors). A uniform resize can be written either as `scale` or as `scale_x = scale_y`; the viewer's side handles and the inspector's linked pair (chain toggle: linked = both multipliers edited together) only ever touch `scale_x`/`scale_y`, corner handles touch `scale`.

**Track view (browser, display only)**: the bottom panel has a Tracks (default) / Layers switch (`localStorage kurgu.layerView`); project.json is identical in both. Tracks: each run of one `group` in z-order is packed first-fit (front layer first) into rows, touching `end == start` does not overlap, upper rows = front layers, numbered from the bottom (V1, T1, C1, A1). Rows are derived, never stored: dragging a clip vertically only changes its `group` and its position in `layers` (z-order), one undo step; a clip that overlaps nothing always packs to the top row of its group. Rows keep their place during a drag and are packed again on drop. Logic: `web/js/trackpack.js`.

**Keyframe editing (browser)**: stopwatch on a property = key at the playhead with the current value (off = keys removed, value kept); with keys, editing a value at the playhead adds/updates a key (auto-key, times are layer-local and rounded to 1 ms). Effect params are keyed as `fx.<id>.<param>`; the editor gives an effect an `id` the first time one of its params is animated. Timeline lanes show one row per animated property; diamond shape = ease (linear ◆, eased round, hold ■); a key's ease is edited in the easing popover (presets + cubic-bezier editor). Viewer handles (corners = uniform scale about the opposite corner, sides = `scale_x`/`scale_y`, Alt = about the anchor, rotate handle, Alt-drag on the anchor crosshair moves the anchor and compensates `x`/`y`) write keys at the playhead when the property is animated.

---

## 2. Effects (`effects`, applied in order, each `{"type": ..., "enabled": true, ...}`)

Colour maths identical to CSS filter functions (W3C Filter Effects matrices), sRGB 0-1, clamped after each step.

| type | params | preview (canvas `ctx.filter`) | render |
|---|---|---|---|
| `grayscale` | `amount` 0-1 (1) | `grayscale(amount)` | CSS grayscale matrix |
| `brightness` | `value` −1..1 | `brightness(1+value)` | rgb × (1+value) |
| `contrast` | `value` −1..1 | `contrast(1+value)` | (rgb − .5)(1+value) + .5 |
| `saturation` | `value` −1..1 | `saturate(1+value)` | CSS saturate matrix |
| `blur` | `radius` px | `blur(radius px)` | Gaussian sigma = radius |
| `zoom` | `from` (1), `to` (1.06) | — | `z(t) = from + (to−from)(t−start)/(end−start)` (slow push-in) |
| `freeze` | — | element paused | the `src_in` frame for the whole layer |

`zoom`/`freeze` only apply to video/image. Colour effects also apply to text.

Global (last, whole frame): vignette `g`: pixel × `1 − g·clamp((r−0.4)/0.6,0,1)²`, r = distance to centre / centre-to-corner distance.
Grain: render uses ffmpeg `noise=alls=<grain>:allf=t`; preview draws an approximate animated noise overlay of similar strength.
`fade_out`: last n s, picture to black and sound to silence, linear.

---

## 3. `render.py`
```
python3 render.py <project_dir> [--draft] [--output path.mp4]
python3 render.py <project_dir> --frame 12.3 --output frame.png [--draft]
```
- Full: width×height, fps, libx264 crf 18 preset medium, yuv420p, aac 192k, +faststart → `project.output`.
- `--draft`: half resolution (all geometry, font sizes and blur sigmas × 0.5), crf 26, veryfast → `<output stem>_draft.mp4`.
- Export flags: `--codec h264|h265|prores|vp9|gif|wav|mp3`, `--format`, `--crf` | `--bitrate`, `--preset`, `--scale` | `--size WxH`, `--fps`, `--from/--to` (video and sound of that span only), `--no-audio`, `--audio-bitrate`. gif = palettegen/paletteuse two-pass, ≤ 15 fps by default; wav/mp3 skip picture composition. Output is written in `.kurgu/render_tmp/` and moved into place when finished; SIGTERM cleans up.
- `--frame t`: a single PNG, no sound, must be fast (single-frame decode per active layer).
- stdout protocol, one line each, flushed: `PROGRESS 0.42`, `DONE <abs path>`, `ERROR <message>` + non-zero exit.
- Composition in numpy (one ffmpeg decoder process per visual layer, rawvideo rgba pipe), bottom-to-top, then vignette, fade_out; encode through an ffmpeg rawvideo pipe; grain in the encoder filter. Layers are evaluated ahead on one thread per layer, composited in horizontal bands on a thread pool and written by a feeder thread (pixels identical to the serial path). Sound: per-layer PCM with gain applied in numpy, mixed in 2 s blocks (memory stays ~100 MB whatever the length), summed, then one ffmpeg pass for loudnorm (`I=<lufs>:TP=-1.5:LRA=16`), fade_out and trim.
- Intermediates in `<project>/.kurgu/render_tmp/`, removed on success. Missing source → `ERROR layer "<name>" (<id>): file not found: <path>`.

## 4. Fonts (`fonts.py`, shared by server and render)
Registry of faces, each `{"id", "family", "style", "weight", "italic", "source": "project"|"bundled"|"system", "file", "index"}`.
- **id** = slug of `family + " " + style`, lowercase ASCII, spaces → `-` (e.g. `inter-bold`, `gill-sans-light`, `helvetica-neue-regular`). On collision the priority is project > bundled > system.
- Sources: `<project>/fonts/*.ttf|otf|ttc` · repo `fonts/` (bundled) · system dirs (macOS `/System/Library/Fonts`, `/Library/Fonts`, `~/Library/Fonts`; Linux `/usr/share/fonts`, `~/.local/share/fonts`, `~/.fonts`; Windows `%WINDIR%\Fonts`, `%LOCALAPPDATA%\Microsoft\Windows\Fonts`). Scan with fontTools (name table: family = nameID 16 or 1, style = nameID 17 or 2), cache the system scan in `~/.cache/kurgu/fonts.json` keyed by dir mtimes.
- Faces inside `.ttc` collections are extracted with fontTools to `~/.cache/kurgu/faces/<id>.ttf` so the **browser loads exactly the same face file** the renderer uses.
- Render: `ImageFont.truetype(file, size, index=index)`. Unknown font id → fall back to `inter-regular` and print a warning line `WARN font <id> not found`.
- Bundled set (OFL, static TTF, must contain Turkish ş ğ ı İ ö ü ç): Inter (Regular, Medium, SemiBold, Bold, Light), a cinematic serif (Cormorant Garamond or Playfair Display, Regular + Bold + Italic), Bebas Neue, JetBrains Mono (Regular, Bold), Space Grotesk (Regular, Bold). Include each family's OFL.txt in `fonts/licenses/`.

## 5. `server.py` (stdlib `ThreadingHTTPServer` + fontTools via `fonts.py`)
```
python3 server.py <project_dir> [--port 8765] [--no-open]
```
Binds 127.0.0.1 only, tries following ports if busy, opens the browser unless `--no-open`, writes an empty template `project.json` (1920×1080, 30 fps, 10 s, no layers) if neither `project.json` nor legacy `proje.json` exists; legacy → §7. One log line per request (not for `/api/version` or `/api/state`). While running it keeps `.kurgu/server.json` = `{"port", "pid", "started"}` (removed on clean exit; a file whose pid is dead is stale), so other tools can find the editor of a directory.

**Request gate** (every route, including `GET /media`, `/api/frame`, `/font-file`): besides the Host allow-list, a request is refused with `403 {"error"}` when it carries an `Origin` header that is not exactly this server's own (`http://127.0.0.1:<port>` or `http://localhost:<port>`; another localhost port is foreign), or a `Sec-Fetch-Site` of `cross-site` or `same-site`. `same-origin`, `none` or no headers at all (curl, MCP, tests) pass. This stops other web pages from writing to the project, starting renders or Ask AI runs, or reading local files through `<img>`/`<video>`. The agent command for Ask AI comes only from env `KURGU_AGENT_CMD` or the user-level config (`~/.config/kurgu/config.json`, `%APPDATA%\kurgu\config.json` on Windows), never from the project's `.kurgu/config.json`.

| endpoint | |
|---|---|
| `GET /`, `/web/*` | static, `Cache-Control: no-store`, `.js` as `text/javascript` |
| `GET /api/project` | `{"project": {...}, "version": "<sha1 of file bytes>"}` |
| `PUT /api/project` | body `{"project", "summary": ["line", ...], "base_version"}`; if disk version ≠ base_version → **409** with current `{"project","version"}`. Else atomic write, backup `.kurgu/backups/project-<ts>.json` (keep 50), append summary lines to `changes.md` as `- HH:MM:SS line` under a `## YYYY-MM-DD` heading. Returns `{"version"}` |
| `GET /api/version` | `{"version"}` — client polls every 1 s |
| `PUT /api/state` | body `{"time", "selected": [ids], "playing", "language"}` → written to `.kurgu/state.json` with `updated` ISO time. **This is how Claude Code knows what "this text" / "here" means.** |
| `GET /media?path=` | file with HTTP Range (206). Allowed: under project dir, or referenced by `src`/`output` in project.json |
| `GET /api/media-info?path=` | `{"duration","width","height","has_audio","codec","proxy","proxy_ready","proxy_progress","proxy_eta"}`; proxies for videos wider than 1280: `.kurgu/proxies/<sha1(path+mtime)>.mp4`, 1280 wide, crf 23 veryfast, `-g 15`, aac, faststart, max 2 concurrent, temp name then rename. `proxy_progress` is 0-1 while the proxy is being made (from ffmpeg `-progress`), 1 when ready, `null` when no proxy applies or it failed; `proxy_eta` is seconds left (`null` until known). HEVC/VP9/AV1 sources use hardware decode + encode when a working encoder exists (fallback: software); sources over 1 GB start their proxy on import / first file listing instead of on first use |
| `GET /api/waveform?path=` | `{"rate": 100, "peaks": [0-1...]}`, cached in `.kurgu/waveforms/` |
| `GET /api/thumbs?path=&n=` | JSON list of `n` (≤ 40) evenly spaced small JPEG thumbnails as `/media?path=...` URLs under `.kurgu/thumbs/` (for filmstrips; cached) |
| `GET /api/files` | media files in the project dir (recursive, skip `.kurgu`, ≤ 500): `[{"path","kind": "video"\|"image"\|"audio"\|"font","size","mtime"}]` (`mtime` = whole seconds since epoch) |
| `GET /api/fonts` | registry list (§4), each plus `"url": "/font-file?id=<id>"` |
| `GET /api/presets`, `POST /api/presets/apply` | manifest of the motion presets (`presets.describe()`: id, name, name_tr, group in/out/emphasis/camera, default_at, applies_to, params) · body `{"layer_id","preset","at"?,"dur"?,"params"?}` bakes that preset into the layer's `keys`/`mods` (like `use`, §1.8) and saves (backup + change line); answers `{"project","version"}` like `GET /api/project`. 400 unknown preset / wrong layer type / locked layer, 404 unknown layer |
| `GET /font-file?id=` | the face file (extracted from ttc if needed), `font/ttf` or `font/otf` |
| `POST /api/import?name=<file name>` | raw request body = file bytes (streamed to disk, no size cap beyond disk). Sanitise the name, route by extension: video/image/audio → `<project>/media/`, ttf/otf/ttc → `<project>/fonts/` (refresh registry). Name clash → `name-2.ext`. woff/woff2 → 415 with message. Returns `{"path" (relative), "kind", "font_ids": [...] (fonts only)}` |
| `POST /api/render` | `{"draft": bool}` (legacy: project.output / `_draft`) or `{"preset": id, "options": {...}}` (exports.py: codec, format, crf/bitrate, encoder (software default | auto | hardware: VideoToolbox/NVENC/QSV/AMF for h264/h265, crf mapped to the encoder's scale; the `fast` preset uses auto), scale/fit_long/width, fps, range, audio, name, folder, exists, open_when_done) → `exports/<name>_<preset>.<ext>`; one at a time (409 if running, 400 on bad options) |
| `GET /api/render` | `{"status": "idle"\|"running"\|"done"\|"error"\|"cancelled", "progress", "output": "/media?path=...", "output_path", "name", "error", "elapsed", "eta", "output_size"}` |
| `POST /api/render/cancel` | kills the render process tree (process group / taskkill /T), removes temp + partial output → status `cancelled`; 409 if idle. Also done when the server stops |
| `POST /api/render/plan` | same body as render → `{"output", "exists", "size", "options"}` (nothing is started) |
| `GET /api/export-presets`, `GET /api/exports`, `POST /api/exports/reveal` | preset table · files in `exports/` `[{path,name,size,mtime,url}]` · show a file in the OS file manager |
| `GET /api/frame?t=` | render.py `--frame --draft` PNG, cached in `.kurgu/frames/` keyed by version + t |

Errors: JSON `{"error": "..."}` with a proper status code, English (the UI translates known cases).

## 6. Editor (`web/`) — i18n and import (additions to the v1 UI, which stays as built)
- **i18n:** every user-facing string comes from `web/i18n/<lang>.json` (flat dotted keys, `{name}` placeholders) through `t(key, params)`. Languages `en`, `tr`. Initial: `localStorage["kurgu.lang"]`, else `tr` if `navigator.language` starts with `tr`, else `en`. A compact `EN | TR` switch in the top bar re-renders the UI instantly. Numbers/time via `Intl.NumberFormat(lang)` (Turkish shows `12,40 sn`, English `12.40 s`); inputs accept both `,` and `.`. Change-summary lines are written in the current UI language.
- **Fonts:** font picker lists `/api/fonts` grouped by source (Project / Built-in / System) with a live preview of the family name; faces load on demand with `new FontFace("kf-" + id, "url(" + url + ")")`; canvas uses `"kf-<id>", "kf-inter-regular"`. Redraw when a face finishes loading.
- **Import:** drag files anywhere onto the window, or an *Import* button (file picker, multiple). Upload with progress in the top bar via `POST /api/import`. Media → a new layer at the playhead (video/audio with their natural duration, image 3 s), fonts → added to the registry and, if a text layer is selected, applied to it. Unsupported types → toast.
- **Notes panel:** a collapsible right-hand drawer beside the inspector (top-bar button next to *Note*, `Shift+N`, open/closed state and width in `localStorage["kurgu.np.*"]`). Lists every note by time: timecode, lazy draft-frame thumbnail (`/api/frame`, cached per visual state), inline-editable text (Enter saves, Esc cancels), you/agent badge, open/done checkbox, layer chips (the layers under that moment now), the agent's reply. Filters All / Open / Done + search; header "5 open · 2 done". Click a row: playhead jumps, the layer panel scrolls it into view; the row under the playhead (±0.25 s) is highlighted; `↑`/`↓` in the list move between notes. `Alt+→` / `Alt+←` anywhere jump to the next / previous note. Per-note actions: jump, edit, done, delete (one undo step), *Ask AI about this* (opens the Ask-AI box scoped to that moment with the note text pre-filled). Ruler and scrub-bar markers: filled = open, hollow = done; hover shows the text, click jumps. Change summaries cover added / edited / done / reopened / removed notes.
- **State for Claude:** on playhead stop/seek, selection change or language change, `PUT /api/state` (debounced 300 ms; during playback at most every 2 s).
- **Sequence settings & viewer zoom:** a dialog (top-bar button, Project section, empty-viewer hint) sets `width`/`height`/`fps` from presets (16:9 1080p/4K/720p, 9:16, 1:1, 4:5, 21:9, custom, even 16-7680) as one undoable change; on a size change layers are either adapted ("keep composition": x,y proportional, text `size` / native and contain image `scale` × min(newW/oldW, newH/oldH)) or left at their pixel positions. The viewer has Fit/10-400 % zoom (⌘+ ⌘− ⌘0 ⌘1, ⌘/pinch wheel around the cursor, Space/middle-drag/wheel pan) and optional safe-area and 9:16 social-UI guides (viewer only, never rendered).

## 7. Migration from v1 (`schema.py`)
`schema.load_project(dir)` returns a normalised v2 dict. If only `proje.json` exists, the server migrates it once, writes `project.json` and leaves `proje.json` untouched. Mapping:
`surum→version, ad→name, genislik→width, yukseklik→height, sure→duration, arkaplan→background, cikti→output, genel→global{gren→grain, vinyet→vignette, ses_hedef_lufs→loudness_lufs, son_karartma→fade_out}, katmanlar→layers, notlar→notes{metin→text, zaman→created}`;
layer: `ad→name, tur→type (video, gorsel→image, yazi→text, renk→color, ses→audio), grup→group, gizli→hidden, kilitli→locked, baslangic→start, bitis→end, giris{tur,sure}→fade_in (sure if tur=="fade" else 0), cikis→fade_out, olcek→scale, opaklik→opacity, efektler→effects, kaynak→src, kaynak_bas→src_in, sigdir→fit (kapla→cover, sigdir→contain, dogal→native), kirp→crop{ust→top, alt→bottom, sol→left, sag→right}, ses_duzeyi→volume_db, sessiz→muted, zarf→envelope, metin→text, boyut→size, renk→color, harf_araligi→letter_spacing, satir_yuksekligi→line_height, hiza→align (orta→center, sol→left, sag→right), golge→shadow{acik→enabled, bulaniklik→blur, opaklik→opacity}`;
effects: `tur→type, acik→enabled, siyahbeyaz→grayscale{miktar→amount}, parlaklik→brightness{deger→value}, kontrast→contrast, doygunluk→saturation, bulaniklik→blur{yaricap→radius}, yakinlas→zoom{bas→from, son→to}, dondur→freeze`;
fonts: `gill→gill-sans-regular, gill-light→gill-sans-light, gill-semibold→gill-sans-semibold, gill-bold→gill-sans-bold, futura→futura-medium, futura-bold→futura-bold, helvetica→helvetica-neue-regular, helvetica-light→helvetica-neue-light, helvetica-bold→helvetica-neue-bold, avenir→avenir-next-regular, avenir-demi→avenir-next-demi-bold, avenir-heavy→avenir-next-heavy`.
`degisiklikler.md` is left as is; new lines go to `changes.md`.

## 8. Claude Code integration
Shipped as a Claude Code **plugin** from this repo (users install it through a plugin marketplace pointing at the repo). It contains:
- a **skill** teaching Claude the project format (§1-§2 condensed), the workflow (write `project.json` → run `kurgu` → the user edits → read `changes.md` and `.kurgu/state.json` before every follow-up edit → `render.py`), rules (never overwrite the user's manual edits blindly: re-read project.json first; keep ids stable; put new assets in `media/`), and how to resolve "this"/"here" from `state.json`;
- a **command** `/kurgu [dir]` that starts the server in the background and reports the URL;
- a dependency check (python, numpy, Pillow, fontTools, ffmpeg) with install hints per OS.

## 9. MCP server (`mcp_server.py`, `.mcp.json`, `AGENTS.md`)
The same editing powers for any MCP-capable agent: stdio, newline-delimited JSON-RPC, zero dependencies, reusing `schema.py`, `fonts.py`, `server.py` (`App`) and `render.py`. Tools (all take optional `project_dir`): `open_editor`, `get_state`, `get_project`, `edit_project`, `render_frame`, `render`, `get_changes`, `get_brief`, `update_brief`, `get_notes`, `update_note`, `add_note`, `list_fonts`, `list_media`, `import_file`, `transcribe`, `make_captions`. Notes: `get_notes(status=open|done|all)` returns each note with the layers on screen at its time; `update_note(id, done?, reply?, text?, t?)` resolves/edits (agents resolve, they do not delete); `add_note(t, text)` writes an `author:"agent"` note. They save like `edit_project` (running editor first) with `[agent]` lines in `changes.md`. `edit_project` applies `update` → `remove` → `add` → `project` atomically, validates with `schema.validate` (rejects only problems the batch introduces), and saves through the running editor's `PUT /api/project` (found via `.kurgu/server.json`), else through `server.App.save_project`; its summary line is written to `changes.md` prefixed `[agent]`. Speaks MCP 2026-07-28 (no handshake, `server/discover`) and the `initialize` handshake of 2025-11-25 and earlier. `.mcp.json` registers it in the Claude Code plugin; `AGENTS.md` is the entry point for other agents.

## 10. Speech-to-text and captions (`transcribe.py`, `captions.py`; optional)
Local, no cloud, optional install. Backend: `mlx-whisper` on Apple Silicon (models `mlx-community/whisper-{tiny,base,small,medium}-mlx`, `whisper-large-v3-mlx`, `whisper-large-v3-turbo`), else `faster-whisper` (any OS; CUDA if present), else `availability()` = `{"available": false, "message", "hints"}` with a one-line pip command per OS. Model default `small` (`tiny|base|small|medium|large-v3|large-v3-turbo`, `large`/`turbo` aliases); language auto-detected or an ISO code. Models download once on first use (progress is reported). `$KURGU_STT_BACKEND=mlx|faster` forces a backend.
- Audio is extracted with ffmpeg to 16 kHz mono wav; only the requested range is transcribed (a layer: `src_in .. src_in + (end - start)`).
- Transcript: `{language, duration, model, backend, segments:[{start,end,text,words:[{start,end,word,prob}]}]}`, times in **source-file seconds**; cached in `.kurgu/transcripts/<sha1(path+mtime+size+model+language+range)>.json`. `to_timeline` maps onto a layer: `t_tl = layer.start + (t - src_in)`, words outside the layer are dropped, times clamped, result flagged `"timeline": true`.
- HTTP (one job at a time, 409 otherwise; 501 when no backend): `GET /api/transcribe` → availability · `POST /api/transcribe {path | layer_id, model?, language?}` → 202 job `{id, status, stage, progress, message, downloading, note}` · `GET /api/transcribe/<id>` → status; `stage` is `extracting|downloading|transcribing|done`; `downloading:true` means the one-time model download; `result` (a transcript, timeline time when `layer_id` was given) when `status` is `done` · `POST /api/transcribe/<id>/cancel`. `path` must be inside the project.
- MCP: `transcribe {layer_id | path, model?, language?, offset?, words?}` (timeline words for a layer) and `make_captions {layer_id | path, max_words_per_line?, max_chars?, font?, size?, y?, uppercase?, color?, replace?}`. Captions: words are chunked (max words / max chars / sentence end / pause > 0.6 s), each chunk becomes a `text` layer `cap_<n>` in group `Captions`, shown from its first word to the next chunk (gaps under 0.35 s closed, else 0.12 s hold), added front-most through `edit_project` (backup, `[agent]` line in `changes.md`, live reload, undoable). Defaults by aspect: vertical (height > width) 3 words, 20 chars, size 7.8 % of width, y 66 % of height (inside the 62-70 % social safe zone); otherwise 7 words, 42 chars, size 3.3 % of width, y 86 %. Re-running replaces existing `cap_*` layers (`replace:false` appends). Word-by-word highlight styling is a later step (catalogue D06).
