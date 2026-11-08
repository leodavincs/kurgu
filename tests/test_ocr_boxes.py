"""ocr_boxes: text (OCR) + shape/object boxes. Backend-specific tests skip with a reason; the pure-PIL tests always run."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ocr_boxes  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

np = pytest.importorskip("numpy")
FONT = os.path.join(ROOT, "fonts", "Inter-Bold.ttf")
HAVE_TEXT = ocr_boxes._vision_available() or bool(ocr_boxes.shutil.which("tesseract"))
needs_text = pytest.mark.skipif(not HAVE_TEXT, reason="no OCR backend (macOS Vision or tesseract) on this machine")
needs_vision = pytest.mark.skipif(not ocr_boxes._vision_available(), reason="macOS Vision not available")


@pytest.fixture(autouse=True)
def _fresh_cache():
    ocr_boxes._MEM.clear()
    yield
    ocr_boxes._MEM.clear()


def busy(w=1920, h=1080, seed=3):
    """A photo-like background: gradient + noise."""
    rng = np.random.default_rng(seed)
    g = np.linspace(40, 150, w)[None, :, None] * np.ones((h, 1, 3))
    return Image.fromarray((g + rng.normal(0, 28, (h, w, 3))).clip(0, 255).astype("uint8"))


def text_on_busy(items, size=96, angle=3):
    """items: [(text, cx, cy)]; returns (path-less image, {text: (cx, cy)}) with each text rotated by `angle` degrees around its centre."""
    im = busy().convert("RGBA")
    f = ImageFont.truetype(FONT, size)
    for s, cx, cy in items:
        bb = f.getbbox(s)
        layer = Image.new("RGBA", (bb[2] + 40, bb[3] + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rectangle((0, 0, layer.width, layer.height), fill=(0, 0, 0, 150))      # scrim so the text is legible on noise
        d.text((20, 20), s, font=f, fill=(255, 255, 255, 255))
        layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
        im.alpha_composite(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)))
    return im.convert("RGB")


@needs_text
def test_turkish_text_boxes(tmp_path):
    items = [("Fena değil", 600, 250), ("Dişliler", 1300, 560), ("İçerik ŞĞÜÖÇı", 700, 850)]
    p = tmp_path / "t.png"
    text_on_busy(items).save(p)
    res = ocr_boxes.read_frame(str(p), objects=False)
    assert res["backend"]["text"] in ("vision", "tesseract"), res["reason"]
    for s, cx, cy in items:
        want = ocr_boxes.fold(s)
        hit = [e for e in res["text"] if want in ocr_boxes.fold(e["text"]) or ocr_boxes.fold(e["text"]) in want and len(ocr_boxes.fold(e["text"])) >= 5]
        assert hit, f"{s!r} not found in {[e['text'] for e in res['text']]}"
        e = min(hit, key=lambda e: abs((e["l"] + e["r"]) / 2 - cx) + abs((e["t"] + e["b"]) / 2 - cy))
        assert abs((e["l"] + e["r"]) / 2 - cx) < 60 and abs((e["t"] + e["b"]) / 2 - cy) < 40, (s, e)
        assert e["r"] - e["l"] > 100 and e["b"] - e["t"] > 40


def shapes_frame():
    im = busy(seed=9)
    d = ImageDraw.Draw(im)
    d.ellipse((300, 300, 560, 560), fill=(220, 30, 30))                    # red circle
    d.rectangle((1000, 200, 1500, 500), fill=(240, 210, 40))               # yellow card
    d.polygon([(900, 800), (1200, 800), (1200, 740), (1340, 850), (1200, 960), (1200, 900), (900, 900)], fill=(30, 60, 220))   # blue arrow
    return im


def close(box, want, tol):
    return all(abs(a - b) <= tol for a, b in zip(box, want))


def test_pixel_shapes_always_run():
    regs = ocr_boxes.pixel_regions(shapes_frame())
    for want in ((300, 300, 560, 560), (1000, 200, 1500, 500), (900, 740, 1340, 960)):
        assert any(close(r["box"], want, 20) for r in regs), (want, [r["box"] for r in regs])
    circ = next(r for r in regs if close(r["box"], (300, 300, 560, 560), 20))
    assert ocr_boxes._shape_hint(circ["box"][2] - circ["box"][0], circ["box"][3] - circ["box"][1], circ["fill"]) == "circle"


def test_read_frame_objects_colors_and_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_boxes, "_vision_available", lambda: False)      # force the pure-PIL path on every OS
    monkeypatch.setattr(ocr_boxes.shutil, "which", lambda n: None)
    p = tmp_path / "s.png"
    shapes_frame().save(p)
    res = ocr_boxes.read_frame(str(p))
    objs = res["objects"]
    assert 1 <= len(objs) <= ocr_boxes.MAX_OBJECTS and res["backend"]["objects"] == ["pixels"]
    assert all(o["approx"] is True and o["source"] == "pixels" for o in objs)

    def rgb(h):
        return [int(h[i:i + 2], 16) for i in (1, 3, 5)]
    for want, col in (((300, 300, 560, 560), (220, 30, 30)), ((1000, 200, 1500, 500), (240, 210, 40)), ((900, 740, 1340, 960), (30, 60, 220))):
        o = next(o for o in objs if close(o["box"], want, 20))
        assert max(abs(a - b) for a, b in zip(rgb(o["color"]), col)) < 30, o
    assert "no OCR backend" in res["reason"] or "unavailable" in res["reason"]


@needs_vision
def test_vision_objects(tmp_path):
    p = tmp_path / "s.png"
    shapes_frame().save(p)
    res = ocr_boxes.read_frame(str(p))
    assert "vision" in res["backend"]["objects"]
    for want in ((300, 300, 560, 560), (1000, 200, 1500, 500)):
        assert any(close(o["box"], want, 45) for o in res["objects"]), (want, [o["box"] for o in res["objects"]])
    assert len(res["objects"]) <= ocr_boxes.MAX_OBJECTS


def test_dedupe_keeps_higher_confidence():
    a = {"box": [0, 0, 100, 100], "conf": 0.5}
    b = {"box": [5, 5, 100, 100], "conf": 0.9}
    c = {"box": [300, 300, 400, 400], "conf": 0.1}
    out = ocr_boxes.dedupe([a, b, c])
    assert b in out and a not in out and c in out
    assert len(ocr_boxes.dedupe([{"box": [i * 10, 0, i * 10 + 5, 5], "conf": 1} for i in range(60)])) == ocr_boxes.MAX_OBJECTS


def test_cache_by_image_hash(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ocr_boxes, "_vision_available", lambda: True)
    monkeypatch.setattr(ocr_boxes, "_vision_run", lambda *a: (calls.append(1) or {"text": [{"text": "Hi", "conf": 1, "b": [0.1, 0.1, 0.2, 0.1]}], "sal": [], "rect": []}, ""))
    p = tmp_path / "c.png"
    Image.new("RGB", (200, 100), (10, 10, 10)).save(p)
    cd = str(tmp_path / "cache")
    r1 = ocr_boxes.read_frame(str(p), cache_dir=cd)
    r2 = ocr_boxes.read_frame(str(p), cache_dir=cd)
    assert len(calls) == 1 and r1 == r2 and r1["text"][0]["text"] == "Hi"
    assert r1["text"][0]["t"] == pytest.approx(100 * (1 - 0.1 - 0.1))        # origin flipped from bottom-left
    ocr_boxes._MEM.clear()
    ocr_boxes.read_frame(str(p), cache_dir=cd)                                 # served from disk
    assert len(calls) == 1 and os.listdir(cd)
    q = tmp_path / "d.png"
    Image.new("RGB", (200, 100), (11, 10, 10)).save(q)                         # another image: a fresh run
    ocr_boxes.read_frame(str(q), cache_dir=cd)
    assert len(calls) == 2


def test_missing_backend_returns_empty_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_boxes, "_vision_available", lambda: False)
    monkeypatch.setattr(ocr_boxes.shutil, "which", lambda n: None)
    monkeypatch.setattr(ocr_boxes.sys, "platform", "linux")
    p = tmp_path / "m.png"
    Image.new("RGB", (320, 180), (0, 0, 0)).save(p)
    assert ocr_boxes.read_text(str(p)) == []
    res = ocr_boxes.read_frame(str(p))
    assert res["text"] == [] and "tesseract" in res["reason"]
    assert ocr_boxes.read_text(str(tmp_path / "nope.png")) == []             # unreadable input never raises
    assert "failed" in ocr_boxes.read_frame(str(tmp_path / "nope.png"))["reason"]


def test_things_under_mark(project_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_boxes, "_vision_available", lambda: False)
    monkeypatch.setattr(ocr_boxes.shutil, "which", lambda n: None)
    import io
    buf = io.BytesIO()
    shapes_frame().resize((960, 540)).save(buf, format="PNG")                 # a draft-size frame of a 1920x1080 project
    note = {"id": "n1", "t": 1.0}
    mark = {"type": "ellipse", "x": 250, "y": 250, "w": 400, "h": 400}
    hit = ocr_boxes.things_under_mark(project_dir, note, mark, frame_png=buf.getvalue())
    assert hit and all(h["approx"] is True for h in hit)
    assert close(hit[0]["box"], (300, 300, 560, 560), 25) and hit[0]["kind"] == "shape"
    assert not any(close(h["box"], (1000, 200, 1500, 500), 40) for h in hit)       # the card is outside the mark
    arrow = {"type": "arrow", "x1": 100, "y1": 100, "x2": 1250, "y2": 850}
    assert any(close(h["box"], (900, 740, 1340, 960), 25) for h in ocr_boxes.things_under_mark(project_dir, note, arrow, frame_png=buf.getvalue()))


def add_text_layer(project_dir, text="Fena değil", x=960, y=900, size=110):
    pj = os.path.join(project_dir, "project.json")
    with open(pj, encoding="utf-8") as f:
        proj = json.load(f)
    proj["layers"].insert(0, {"type": "text", "id": "ocr_t", "name": "ocr", "start": 0, "end": 12, "x": x, "y": y, "text": text, "size": size,
                              "color": "#ffffff", "font": "Inter Bold", "shadow": {"blur": 0, "x": 0, "y": 0, "color": "#000000", "opacity": 1}})
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(proj, f)


@needs_text
def test_cli_maps_to_project_px(project_dir, capsys):
    add_text_layer(project_dir)
    rc = ocr_boxes.detect_main([project_dir, "2", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["space"] == "project px"
    hits = [e for e in out["text"] if "fenadegil" in ocr_boxes.fold(e["text"]) or "fena" in ocr_boxes.fold(e["text"])]
    assert hits, [e["text"] for e in out["text"]]
    l, t, r, b = hits[0]["box"]
    assert abs((l + r) / 2 - 960) < 60 and abs((t + b) / 2 - 900) < 70 and r - l > 250     # project px, not the half-size draft
    assert hits[0]["approx"] is True
    assert ocr_boxes.detect_main([project_dir, "-1"]) == 1


def test_cli_errors(tmp_path, capsys):
    assert ocr_boxes.detect_main([str(tmp_path), "1"]) == 1
    assert "No project.json" in capsys.readouterr().err


def test_mcp_tool_registered_and_runs(project_dir, monkeypatch):
    import mcp_server
    assert "detect" in [t[0] for t in mcp_server.TOOLS]
    monkeypatch.setattr(mcp_server, "render_still", lambda d, t, draft=True: _png())
    r = mcp_server.tool_detect({"project_dir": project_dir, "t": 1}, None)
    assert "Boxes are" in r[0]["text"]
    with pytest.raises(mcp_server.ToolError):
        mcp_server.tool_detect({"project_dir": project_dir, "t": -2}, None)


def _png():
    import io
    b = io.BytesIO()
    Image.new("RGB", (960, 540), (0, 0, 0)).save(b, format="PNG")
    return b.getvalue()


def test_ocr_cache_lives_under_the_user_cache_not_the_project(project_dir, monkeypatch):
    import kurgu_paths
    seen = []
    real = ocr_boxes.read_frame

    def spy(path, *a, **k):
        seen.append(k.get("cache_dir"))
        return real(path, *a, **k)
    monkeypatch.setattr(ocr_boxes, "read_frame", spy)
    os.makedirs(os.path.join(project_dir, ".kurgu", "ocr"))
    ocr_boxes.detect_project(project_dir, 1.0)
    assert seen and all(c and os.path.realpath(c).startswith(os.path.realpath(kurgu_paths.cache_dir())) for c in seen)
    assert not any(os.path.realpath(project_dir) in os.path.realpath(c) for c in seen)


def test_detect_refuses_a_folder_that_is_not_a_project(tmp_path):
    (tmp_path / "project.json").write_text("[]")
    with pytest.raises(ValueError):
        ocr_boxes.detect_project(str(tmp_path), 1.0)
