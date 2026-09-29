"""Golden parity: render.py frames vs the preview canvas drawn in headless Chrome (SPEC §1.8, ROADMAP §4).

Set KURGU_PARITY_OUT=<dir> to keep the PNGs (render / preview / amplified diff) for a look."""
import io
import json
import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest
from PIL import Image, ImageDraw

import server
from chrome_cdp import Chrome, decode_data_url, find_chrome

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 640, 360
pytestmark = pytest.mark.skipif(find_chrome() is None, reason="no Chrome/Chromium found (set KURGU_CHROME)")


def make_image(path):
    im = Image.new("RGBA", (400, 260), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((10, 10, 389, 249), 40, fill=(230, 120, 40, 255))
    for i in range(0, 380, 80):                                   # broad bands + a smooth gradient bar: sampling, not stripe aliasing
        d.rectangle((10 + i, 10, 50 + i, 249), fill=(40 + i // 2, 90, 200 - i // 3, 255))
    for x in range(20, 380):
        d.line((x, 215, x, 240), fill=(int(255 * (x - 20) / 360), 120, 60, 255))
    d.ellipse((140, 70, 260, 190), fill=(250, 250, 240, 255))
    d.polygon([(200, 90), (235, 170), (165, 170)], fill=(20, 20, 30, 255))
    im.save(path)


def project_a():
    """image + color layers: rotation, scale, non-uniform scale, anchor, opacity keys, seeded shake, a static flipped/rotated layer."""
    return {"version": 2, "name": "parity", "width": W, "height": H, "fps": 30, "duration": 2, "background": "#101418",
            "layers": [
                {"id": "flip", "type": "image", "src": "media/img.png", "start": 0, "end": 2, "x": 520, "y": 90, "scale": 0.4,
                 "rotation": 15, "scale_x": -1, "fit": "contain"},
                {"id": "hero", "type": "image", "src": "media/img.png", "start": 0, "end": 2, "x": 300, "y": 190, "fit": "contain",
                 "anchor_x": 0.2, "anchor_y": 0.7, "opacity": 1,
                 "keys": {"rotation": [{"t": 0, "v": -25, "ease": "in-out-sine"}, {"t": 2, "v": 40}],
                          "scale": [{"t": 0, "v": 0.35, "ease": "out-back"}, {"t": 1.2, "v": 0.7}],
                          "scale_x": [{"t": 0, "v": 1}, {"t": 2, "v": 0.7}],
                          "opacity": [{"t": 0, "v": 0.3}, {"t": 1, "v": 1}]},
                 "mods": {"x": [{"type": "wiggle", "amp": 12, "freq": 9, "octaves": 2, "seed": 3}],
                          "y": [{"type": "wiggle", "amp": 12, "freq": 9, "octaves": 2, "seed": 3}]}},
            ]}


def project_b():
    """text with the same kinds of motion (fonts differ by up to ~2 px between Pillow and Chrome: looser bound)."""
    return {"version": 2, "name": "parity-text", "width": W, "height": H, "fps": 30, "duration": 2, "background": "#203040",
            "layers": [
                {"id": "t", "type": "text", "text": "KURGU", "font": "bebas-neue-regular", "size": 120, "color": "#ffd400", "start": 0, "end": 2,
                 "x": 320, "y": 180, "shadow": {"enabled": False, "blur": 0, "opacity": 0},
                 "keys": {"rotation": [{"t": 0, "v": -12, "ease": "out-elastic"}, {"t": 1, "v": 0}],
                          "scale": [{"t": 0, "v": 0.5, "ease": "out-back"}, {"t": 0.8, "v": 1}],
                          "opacity": [{"t": 0, "v": 0}, {"t": 0.5, "v": 1}]},
                 "mods": {"x": [{"type": "wiggle", "amp": 8, "freq": 10, "seed": 9}]}}]}


def edge_mask(a):
    """1 px-dilated mask of strong gradients in the render (edges are excluded from the strict statistics)."""
    g = a.astype(np.int16).sum(axis=2)
    m = np.zeros(g.shape, bool)
    m[:, 1:] |= np.abs(np.diff(g, axis=1)) > 24
    m[1:, :] |= np.abs(np.diff(g, axis=0)) > 24
    d = m.copy()
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            d |= np.roll(np.roll(m, dy, 0), dx, 1)
    return d


def run_case(tmp_path, proj, times, chrome_exe):
    pdir = tmp_path / "p"
    (pdir / "media").mkdir(parents=True)
    make_image(str(pdir / "media" / "img.png"))
    (pdir / "project.json").write_text(json.dumps(proj))
    srv = server.make_server(str(pdir), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    ch = Chrome(chrome_exe)
    out = []
    try:
        ch.goto(f"http://127.0.0.1:{port}/")
        for _ in range(100):
            if ch.eval("import('/web/js/state.js').then(m => !!m.S.project)"):
                break
            time.sleep(0.2)
        js = """(async (t) => {
          const st = await import('/web/js/state.js'), pv = await import('/web/js/preview.js');
          const c = document.getElementById('canvas'); let prev = '', cur = '';
          for (let i = 0; i < 40; i++) {
            st.setTime(t); pv.requestRedraw(); await new Promise(r => setTimeout(r, 150));
            cur = c.toDataURL('image/png'); if (cur === prev && i > 3) break; prev = cur;
          }
          return cur; })(%s)"""
        for t in times:
            png = decode_data_url(ch.eval(js % t))
            pre = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
            fr = str(tmp_path / f"r_{t}.png")
            r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), str(pdir), "--frame", str(t), "--output", fr],
                               capture_output=True, text=True, timeout=120)
            assert r.returncode == 0, r.stdout + r.stderr
            ren = np.asarray(Image.open(fr).convert("RGB"))
            assert pre.shape == ren.shape == (H, W, 3), (pre.shape, ren.shape)
            out.append((t, ren, pre))
    finally:
        ch.close()
        srv.shutdown()
        srv.server_close()
    return out


def stats(ren, pre):
    diff = np.abs(ren.astype(np.int16) - pre.astype(np.int16)).max(axis=2)
    m = edge_mask(ren) | edge_mask(pre)
    inner = diff[~m]
    return {"max": int(diff.max()), "mean": float(diff.mean()), "inner_max": int(inner.max()) if inner.size else 0,
            "inner_mean": float(inner.mean()) if inner.size else 0.0, "p999": float(np.percentile(diff, 99.9)),
            "edge_frac": float(m.mean())}


def save_debug(tmp_path, name, results):
    keep = os.environ.get("KURGU_PARITY_OUT")
    if not keep:
        return
    os.makedirs(keep, exist_ok=True)
    for t, ren, pre in results:
        diff = np.clip(np.abs(ren.astype(np.int16) - pre.astype(np.int16)) * 8, 0, 255).astype(np.uint8)
        Image.fromarray(np.concatenate([ren, pre, diff], axis=1)).save(os.path.join(keep, f"{name}_{t}.png"))


def test_parity_image_motion(tmp_path):
    res = run_case(tmp_path, project_a(), [0.3, 1.0, 1.7], find_chrome())
    save_debug(tmp_path, "image", res)
    allstats = [stats(r, p) for _, r, p in res]
    print("PARITY image", json.dumps(allstats))
    for s in allstats:
        # measured: mean 0.7-1.03, interior mean ~0.1, p99.9 <= 57 (only 1-px anti-aliased edges differ, ~0.05 px centroid shift)
        assert s["mean"] <= 1.5 and s["inner_mean"] <= 0.3 and s["p999"] <= 70, s


def test_parity_text_motion(tmp_path):
    res = run_case(tmp_path, project_b(), [0.3, 0.9, 1.6], find_chrome())
    save_debug(tmp_path, "text", res)
    allstats = [stats(r, p) for _, r, p in res]
    print("PARITY text", json.dumps(allstats))
    for s in allstats:
        # measured mean 0.7-1.2 on macOS/Linux; Windows Chrome rasterises glyphs with DirectWrite (~2.3, inner mean ~0.01)
        assert s["mean"] <= (3.0 if sys.platform == "win32" else 2.0), s


def project_c():
    """keyed effect params (blur radius, brightness) on a rotating layer + an axis-aligned layer (snapped like canvas)."""
    return {"version": 2, "name": "parity-fx", "width": W, "height": H, "fps": 30, "duration": 2, "background": "#000000",
            "layers": [
                {"id": "a", "type": "image", "src": "media/img.png", "start": 0, "end": 2, "x": 200, "y": 180, "scale": 0.6, "rotation": 20,
                 "effects": [{"type": "blur", "id": "b1", "radius": 2}, {"type": "brightness", "id": "br", "value": 0}],
                 "keys": {"fx.b1.radius": [{"t": 0, "v": 12}, {"t": 1, "v": 0}], "fx.br.value": [{"t": 0, "v": -0.5}, {"t": 1, "v": 0.3}]}},
                {"id": "b", "type": "image", "src": "media/img.png", "start": 0, "end": 2, "x": 470, "y": 180, "scale": 0.5,
                 "keys": {"x": [{"t": 0, "v": 470.4}, {"t": 2, "v": 500.7, "ease": "out-quad"}], "opacity": [{"t": 0, "v": 0.4}, {"t": 2, "v": 1}]}}]}


def test_parity_keyed_effects_and_snapping(tmp_path):
    res = run_case(tmp_path, project_c(), [0.25, 1.1], find_chrome())
    save_debug(tmp_path, "fx", res)
    allstats = [stats(r, p) for _, r, p in res]
    print("PARITY fx", json.dumps(allstats))
    for s in allstats:
        assert s["mean"] <= 1.5 and s["p999"] <= 70 and s["max"] <= 120, s   # blurred edges are not in the edge mask: no inner_mean bound
