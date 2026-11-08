"""Headless Chrome: a note follows its scene in the editor (SPEC 1.0, "Anchors").

Notes are written in the note box over three scenes; then (1) an edit in the editor makes scene 1 one second longer and shifts the later scenes:
the timeline markers of the later notes move by one second and ONE undo puts layers and notes back together; (2) the save carries the moved
times to project.json; (3) an agent rewrites project.json itself: the editor reloads and shows the markers where the content now is.
Own server on a free port, own Chrome profile and debug port; both are stopped even when a step fails."""
import json
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
CMD = 4 if sys.platform == "darwin" else 2
READY = "window.__kurgu && __kurgu.S.project && document.querySelector('.row')"
WAIT = 60 if os.environ.get("CI") else 20


def prepare(p):
    p["duration"] = 14
    p["notes"] = []
    p["layers"] = [
        {"id": "logo", "type": "image", "src": "media/logo.png", "start": 0.5, "end": 11.5, "scale": 0.3, "x": 1700, "y": 120},   # in front of every scene
        {"id": "s1", "type": "video", "src": "media/a.mp4", "start": 0, "end": 4, "src_in": 0, "name": "Scene 1"},
        {"id": "s2", "type": "video", "src": "media/b.mp4", "start": 4, "end": 8, "src_in": 0, "name": "Scene 2"},
        {"id": "s3", "type": "video", "src": "media/a.mp4", "start": 8, "end": 12, "src_in": 0, "name": "Scene 3"},
        {"id": "music", "type": "audio", "src": "media/muzik.wav", "start": 0, "end": 12},
    ]


@pytest.fixture(scope="module")
def env():
    server = chrome = page = None
    try:
        server = cdp.Server(prepare)
        chrome = cdp.Chrome(width=1500, height=900)
        page = chrome.new_page()
        page.open(server.url("/"), lang="en", view="layers", mode="edit", ready=READY)
        yield server, page
    finally:
        if page:
            page.close()
        if chrome:
            chrome.close()
        if server:
            server.close()


def marker_left(page, text):
    """Left (px) of the ruler marker of the note with this text, and the editor's pixels per second (from the ruler)."""
    return page.eval("(()=>{const m=[...document.querySelectorAll('.lp-note')].find(e=>e.title.endsWith(%s));return m?parseFloat(m.style.left):null})()" % json.dumps("— " + text))


def note_t(page, text):
    return page.eval("__kurgu.S.project.notes.find(n=>n.text===%s).t" % json.dumps(text))


def add_note(page, t, text):
    page.eval("__kurgu.S.t=%r;__kurgu.emit('time')" % t)
    page.click_sel("#nb-input")
    page.wait_for("document.activeElement.id==='nb-input'", WAIT)
    page.send("Input.insertText", text=text)
    page.key("Enter", code="Enter")
    page.wait_for("__kurgu.S.project.notes.some(n=>n.text===%s)" % json.dumps(text), WAIT)


