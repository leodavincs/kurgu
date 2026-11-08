"""Where is the text, and what are the shapes, in a frame? A generic fallback for pixels Kurgu did not draw itself.

Text burned into a clip (footage, 3D/WebGL text, another tool's render) and flat graphics inside it have no layer `bounds` and no element map,
so an agent can only guess where they are. `read_frame` returns approximate boxes in image px; the agent should still LOOK (`kurgu frame`).

    read_frame(path) -> {"text": [{text, l, t, r, b, conf}],
                         "objects": [{kind, box:[l,t,r,b], conf, approx:True, source, color, shape?}],
                         "backend": {"text": ..., "objects": [...]}, "reason": ""}
    read_text(path)  -> just the text list (thin wrapper)

Backends, picked automatically, no pip installs:
  text    macOS Vision (VNRecognizeTextRequest, accurate, tr+en) through `osascript -l JavaScript`; Linux `tesseract` if installed;
          Windows: unavailable (reason says so).
  objects macOS Vision (objectness saliency + rectangle detection) and, everywhere, a pure numpy/PIL colour-region pass for flat graphics
          (circles, cards, arrows on plain or photo-like backgrounds; source "pixels").
It never raises: a missing backend gives empty lists and a `reason`. Results are cached by image hash (memory, and `cache_dir` on disk).

Wiring into the note payload (done by the integrator, not here): in `agent_notes.note_view`, where a mark has no sidecar `elements_under`, call
    import ocr_boxes
    for mv in out.get("marks", []): mv.setdefault("elements_under", ocr_boxes.things_under_mark(d, n, mv))
Every entry is `approx: True`."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

VERSION = 2
MAX_OBJECTS = 25
MIN_AREA = 0.004          # fraction of the frame: smaller boxes are noise
IOU_DEDUPE = 0.6
_MEM = {}

# ----------------------------------------------------------------------------- macOS Vision through JXA (zero install)
_JXA = r"""
ObjC.import('Vision'); ObjC.import('Foundation');
function bx(o){var b=o.boundingBox;return [b.origin.x,b.origin.y,b.size.width,b.size.height];}
function run(argv){
  var url=$.NSURL.fileURLWithPath(argv[0]); var langs=argv[1].split(','); var wantObj=argv[2]==='1';
  var h=$.VNImageRequestHandler.alloc.initWithURLOptions(url,$({}));
  var tr=$.VNRecognizeTextRequest.alloc.init; tr.recognitionLevel=0; tr.usesLanguageCorrection=true; tr.recognitionLanguages=$(langs);
  var reqs=[tr], sal=null, rc=null;
  if(wantObj){
    sal=$.VNGenerateObjectnessBasedSaliencyImageRequest.alloc.init;
    rc=$.VNDetectRectanglesRequest.alloc.init; rc.maximumObservations=20; rc.minimumSize=0.02; rc.minimumAspectRatio=0.1; rc.minimumConfidence=0.3; rc.quadratureTolerance=30;
    reqs.push(sal); reqs.push(rc);
  }
  var err=Ref(); h.performRequestsError($(reqs),err);
  var out={text:[],sal:[],rect:[]};
  var res=tr.results;
  for(var i=0;res&&i<res.count;i++){var o=res.objectAtIndex(i);var c=o.topCandidates(1).objectAtIndex(0);
    out.text.push({text:ObjC.unwrap(c.string),conf:c.confidence,b:bx(o)});}
  if(sal){var r=sal.results; if(r&&r.count>0){var so=r.objectAtIndex(0).salientObjects;for(var i=0;so&&i<so.count;i++){out.sal.push(bx(so.objectAtIndex(i)));}}}
  if(rc){var rr=rc.results;for(var i=0;rr&&i<rr.count;i++){var o=rr.objectAtIndex(i);out.rect.push(bx(o).concat([o.confidence]));}}
  return JSON.stringify(out);
}
"""
_jxa_path = None


def _vision_available():
    return sys.platform == "darwin" and shutil.which("osascript") is not None


def _jxa_file():
    global _jxa_path
    if not _jxa_path or not os.path.exists(_jxa_path):
        fd, p = tempfile.mkstemp(suffix=".js", prefix="kurgu-vision-")
        with os.fdopen(fd, "w") as f:
            f.write(_JXA)
        _jxa_path = p
    return _jxa_path


def _vision_run(path, langs, objects):
    """Raw Vision result (normalised, origin bottom-left) or (None, reason)."""
    lc = {"tr": "tr-TR", "en": "en-US", "de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it-IT"}
    tags = ",".join(lc.get(l, l) for l in langs) or "en-US"
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", _jxa_file(), path, tags, "1" if objects else "0"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"Vision (osascript) failed to run: {e}"
    if r.returncode != 0:
        return None, "Vision (osascript) failed: " + ((r.stderr or "").strip().splitlines() or ["unknown error"])[-1]
    try:
        return json.loads(r.stdout), ""
    except ValueError:
        return None, "Vision returned unreadable output"


def _norm_to_px(b, W, H):
    x, y, w, h = b[:4]
    return [x * W, (1 - y - h) * H, (x + w) * W, (1 - y) * H]


# ----------------------------------------------------------------------------- Linux: tesseract
def _tesseract_run(path, langs):
    exe = shutil.which("tesseract")
    if not exe:
        return None, "no OCR backend: install tesseract (e.g. `apt install tesseract-ocr tesseract-ocr-tur`)"
    m = {"tr": "tur", "en": "eng", "de": "deu", "fr": "fra", "es": "spa", "it": "ita"}
    have = subprocess.run([exe, "--list-langs"], capture_output=True, text=True).stdout.split()
    use = [m.get(l, l) for l in langs if m.get(l, l) in have] or (["eng"] if "eng" in have else [])
    cmd = [exe, path, "stdout", "tsv"] + (["-l", "+".join(use)] if use else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"tesseract failed: {e}"
    lines = {}
    for row in r.stdout.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12 or not c[11].strip():
            continue
        try:
            key = tuple(int(x) for x in c[1:5])
            l, t, w, h, conf = int(c[6]), int(c[7]), int(c[8]), int(c[9]), float(c[10])
        except ValueError:
            continue
        if conf < 0:
            continue
        e = lines.setdefault(key, {"w": [], "l": l, "t": t, "r": l + w, "b": t + h, "c": []})
        e["w"].append(c[11].strip()); e["c"].append(conf)
        e["l"], e["t"], e["r"], e["b"] = min(e["l"], l), min(e["t"], t), max(e["r"], l + w), max(e["b"], t + h)
    out = [{"text": " ".join(e["w"]), "l": e["l"], "t": e["t"], "r": e["r"], "b": e["b"], "conf": round(sum(e["c"]) / len(e["c"]) / 100, 3)}
           for e in lines.values()]
    return out, ""


# ----------------------------------------------------------------------------- pure numpy/PIL: flat colour regions
def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in rgb)


def dominant_color(im, box):
    """Dominant colour (hex) inside box l,t,r,b of PIL image `im`: mode of a coarse histogram of a 32x32 sample, then the mean of that bin."""
    import numpy as np
    l, t, r, b = [int(round(v)) for v in box]
    l, t = max(0, l), max(0, t)
    r, b = min(im.width, max(r, l + 1)), min(im.height, max(b, t + 1))
    a = np.asarray(im.convert("RGB").crop((l, t, r, b)).resize((32, 32))).reshape(-1, 3).astype(int)
    q = a // 32
    key = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
    top = np.bincount(key).argmax()
    return _hex(a[key == top].mean(axis=0))


def _shape_hint(w, h, fill):
    """Rough shape from the box aspect and how much of the box the region fills (circle ~0.785, rect ~1)."""
    asp = w / h if h else 1
    if fill >= 0.88:
        return "square" if 0.85 <= asp <= 1.18 else "rect"
    if 0.7 <= fill <= 0.86 and 0.8 <= asp <= 1.25:
        return "circle"
    if 0.7 <= fill <= 0.86:
        return "ellipse"
    return "other"


def pixel_regions(im, min_area=MIN_AREA):
    """Flat colour regions that differ from the dominant flat background: [{box:[l,t,r,b], area, fill}] in image px. Works on a block grid,
    so photo-like noise (high variance inside a block) is ignored and only flat graphics remain."""
    import numpy as np
    from collections import deque
    a = np.asarray(im.convert("RGB")).astype(np.float32)
    H, W = a.shape[:2]
    f = max(2, W // 240)
    h2, w2 = H // f, W // f
    if h2 < 4 or w2 < 4:
        return []
    blk = a[:h2 * f, :w2 * f].reshape(h2, f, w2, f, 3)
    mean = blk.mean(axis=(1, 3))
    std = blk.std(axis=(1, 3)).max(axis=2)
    flat = std < 10
    q = (mean // 28).astype(np.int32)
    key = q[..., 0] * 10000 + q[..., 1] * 100 + q[..., 2]
    if flat.sum() < 10:
        return []
    border = np.zeros_like(flat)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    ks, cnt = np.unique(key[flat & border], return_counts=True)     # the background is the flat colour that touches the frame edge
    bg = ks[cnt.argmax()] if len(ks) and cnt.max() > 0.3 * (flat & border).sum() else None
    out = []
    seen = np.zeros_like(flat)
    ys, xs = np.nonzero(flat & ((key != bg) if bg is not None else True))
    for sy, sx in zip(ys, xs):
        if seen[sy, sx]:
            continue
        k0 = key[sy, sx]
        dq = deque([(sy, sx)]); seen[sy, sx] = True
        n, x0, y0, x1, y1 = 0, sx, sy, sx, sy
        while dq:
            y, x = dq.popleft()
            n += 1
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h2 and 0 <= nx < w2 and not seen[ny, nx] and flat[ny, nx] and key[ny, nx] == k0:
                    seen[ny, nx] = True
                    dq.append((ny, nx))
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if n * f * f < min_area * W * H or n * f * f > 0.8 * W * H:
            continue
        pad = f      # the block grid drops edge blocks (they straddle the border); give one block back on each side
        box = [max(0, x0 * f - pad // 2), max(0, y0 * f - pad // 2), min(W, (x1 + 1) * f + pad // 2), min(H, (y1 + 1) * f + pad // 2)]
        out.append({"box": [int(v) for v in box], "area": int(n * f * f), "fill": n / float(bw * bh)})
    return out


# ----------------------------------------------------------------------------- merging
def _iou(a, b):
    ix, iy = max(0, min(a[2], b[2]) - max(a[0], b[0])), max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def _covered(box, others):
    a = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    return sum(max(0, min(box[2], o[2]) - max(box[0], o[0])) * max(0, min(box[3], o[3]) - max(box[1], o[1])) for o in others) / a


def dedupe(objs, cap=MAX_OBJECTS):
    keep = []
    for o in sorted(objs, key=lambda o: -o["conf"]):
        if all(_iou(o["box"], k["box"]) <= IOU_DEDUPE for k in keep):
            keep.append(o)
    return keep[:cap]


def _edge_contrast(a, box):
    """For a candidate rectangle: 0 unless one flat colour fills at least half of it; else the max-channel difference between that colour and the
    mean of a ring just outside the box (numpy array `a`, HxWx3). Textured or background-coloured candidates score 0."""
    l, t, r, b = [int(v) for v in box]
    H, W = a.shape[:2]
    m = max(3, int(0.04 * max(r - l, b - t)))
    L, T, R, B = max(0, l - m), max(0, t - m), min(W, r + m), min(H, b + m)
    crop = a[t:b, l:r]
    if crop.size == 0:
        return 0.0
    step = max(1, int(max(crop.shape[:2]) // 64))
    inner = crop[::step, ::step].reshape(-1, 3).astype("int32")
    q = inner // 32
    key = q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2]
    top = int(__import__("numpy").bincount(key).argmax())
    if (key == top).mean() < 0.5:
        return 0.0
    col = inner[key == top].mean(axis=0)
    ring = a[T:B, L:R][::step, ::step].reshape(-1, 3).astype("float32")
    return float(abs(col - ring.mean(axis=0)).max()) if len(ring) else 255.0


def _objects(im, raw, text_boxes):
    import numpy as np
    arr = np.asarray(im)
    edge = np.concatenate([arr[::8, 0], arr[::8, -1], arr[0, ::8], arr[-1, ::8]]).astype(int)
    ek = (edge // 32) @ np.array([64, 8, 1])
    etop = np.bincount(ek).argmax()
    bg = edge[ek == etop].mean(axis=0) if (ek == etop).mean() > 0.5 else None      # flat frame edge = plain background
    W, H = im.size
    cand = []
    if raw:
        for b in raw.get("sal", []):
            cand.append(("object", _norm_to_px(b, W, H), 0.7, "vision"))
        for b in raw.get("rect", []):
            bw, bh = b[2], b[3]
            if bw < 0.02 or bh < 0.02 or bw * bh < MIN_AREA:
                continue
            px = _norm_to_px(b, W, H)
            hx = dominant_color(im, px)
            if bg is not None and abs(np.array([int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16)]) - bg).max() < 25:
                continue       # a "card" the colour of the plain background is text or a gap, not a shape
            if _edge_contrast(arr, px) >= 25:      # Vision proposes rectangles in texture too; keep those whose inside differs from their surround
                cand.append(("rect", px, min(0.9, float(b[4])), "vision"))
    for r in pixel_regions(im):
        if _covered(r["box"], text_boxes) > 0.6:
            continue
        cand.append(("shape", r["box"], 0.95, "pixels", _shape_hint(r["box"][2] - r["box"][0], r["box"][3] - r["box"][1], r["fill"])))
    objs = []
    for c in cand:
        box = [round(max(0, min(v, lim)), 1) for v, lim in zip(c[1], (W, H, W, H))]
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            continue
        o = {"kind": c[0], "box": box, "conf": round(c[2], 3), "approx": True, "source": c[3], "color": dominant_color(im, box)}
        if len(c) > 4:
            o["shape"] = c[4]
        elif c[0] == "rect":
            o["shape"] = "rect"
        objs.append(o)
    return dedupe(objs)


# ----------------------------------------------------------------------------- public API
def _user_cache():
    """OCR results are cached under the USER's cache folder, keyed by the image hash: a project folder that ships its own `.kurgu/ocr/*.json` cannot feed fake text boxes."""
    import kurgu_paths
    return os.path.join(kurgu_paths.cache_dir(), "ocr")


