"""Parity of layers edited through the editor UI: keys made with the stopwatch / auto-key, stretched with the side handles,
then the saved project.json is rendered by render.py --frame and compared with the preview canvas (same statistics as test_parity.py).

Set KURGU_PARITY_OUT=<dir> to keep the PNGs (render / preview / amplified diff)."""
import io
import json
import os
import subprocess
import sys
import time

import numpy as np
import pytest
from PIL import Image

import cdp
from chrome_cdp import decode_data_url
from test_parity import make_image, save_debug, stats

try:
    import websocket  # noqa: F401
    HAVE_WS = True
except Exception:  # pragma: no cover
    HAVE_WS = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 640, 360
pytestmark = pytest.mark.skipif(not (cdp.find_chrome() and HAVE_WS), reason="Chrome / websocket-client not available")


def _prepare(p):
    p.update({"width": W, "height": H, "duration": 2, "background": "#203040", "layers": [
        {"id": "img", "type": "image", "src": "media/parity.png", "start": 0, "end": 2, "x": 170, "y": 190, "scale": 0.5, "fit": "contain"},
        {"id": "txt", "type": "text", "text": "KURGU", "font": "bebas-neue-regular", "size": 90, "color": "#ffd400", "start": 0, "end": 2, "x": 450, "y": 80,
         "shadow": {"enabled": False, "blur": 0, "opacity": 0}}]})
    p.pop("notes", None)


def _at(page, lid, t):
    page.eval("__kurgu.S.selection=[%s];__kurgu.S.t=%s;__kurgu.emit('time');__kurgu.emit('selection')" % (json.dumps(lid), t))
    time.sleep(0.4)


def _type(page, prop, text):
    page.click_sel(".field[data-prop=%s] input.field-input" % json.dumps(prop))
    page.send("Input.insertText", text=text)
    page.key("Enter")
    time.sleep(0.3)


def _handles(page, lid):
    return page.eval("import('/web/js/preview.js').then(m=>m.debugHandles(%s))" % json.dumps(lid), await_promise=True)


def _drag_side(page, lid, side, dx, dy):
    x, y = _handles(page, lid)[side]
    page.drag(x, y, x + dx, y + dy, steps=10)
    time.sleep(0.3)


def test_ui_edited_layers_render_like_the_preview(tmp_path):
    chrome = cdp.Chrome(width=1600, height=1000)
    srv = cdp.Server(prepare=_prepare)
    try:
        make_image(os.path.join(srv.dir, "media", "parity.png"))
        page = chrome.new_page()
        page.open(srv.url("/"), lang="en")
        # image: rotation animated with the stopwatch + auto-key, stretched sideways with the east handle (a rotated, stretched, keyed layer)
        _at(page, "img", 0.2)
        page.click_sel(".field[data-prop=rotation] .kf-sw")
        _at(page, "img", 1.6)
        _type(page, "rotation", "35")
        _at(page, "img", 1.0)
        page.click_sel(".field[data-prop=opacity] .kf-sw")
        _at(page, "img", 1.8)
        _type(page, "opacity", "0.6")
        _at(page, "img", 1.0)
        _drag_side(page, "img", "e", 60, 0)          # writes scale_x (static: not animated) and compensates x / y
        img = page.eval("JSON.parse(JSON.stringify(__kurgu.S.project.layers.find(l=>l.id==='img')))")
        assert img["scale_x"] > 1.05 and len(img["keys"]["rotation"]) == 2 and len(img["keys"]["opacity"]) == 2, img
        # text: stretched horizontally (wider, same height)
        _at(page, "txt", 1.0)
        _drag_side(page, "txt", "e", 70, 0)
        txt = page.eval("JSON.parse(JSON.stringify(__kurgu.S.project.layers.find(l=>l.id==='txt')))")
        assert txt["scale_x"] > 1.1 and abs(txt.get("scale_y", 1) - 1) < 1e-9, txt
        # wait until the autosave reached disk
        page.wait_for("__kurgu.S.saveState==='saved'", timeout=15)
        assert srv.project()["project"]["layers"][0]["keys"]["rotation"][1]["v"] == 35
        assert "keys" in srv.project()["project"]["requires"] and "transform" in srv.project()["project"]["requires"]
        res = []
        for t in (1.0, 1.7):
            js = """(async (t) => {
              const st = await import('/web/js/state.js'), pv = await import('/web/js/preview.js');
              const c = document.getElementById('canvas'); let prev = '', cur = '';
              for (let i = 0; i < 40; i++) {
                st.setTime(t); pv.requestRedraw(); await new Promise(r => setTimeout(r, 150));
                cur = c.toDataURL('image/png'); if (cur === prev && i > 3) break; prev = cur;
              }
              return cur; })(%s)""" % t
            pre = np.asarray(Image.open(io.BytesIO(decode_data_url(page.eval(js, await_promise=True)))).convert("RGB"))
            out = str(tmp_path / f"r_{t}.png")
            r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), srv.dir, "--frame", str(t), "--output", out], capture_output=True, text=True, timeout=120)
            assert r.returncode == 0, r.stdout + r.stderr
            ren = np.asarray(Image.open(out).convert("RGB"))
            assert pre.shape == ren.shape == (H, W, 3), (pre.shape, ren.shape)
            res.append((t, ren, pre))
        save_debug(tmp_path, "ui_edited", res)
        allstats = [stats(r, p) for _, r, p in res]
        print("PARITY ui-edited", json.dumps(allstats))
        for s in allstats:
            assert s["mean"] <= (4.0 if sys.platform.startswith("win") else 2.0) and s["inner_mean"] <= 1.2, s      # measured mean 1.3-1.7, inner 0.14-0.93: glyph edges (Pillow vs Chrome) and 8-bit alpha blending of the keyed opacity
        page.close()
    finally:
        chrome.close()
        srv.close()