def test_markers_follow_the_scenes(env):
    server, page = env
    for t, text in ((1.5, "on one"), (5.0, "on two"), (9.0, "on three")):
        add_note(page, t, text)
    notes = {n["text"]: n for n in page.eval("__kurgu.S.project.notes")}
    assert notes["on one"]["anchor"]["layer"] == "s1" and notes["on two"]["anchor"]["layer"] == "s2"   # the scene, not the logo in front
    assert [note_t(page, x) for x in ("on one", "on two", "on three")] == [1.5, 5.0, 9.0]
    page.wait_for("(()=>{const ls=[...document.querySelectorAll('.lp-note')];return ls.length===3})()", WAIT)
    x1, x2, x3 = (marker_left(page, x) for x in ("on one", "on two", "on three"))
    pps = x1 / 1.5
    assert abs(x2 - 5.0 * pps) < 1 and abs(x3 - 9.0 * pps) < 1
    undo0 = page.eval("__kurgu.S.undo.length")

    # (1) the editor's own edit: scene 1 one second longer, the later scenes shifted (one change = one undo step)
    page.eval("__kurgu.change(p=>{const L=id=>p.layers.find(l=>l.id===id);L('s1').end=5;L('s2').start=5;L('s2').end=9;L('s3').start=9;L('s3').end=13})")
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='on two').t===6", WAIT)
    assert [note_t(page, x) for x in ("on one", "on two", "on three")] == [1.5, 6.0, 10.0]
    page.wait_for("Math.abs(parseFloat([...document.querySelectorAll('.lp-note')].find(e=>e.title.endsWith('— on two')).style.left)-%r)<1" % (6.0 * pps), WAIT)
    assert abs(marker_left(page, "on three") - 10.0 * pps) < 1 and abs(marker_left(page, "on one") - x1) < 0.5
    assert page.eval("__kurgu.S.undo.length") == undo0 + 1                  # the cascade is part of the same step
    page.key("z", code="KeyZ", modifiers=CMD)
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='on two').t===5", WAIT)
    assert page.eval("__kurgu.S.project.layers.find(l=>l.id==='s2').start") == 4 and note_t(page, "on three") == 9.0
    page.wait_for("Math.abs(parseFloat([...document.querySelectorAll('.lp-note')].find(e=>e.title.endsWith('— on two')).style.left)-%r)<1" % x2, WAIT)
    page.key("z", code="KeyZ", modifiers=CMD | 8)                            # redo: layers and notes forward again together
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='on two').t===6", WAIT)

    # (2) the save carries the moved times to project.json, and the server agrees with the editor
    page.eval("__kurgu.saveAndWait()", await_promise=True)
    with open(os.path.join(server.dir, "project.json"), encoding="utf-8") as f:
        disk = {n["text"]: n for n in json.load(f)["notes"]}
    assert [disk[x]["t"] for x in ("on one", "on two", "on three")] == [1.5, 6.0, 10.0]
    assert {n["text"]: n["t"] for n in server.project()["project"]["notes"]} == {"on one": 1.5, "on two": 6.0, "on three": 10.0}

    # (3) an agent rewrites project.json itself (no server call): scene 2 and 3 move one more second; the editor reloads and the markers follow
    pj = os.path.join(server.dir, "project.json")
    with open(pj, encoding="utf-8") as f:
        p = json.load(f)
    for l in p["layers"]:
        if l["id"] in ("s2", "s3"):
            l["start"] += 1
            l["end"] += 1
    p["layers"][1]["end"] = 6
    tmp = pj + ".agent"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2)
    for i in range(50):                                                      # Windows: the server may have project.json open for a moment
        try:
            os.replace(tmp, pj)
            break
        except PermissionError:
            if i == 49:
                raise
            time.sleep(0.1)
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='on two').t===7", WAIT)
    page.wait_for("Math.abs(parseFloat([...document.querySelectorAll('.lp-note')].find(e=>e.title.endsWith('— on three')).style.left)-%r)<1" % (11.0 * pps), WAIT)
    assert note_t(page, "on one") == 1.5
    assert not page.errors, page.errors


def test_deleting_a_scene_leaves_its_note_where_it_was(env):
    server, page = env
    t_before = note_t(page, "on two")
    page.eval("__kurgu.removeLayers(['s2'])")
    page.wait_for("!__kurgu.S.project.layers.some(l=>l.id==='s2')", WAIT)
    n = page.eval("__kurgu.S.project.notes.find(n=>n.text==='on two')")
    assert n["t"] == t_before and n["anchor"]["stale"] is True
    page.key("z", code="KeyZ", modifiers=CMD)
    page.wait_for("__kurgu.S.project.layers.some(l=>l.id==='s2')", WAIT)
    assert not page.eval("__kurgu.S.project.notes.find(n=>n.text==='on two').anchor.stale")


SCENES_JS = """p.notes=[];p.layers=[
 {id:'logo',type:'image',src:'media/logo.png',start:0.5,end:11.5,scale:0.3,x:1700,y:120},
 {id:'title',type:'text',text:'TITLE',start:0,end:3,x:960,y:540,size:220},
 {id:'s1',type:'video',src:'media/a.mp4',start:0,end:4,src_in:0,name:'Scene 1'},
 {id:'s2',type:'video',src:'media/b.mp4',start:4,end:8,src_in:0,name:'Scene 2',link:'lk_t'},
 {id:'s2a',type:'audio',src:'media/b.mp4',start:4,end:8,src_in:0,name:'Scene 2 sound',link:'lk_t'},
 {id:'s3',type:'video',src:'media/a.mp4',start:8,end:12,src_in:0,name:'Scene 3'},
 {id:'music',type:'audio',src:'media/muzik.wav',start:0,end:12}].map(l=>__kurgu.normalizeLayer?__kurgu.normalizeLayer(l,p):l)"""