def _key(path, langs, objects):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    h.update(f"|v{VERSION}|{','.join(langs)}|{int(objects)}".encode())
    return h.hexdigest()


def read_frame(image_path, langs=("tr", "en"), objects=True, cache_dir=None):
    """Text lines and object boxes of an image. Never raises; see the module docstring for the result shape."""
    empty = {"text": [], "objects": [], "backend": {"text": None, "objects": []}, "reason": ""}
    try:
        langs = tuple(langs)
        key = _key(image_path, langs, objects)
        if key in _MEM:
            return json.loads(json.dumps(_MEM[key]))
        disk = os.path.join(cache_dir, key + ".json") if cache_dir else None
        if disk and os.path.exists(disk):
            with open(disk, encoding="utf-8") as f:
                res = json.load(f)
            _MEM[key] = res
            return res
        from PIL import Image
        with Image.open(image_path) as im0:
            im = im0.convert("RGB")
        W, H = im.size
        res = json.loads(json.dumps(empty))
        reasons, raw = [], None
        path = image_path
        tmp = None
        if _vision_available():
            if not image_path.lower().endswith((".png", ".jpg", ".jpeg")):
                fd, tmp = tempfile.mkstemp(suffix=".png"); os.close(fd); im.save(tmp); path = tmp
            try:
                raw, why = _vision_run(os.path.abspath(path), langs, objects)
            finally:
                tmp and os.path.exists(tmp) and os.remove(tmp)
            if raw is not None:
                res["backend"]["text"] = "vision"
                if objects:
                    res["backend"]["objects"].append("vision")
                res["text"] = [{"text": e["text"], "l": round(b[0], 1), "t": round(b[1], 1), "r": round(b[2], 1), "b": round(b[3], 1), "conf": round(float(e["conf"]), 3)}
                               for e in raw["text"] for b in [_norm_to_px(e["b"], W, H)]]
            else:
                reasons.append(why)
        elif sys.platform.startswith("win"):
            reasons.append("text OCR unavailable on Windows (no zero-install backend); object/shape detection still works")
        if res["backend"]["text"] is None and not sys.platform.startswith("win"):
            if shutil.which("tesseract"):
                t, why = _tesseract_run(image_path, langs)
                if t is not None:
                    res["text"], res["backend"]["text"] = t, "tesseract"
                else:
                    reasons.append(why)
            elif not _vision_available():
                reasons.append("no OCR backend: install tesseract (e.g. `apt install tesseract-ocr tesseract-ocr-tur`)")
        if objects:
            try:
                tb = [[e["l"], e["t"], e["r"], e["b"]] for e in res["text"]]
                res["objects"] = _objects(im, raw, tb)
                res["backend"]["objects"].append("pixels")
            except Exception as e:      # never fail the caller
                reasons.append(f"pixel detection failed: {e}")
        res["size"] = [W, H]
        res["reason"] = "; ".join(r for r in reasons if r)
        if res["text"] or res["objects"] or not res["reason"]:
            _MEM[key] = res
            if disk:
                os.makedirs(cache_dir, exist_ok=True)
                with open(disk, "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False)
        return json.loads(json.dumps(res))
    except Exception as e:
        empty["reason"] = f"detection failed: {e}"
        return empty


def read_text(image_path, langs=("tr", "en"), cache_dir=None):
    """[{text, l, t, r, b, conf}] in image px, or []. Use read_frame to also get the reason."""
    return read_frame(image_path, langs, objects=False, cache_dir=cache_dir)["text"]


def fold(s):
    """Case- and diacritic-insensitive form for comparing OCR text (Turkish dotless i included)."""
    s = s.replace("ı", "i").replace("İ", "i").replace("I", "i")
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode())


