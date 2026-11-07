"""Drawing on the frame to say where: ellipse drawn with the mouse while a note is written (Review mode and watch mode), stored on the note as
marks with project-pixel AND normalised coordinates; the note box is centred under the viewer in Review mode; marks come back when the note is shown."""
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
READY = "window.__kurgu && __kurgu.S.project && document.querySelector('#b-menu')"


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
    p.open(server.url("/"), lang="en", view=None, mode="review", ready=READY)
    p.eval("__kurgu.S.t=1.5; __kurgu.emit('time')")
    time.sleep(0.6)
    yield p
    p.close()


def stage_point(page, fx, fy):
    """Client coordinates of the point (fx, fy) in 0-1 frame space."""
    return page.eval("(()=>{const g=__kurgu.viewGeom(),r=document.getElementById('mark-canvas').getBoundingClientRect();return [r.left+g.vox+fx*g.W*g.vz, r.top+g.voy+fy*g.H*g.vz]})()".replace("fx", str(fx)).replace("fy", str(fy)))


def last_note(page):
    return page.eval("__kurgu.S.project.notes[__kurgu.S.project.notes.length-1]")


def test_note_box_is_centred_under_the_video_in_review_mode(page):
    d = page.eval("(()=>{const a=document.getElementById('note-dock').getBoundingClientRect(),f=document.getElementById('frame').getBoundingClientRect();"
                  "return {dockC:a.left+a.width/2, frameC:f.left+f.width/2, dockW:a.width, frameW:f.width, dockTop:a.top, frameBottom:f.bottom, tr:document.getElementById('transport').getBoundingClientRect().top, bar:document.getElementById('note-bar').getBoundingClientRect().width}})()")
    assert abs(d["dockC"] - d["frameC"]) < 2, d                                  # one centre line
    assert 420 <= d["dockW"] <= 720 and d["frameBottom"] <= d["dockTop"] <= d["tr"]   # between viewer and transport
    assert abs(d["bar"] - d["dockW"]) < 2


def draw_ellipse_and_send_note(page, enter_target):
    (x0, y0), (x1, y1) = stage_point(page, 0.30, 0.25), stage_point(page, 0.50, 0.55)
    page.drag(x0, y0, x1, y1, steps=6)
    page.wait_for("document.querySelectorAll('.dt .dt-count').length>0 && [...document.querySelectorAll('.dt-count')].some(c=>c.textContent==='1')")
    page.send("Input.insertText", text="circle this")
    page.key("Enter", code="Enter")
    page.wait_for("__kurgu.S.project.notes.some(n=>n.text==='circle this')")
    n = [x for x in page.eval("__kurgu.S.project.notes") if x["text"] == "circle this"][0]
    m = n["marks"][0]
    assert len(n["marks"]) == 1 and m["type"] == "ellipse"
    assert abs(m["nx"] - 0.30) < 0.01 and abs(m["ny"] - 0.25) < 0.01 and abs(m["nw"] - 0.20) < 0.01 and abs(m["nh"] - 0.30) < 0.01
    W, H = page.eval("[__kurgu.S.project.width, __kurgu.S.project.height]")
    assert abs(m["x"] - m["nx"] * W) <= 1 and abs(m["h"] - m["nh"] * H) <= 1       # pixels and normalised say the same thing
    assert isinstance(m["layers"], list)
    return n


def test_draw_an_ellipse_in_review_mode(page):
    page.click_sel("#nb-input")
    page.wait_for("!document.querySelector('#note-dock .dt').hidden")             # the tool row appears while the note is written
    n = draw_ellipse_and_send_note(page, "review")
    assert abs(n["t"] - 1.5) < 0.05
    page.wait_for("document.querySelector('#note-dock .dt').hidden")
    # the mark comes back when the playhead rests on the note
    page.eval("__kurgu.S.t=4; __kurgu.emit('time')")
    page.eval("__kurgu.S.t=1.5; __kurgu.emit('time')")
    time.sleep(0.3)
    px = page.eval("(()=>{const c=document.getElementById('mark-canvas'),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=3;i<d.length;i+=4)if(d[i]>0)n++;return n})()")
    assert px > 200


def test_draw_an_ellipse_in_watch_mode(page):
    page.click_sel("#b-watch")
    page.wait_for("!document.getElementById('watch').hidden")
    page.eval("__kurgu.S.t=2.5; __kurgu.emit('time')")
    page.key("g", code="KeyG", text="g")                                         # typing starts the note: the frame is paused and can be drawn on
    page.wait_for("!document.querySelector('#watch .dt').hidden")
    page.eval("document.getElementById('w-input').value=''")
    n = draw_ellipse_and_send_note(page, "watch")
    assert abs(n["t"] - 2.5) < 0.1
    page.key("Escape", code="Escape")
    page.wait_for("document.getElementById('watch').hidden")
