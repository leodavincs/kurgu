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


def test_the_frame_loop_sleeps_when_idle_and_wakes_on_change(page):
    page.eval("window.__raf=0;const o=window.requestAnimationFrame.bind(window);window.requestAnimationFrame=f=>{window.__raf++;return o(f)}")
    time.sleep(1.5)
    assert page.eval("window.__raf") < 15                                              # a free-running loop would be ~90 here
    page.eval("window.__raf=0; __kurgu.S.t=3; __kurgu.emit('time')")
    time.sleep(0.5)
    assert page.eval("window.__raf") >= 1                                              # a change wakes it up and the frame is drawn
    page.eval("window.__raf=0")
    page.key(" ", code="Space", text=" ")
    time.sleep(0.6)
    assert page.eval("__kurgu.S.playing") and page.eval("window.__raf") > 20           # playing: one callback per frame
    page.key(" ", code="Space", text=" ")


def test_a_hidden_tab_does_not_poll_the_server(page):
    page.eval("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true}); performance.clearResourceTimings()")
    time.sleep(2.5)
    assert page.eval("performance.getEntriesByType('resource').filter(r=>r.name.includes('/api/version')).length") == 0
    page.eval("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
    time.sleep(1.5)
    assert page.eval("performance.getEntriesByType('resource').filter(r=>r.name.includes('/api/version')).length") >= 1


def test_a_hidden_tab_still_sends_a_slow_keep_alive(page):
    page.eval("window.__pings=0; const of=window.fetch; window.fetch=(u, ...a) => { if (String(u).includes('/api/version')) window.__pings++; return of(u, ...a); }")
    page.eval("window.__kurguHiddenPingMs=700; Object.defineProperty(document,'hidden',{configurable:true,get:()=>true})")
    time.sleep(3.5)
    n = page.eval("window.__pings")
    assert 1 <= n <= 6                                                                 # a few pings (not one per second): the server sees an open editor
    page.eval("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false})")