def reset(page):
    page.eval("__kurgu.change(p=>{%s})" % SCENES_JS)
    page.wait_for("__kurgu.S.project.layers.length===7 && !__kurgu.S.project.notes.length", WAIT)


def test_a_split_keeps_the_note_on_the_second_half_of_the_picture(env):
    """Splitting a linked video + sound pair: the note on the second half goes to the new PICTURE half (not the logo in front, not the sound half),
    follows it when it moves, ignores the logo moving; one undo puts the split and the anchor back."""
    server, page = env
    reset(page)
    add_note(page, 5.0, "early on two")
    add_note(page, 6.0, "late on two")
    assert page.eval("__kurgu.S.project.notes.find(n=>n.text==='late on two').anchor.layer") == "s2"
    page.eval("__kurgu.splitLayers(['s2','s2a'], 5.5)")
    page.wait_for("__kurgu.S.project.layers.length===9", WAIT)
    halves = page.eval("__kurgu.S.project.layers.filter(l=>l.start===5.5).map(l=>[l.id,l.type])")
    vid = [i for i, t in halves if t == "video"][0]
    late = page.eval("__kurgu.S.project.notes.find(n=>n.text==='late on two')")
    assert late["anchor"]["layer"] == vid and not late["anchor"].get("stale"), (late, halves)
    assert page.eval("__kurgu.S.project.notes.find(n=>n.text==='early on two').anchor.layer") == "s2"
    page.eval("__kurgu.change(p=>{const b=p.layers.find(l=>l.id===%s);b.start+=3;b.end+=3})" % json.dumps(vid))
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='late on two').t===9", WAIT)
    page.eval("__kurgu.change(p=>{const b=p.layers.find(l=>l.id==='logo');b.start+=2;b.end+=2})")
    time.sleep(0.3)
    assert note_t(page, "late on two") == 9 and note_t(page, "early on two") == 5
    for _ in range(3):                                                       # logo move, half move, split: three steps back
        page.key("z", code="KeyZ", modifiers=CMD)
    page.wait_for("__kurgu.S.project.layers.length===7", WAIT)
    late = page.eval("__kurgu.S.project.notes.find(n=>n.text==='late on two')")
    assert late["t"] == 6 and late["anchor"]["layer"] == "s2"
    assert not page.errors, page.errors


def test_a_note_drawn_on_the_title_follows_the_title(env):
    """Review mode: an ellipse drawn on the title anchors the note to the title (what the user pointed at), not to the scene behind it."""
    server, page = env
    reset(page)
    page.eval("__kurgu.saveAndWait()", await_promise=True)
    page.open(server.url("/"), lang="en", view=None, mode="review", ready="window.__kurgu && __kurgu.S.project && document.querySelector('#b-menu')")
    page.eval("__kurgu.S.t=1.5; __kurgu.emit('time')")
    time.sleep(0.6)
    page.click_sel("#nb-input")                                              # writing a note opens the drawing tools
    page.wait_for("!document.querySelector('#note-dock .dt').hidden", WAIT)
    g = "(()=>{const g=__kurgu.viewGeom(),r=document.getElementById('mark-canvas').getBoundingClientRect();return [r.left+g.vox+%s*g.W*g.vz, r.top+g.voy+%s*g.H*g.vz]})()"
    x0, y0 = page.eval(g % (0.42, 0.42))
    x1, y1 = page.eval(g % (0.58, 0.58))
    page.drag(x0, y0, x1, y1, steps=6)
    page.wait_for("[...document.querySelectorAll('.dt-count')].some(c=>c.textContent==='1')", WAIT)
    page.send("Input.insertText", text="title too big")
    page.key("Enter", code="Enter")
    page.wait_for("__kurgu.S.project.notes.some(n=>n.text==='title too big')", WAIT)
    n = page.eval("__kurgu.S.project.notes.find(n=>n.text==='title too big')")
    assert n["marks"][0]["layers"][0]["id"] == "title", n
    assert n["anchor"]["layer"] == "title" and n["anchor"]["sig"] == "text", n
    page.eval("__kurgu.change(p=>{p.layers.find(l=>l.id==='title').text='TITLE, fixed'})")      # the typo the note was about is fixed ...
    page.eval("__kurgu.change(p=>{const l=p.layers.find(l=>l.id==='title');l.start+=1;l.end+=1})")   # ... and the note still follows the title
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text==='title too big').t===2.5", WAIT)
    assert not page.errors, page.errors