# ----------------------------------------------------------------------------- note marks
def _mark_box(m, W, H):
    def g(nk, pk, size):
        v = m.get(nk)
        return float(v) * size if isinstance(v, (int, float)) and not isinstance(v, bool) else float(m.get(pk, 0) or 0)
    if m.get("type") == "arrow":
        x, y = g("nx2", "x2", W), g("ny2", "y2", H)
        r = 0.03 * max(W, H)
        return [x - r, y - r, x + r, y + r]
    x, y, w, h = g("nx", "x", W), g("ny", "y", H), g("nw", "w", W), g("nh", "h", H)
    return [x, y, x + w, y + h]


def things_under_mark(project_dir, note, mark, frame_png=None):
    """Text lines and objects whose box meets the mark, smallest first: [{text? | kind, box:[l,t,r,b], approx:True, source, color?}], project px.
    Renders a CLEAN frame at the note time (the note JPEG has the marks burned in, it is only the fallback)."""
    try:
        with open(os.path.join(project_dir, "project.json"), encoding="utf-8") as f:
            proj = json.load(f)
        pw, ph = float(proj.get("width") or 1920), float(proj.get("height") or 1080)
        src, own = None, False
        try:
            if frame_png is None:
                import mcp_server
                frame_png = mcp_server.render_still(project_dir, float(note.get("t", 0)), draft=True)
            fd, src = tempfile.mkstemp(suffix=".png"); os.close(fd)
            with open(src, "wb") as f:
                f.write(frame_png)
            own = True
        except Exception:
            src = os.path.join(project_dir, ".kurgu", "notes", str(note.get("id")) + ".jpg")
            if not os.path.exists(src):
                return []
        try:
            res = read_frame(src, cache_dir=_user_cache())
        finally:
            if own and os.path.exists(src):
                os.remove(src)
        iw, ih = res.get("size") or [pw, ph]
        kx, ky = pw / iw, ph / ih
        mb = _mark_box(mark, pw, ph)
        items = []
        for e in res["text"]:
            items.append({"text": e["text"], "box": [e["l"] * kx, e["t"] * ky, e["r"] * kx, e["b"] * ky], "source": "ocr"})
        for o in res["objects"]:
            b = o["box"]
            items.append({"kind": o["kind"], "box": [b[0] * kx, b[1] * ky, b[2] * kx, b[3] * ky], "source": o["source"], "color": o["color"]})
        hit = []
        for it in items:
            b = it["box"]
            if min(b[2], mb[2]) > max(b[0], mb[0]) and min(b[3], mb[3]) > max(b[1], mb[1]):
                it["box"] = [round(v, 1) for v in b]
                it["approx"] = True
                hit.append(it)
        return sorted(hit, key=lambda it: (it["box"][2] - it["box"][0]) * (it["box"][3] - it["box"][1]))
    except Exception:
        return []


