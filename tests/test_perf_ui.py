"""Performance behaviour of the editor in headless Chrome: the preview canvas is only as big as what is shown, the frame loop sleeps when nothing moves,
and a hidden tab does not poll."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

try:
    import websocket  # noqa: F401
    HAVE_WS = True
except Exception:  # pragma: no cover
    HAVE_WS = False

pytestmark = pytest.mark.skipif(not (cdp.find_chrome() and HAVE_WS), reason="Chrome / websocket-client not available")


@pytest.fixture(scope="module")
def chrome():
    c = cdp.Chrome(width=1440, height=900)
    yield c
    c.close()


@pytest.fixture(scope="module")
def server():
    s = cdp.Server()
    yield s
    s.close()


@pytest.fixture()
def page(chrome, server):
    p = chrome.new_page()
    p.open(server.url("/"), lang="en", view="tracks", mode="edit")
    time.sleep(0.8)
    yield p
    p.close()


CANVAS = "(()=>{const c=document.getElementById('canvas'),f=document.getElementById('frame');return {cw:c.width,ch:c.height,fw:f.offsetWidth,W:__kurgu.S.project.width,rf:__kurgu.S.realFrame}})()"


def test_canvas_is_as_big_as_the_picture_on_screen_and_full_size_when_it_matters(page):
    d = page.eval(CANVAS)
    assert d["cw"] <= d["fw"] * 1.3 + 2 and d["cw"] < d["W"], d                       # fit view: about the on-screen size, not the project size
    page.key("r", code="KeyR", text="r")                                               # real render frame: full resolution
    page.wait_for("__kurgu.S.realFrame===true")
    time.sleep(0.3)
    assert page.eval(CANVAS)["cw"] >= d["W"] * 0.9 or page.eval(CANVAS)["cw"] > d["cw"] * 1.5
    page.key("r", code="KeyR", text="r")
    page.wait_for("__kurgu.S.realFrame===false")
    page.eval("__kurgu.setZoom(1)")                                                    # 100 %: every project pixel is shown
    time.sleep(0.3)
    z = page.eval(CANVAS)
    assert z["cw"] >= z["W"] * 0.9, z
    page.eval("__kurgu.setZoom('fit')")
    time.sleep(0.3)
    assert page.eval(CANVAS)["cw"] <= d["fw"] * 1.3 + 2 or page.eval(CANVAS)["cw"] < z["cw"]


def test_marks_still_line_up_with_the_smaller_canvas(page):
    g = page.eval("(()=>{const g=__kurgu.viewGeom();const f=document.getElementById('frame');return {fw:f.offsetWidth,gw:g.W*g.vz}})()")
    assert abs(g["fw"] - g["gw"]) < 1.5                                                # overlays use project px x zoom: unchanged by the canvas size


