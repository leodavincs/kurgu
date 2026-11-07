"""The "Audio" (Ses) pane of the bottom panel in headless Chrome: default when the project has an audio layer, renders the
waveform, click seeks, a note shows as a marker, the choice is remembered, and the old views still work."""
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
READY = "window.__kurgu && __kurgu.S.project && document.querySelector('.lp-seg-btn')"


def seed(p):
    p["notes"] = [{"id": "n_a", "t": 4.0, "text": "sound a bit loud here"}]


@pytest.fixture(scope="module")
def chrome():
    c = cdp.Chrome(width=1440, height=900)
    yield c
    c.close()


@pytest.fixture(scope="module")
def server():
    s = cdp.Server(prepare=seed)
    yield s
    s.close()


@pytest.fixture()
def page(chrome, server):
    p = chrome.new_page()
    p.open(server.url("/"), lang="en", view=None, ready=READY)
    yield p
    p.close()


def pane_on(page):
    return page.eval("document.querySelector('.lp-seg-btn.on').dataset.view")


def test_audio_is_default_renders_and_click_seeks(page):
    assert page.eval("[...document.querySelectorAll('.lp-seg-btn')].map(b=>b.dataset.view).join()") == "audio,tracks,layers"
    assert page.eval("document.querySelector('.lp-seg-btn').textContent") == "Audio"
    assert pane_on(page) == "audio"
    page.wait_for("document.querySelector('.av-wave') && document.querySelector('.av-wave').clientHeight>60")
    # the waveform canvas holds drawn pixels (peaks arrive asynchronously)
    painted = "(()=>{const c=document.querySelector('.av-wave'),d=c.getContext('2d').getImageData(0,c.height/2-30,c.width,60).data;for(let i=3;i<d.length;i+=4)if(d[i]>200)return true;return false})()"
    page.wait_for(painted, 20)
    assert page.eval("document.querySelector('.lp-body').hidden")                  # the layer list is hidden, not gone
    page.eval("__kurgu.S.t=0; __kurgu.emit('time')")
    page.click_sel(".av-main")                                                    # centre of the wave area = middle of the visible window
    page.wait_for("__kurgu.S.t>0.5")
    t1 = page.eval("__kurgu.S.t")
    page.eval("__kurgu.S.t=1; __kurgu.emit('time')")
    page.key("+", code="Equal", text="+")                                         # + zooms in (the 12 s project no longer fits the window)
    page.key("+", code="Equal", text="+")
    c = page.center(".av-over")                                                   # overview: click outside the box = jump there
    page.click(c[0] + 400, c[1])
    page.wait_for("__kurgu.S.t>%s+2" % t1)


def test_note_marker_hover_and_seek(page):
    page.eval("__kurgu.S.t=0; __kurgu.emit('time')")
    page.eval("document.querySelector('.av-main').dispatchEvent(new Event('x'))")
    # window starts at 0 with a 12 s span: the note at 4 s sits at x = width * 4 / 12
    w = page.eval("document.querySelector('.av-main').clientWidth")
    r = page.center(".av-main")
    left = r[0] - w / 2
    x = left + w * 4 / 12
    page.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=r[1])
    page.wait_for("!document.querySelector('.av-tip').hidden")
    assert "sound a bit loud here" in page.eval("document.querySelector('.av-tip').textContent")
    page.click(x, r[1])
    page.wait_for("Math.abs(__kurgu.S.t-4)<0.01")


def test_choice_is_remembered_and_old_views_work(page):
    page.click_sel(".lp-seg-btn[data-view=layers]")
    page.wait_for("!!document.querySelector('.lp-body') && !document.querySelector('.lp-body').hidden && document.querySelectorAll('.row').length>3")
    assert pane_on(page) == "layers" and page.eval("localStorage.getItem('kurgu.layerView')") == "layers"
    page.reload_and_wait(READY)
    time.sleep(0.5)
    assert pane_on(page) == "layers"                                              # remembered
    page.click_sel(".lp-seg-btn[data-view=tracks]")
    page.wait_for("document.querySelectorAll('.lp-list.view-tracks .row, .view-tracks .row').length>0")
    page.click_sel(".lp-seg-btn[data-view=audio]")
    page.wait_for("!document.querySelector('.lp-audio').hidden")
    page.reload_and_wait(READY)
    assert pane_on(page) == "audio"