def text_under_mark(project_dir, note, mark):
    """Only the text lines of things_under_mark."""
    return [t for t in things_under_mark(project_dir, note, mark) if "text" in t]


# ----------------------------------------------------------------------------- CLI body (called from kurgu_cli)
def _layer_frame(d, layer_id, t, out):
    """Extract the layer's own media frame at timeline time t to `out`. Returns (project-px bounds of the layer or None) or raises ValueError."""
    with open(os.path.join(d, "project.json"), encoding="utf-8") as f:
        proj = json.load(f)
    l = next((x for x in proj.get("layers", []) if x.get("id") == layer_id), None)
    if not l or l.get("type") not in ("video", "image"):
        raise ValueError(f"no video or image layer {layer_id!r}")
    src = os.path.realpath(os.path.join(d, os.path.expanduser(str(l.get("src", "")))))
    if not os.path.exists(src):
        raise ValueError(f"layer media missing: {l.get('src')}")
    if l["type"] == "video":
        import media_tools
        ts = max(0.0, float(l.get("src_in", 0) or 0) + (t - float(l.get("start", 0) or 0)) * float(l.get("speed", 1) or 1))
        r = subprocess.run([media_tools.ffmpeg(), "-v", "error", "-y", "-ss", f"{ts:.3f}", "-i", src, "-frames:v", "1", out],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        if r.returncode != 0 or not os.path.exists(out):
            raise ValueError("ffmpeg could not extract a frame: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1])
    else:
        from PIL import Image
        Image.open(src).convert("RGB").save(out)
    try:
        import agent_notes
        return agent_notes.bounds_of(proj, {"t": t}, l, d)
    except Exception:
        return None


def detect_project(d, t, layer=None, text_only=False):
    """Detect text + objects for project dir `d` at time `t` (composed frame, or one layer's own media frame). Boxes in PROJECT px.
    Raises ValueError with a readable message."""
    import kurgu_paths
    d = kurgu_paths.require_project(d)
    with open(os.path.join(d, "project.json"), encoding="utf-8") as f:
        pw = float(json.load(f).get("width") or 1920)
    fd, tmp = tempfile.mkstemp(suffix=".png"); os.close(fd)
    space = "project px"
    try:
        lb = None
        if layer:
            lb = _layer_frame(d, layer, t, tmp)
        else:
            import mcp_server
            try:
                png = mcp_server.render_still(d, t, draft=True)
            except mcp_server.ToolError as e:
                raise ValueError(str(e))
            with open(tmp, "wb") as f:
                f.write(png)
        res = read_frame(tmp, objects=not text_only, cache_dir=_user_cache())
    finally:
        os.path.exists(tmp) and os.remove(tmp)
    iw, ih = res.get("size") or [pw, pw * 9 / 16]
    if lb:
        sx, sy, ox, oy = lb["w"] / iw, lb["h"] / ih, lb["left"], lb["top"]
        space = "project px, mapped through the layer's estimated bounds"
    else:
        sx = sy = pw / iw if iw else 1
        ox = oy = 0

    def mp(b):
        return [round(b[0] * sx + ox, 1), round(b[1] * sy + oy, 1), round(b[2] * sx + ox, 1), round(b[3] * sy + oy, 1)]
    return {"t": t, "space": space, "backend": res["backend"], "reason": res["reason"],
            "text": [{"text": e["text"], "box": mp([e["l"], e["t"], e["r"], e["b"]]), "conf": e["conf"], "approx": True, "source": "ocr"} for e in res["text"]],
            "objects": [dict(o, box=mp(o["box"])) for o in res["objects"]]}


def detect_parser():
    import argparse
    ap = argparse.ArgumentParser(prog="kurgu detect", description="Find text (OCR) and shapes/objects in a frame, boxes in PROJECT px. Approximate: "
                                 "still look at the frame with `kurgu frame`.")
    ap.add_argument("project_dir", help="project folder")
    ap.add_argument("seconds", type=float, help="timeline time in seconds")
    ap.add_argument("--layer", default=None, help="optional layer id: analyse that video/image layer's own media frame instead of the composed frame")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--text-only", action="store_true", help="skip shapes/objects (default false)")
    return ap


def detect_main(argv):
    """kurgu detect <project_dir> <seconds> [--layer ID] [--json] [--text-only]  (alias: kurgu ocr)"""
    a = detect_parser().parse_args(argv)
    d = os.path.realpath(os.path.expanduser(a.project_dir))
    if not os.path.exists(os.path.join(d, "project.json")):
        print(f"error: No project.json in {d}", file=sys.stderr)
        return 1
    if a.seconds < 0:
        print("error: seconds must be >= 0", file=sys.stderr)
        return 1
    try:
        out = detect_project(d, a.seconds, a.layer, a.text_only)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(format_detect(out))
    return 0


def format_detect(out):
    L = [f"Boxes are [left, top, right, bottom] in {out['space']} at t={out['t']:g}s. Approximate: confirm with `kurgu frame`."]
    if out["reason"]:
        L.append("note: " + out["reason"])
    L.append(f"text ({len(out['text'])}):")
    L += [f"  {e['box']}  \"{e['text']}\"  conf {e['conf']:.2f}" for e in out["text"]] or ["  none"]
    L.append(f"objects ({len(out['objects'])}):")
    L += [f"  {o['box']}  {o['kind']}{'/' + o['shape'] if o.get('shape') else ''}  {o['color']}  conf {o['conf']:.2f}  [{o['source']}]" for o in out["objects"]] or ["  none"]
    return "\n".join(L)
