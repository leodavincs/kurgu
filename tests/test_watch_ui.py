"""Watch + note in a real (headless) Chrome: top-bar note box, İzle + not mode (typing pauses, Enter adds and plays on), Send, and the
agent's `notes done` showing up as "fixed". Own server (free port) and own Chrome. Skipped without Chrome / websocket-client."""
import json
import os
import subprocess
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
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


@pytest.fixture(scope="module")
def page(chrome, server):
    p = chrome.new_page()
    p.open(server.url("/"), lang="en")
    yield p
    p.close()


def notes(page):
    return page.eval("__kurgu.S.project.notes.map(n=>({t:n.t,text:n.text,sent:n.sent,done:n.done}))")


def test_1_top_bar_note_box_adds_a_note_and_send_badge_counts(page):
    assert page.eval("!document.getElementById('b-ask')")                       # the assistant command box is gone from the top bar
    assert page.eval("document.getElementById('nb-input').placeholder") == "Add a note…"
    assert page.eval("document.getElementById('b-send').disabled")              # nothing to send yet
    page.eval("__kurgu.S.t=2; __kurgu.emit('time')")
    page.click_sel("#nb-input")
    page.send("Input.insertText", text="first note")
    page.key("Enter", code="Enter")
    page.wait_for("__kurgu.S.project.notes.length===1")
    assert notes(page)[0]["t"] == 2 and notes(page)[0]["text"] == "first note"
    assert page.eval("document.querySelector('#b-send .send-badge').textContent") == "1"
    assert page.eval("!document.getElementById('b-send').disabled")
    assert page.eval("document.querySelector('#b-send .send-label').textContent") == "Send to agent"
    page.key("n", code="KeyN", text="n")                                        # N jumps into the note box
    page.wait_for("document.activeElement.id==='nb-input'")
    page.key("Escape", code="Escape")


def test_2_watch_mode_typing_pauses_enter_adds_and_resumes(page):
    assert page.eval("!!document.getElementById('b-watch') && !document.getElementById('b-frame')")
    page.click_sel("#b-watch")
    page.wait_for("!!document.getElementById('watch') && !document.getElementById('watch').hidden")
    page.eval("__kurgu.S.t=1; __kurgu.emit('time')")
    page.key(" ", code="Space", text=" ")
    page.wait_for("__kurgu.S.playing===true")
    page.key("o", code="KeyO", text="o")                                        # any letter starts a note: video stops
    page.wait_for("__kurgu.S.playing===false")
    assert page.eval("document.activeElement.id==='w-input' && !document.querySelector('.w-paused').hidden")
    page.send("Input.insertText", text="k watch note")
    assert page.eval("document.getElementById('w-input').value") == "ok watch note"
    page.key("Enter", code="Enter")
    page.wait_for("__kurgu.S.project.notes.length===2")
    page.wait_for("__kurgu.S.playing===true")                                   # Enter resumed playback
    n = [x for x in notes(page) if x["text"] == "ok watch note"][0]
    assert 1.0 <= n["t"] <= 1.6, n                                              # pinned to where typing started, not where playback went on
    page.key(" ", code="Space", text=" ")
    page.wait_for("__kurgu.S.playing===false")
    assert page.eval("document.querySelectorAll('#watch .w-note').length") == 2
    assert page.eval("document.querySelector('#wl-send .send-label').textContent") == "Send to agent"          # the count is in the badge only
    assert page.eval("document.querySelector('#wl-send .send-badge').textContent") == "2"
    page.eval("document.getElementById('w-input').focus()")                     # Esc in the box clears it, a second Esc leaves the mode
    page.send("Input.insertText", text="draft")
    page.key("Escape", code="Escape")
    assert page.eval("document.getElementById('w-input').value") == "" and page.eval("!document.getElementById('watch').hidden")
    page.key("Escape", code="Escape")
    page.wait_for("document.getElementById('watch').hidden")
    assert page.eval("document.getElementById('stage').parentNode.id") == "preview-section"   # the viewer went back to the editor


def test_3_send_writes_notlar_inbox_frames_and_ui_marks_sent(page, server):
    page.click_sel("#b-send")
    page.wait_for("__kurgu.S.project.notes.every(n=>n.sent)", 90)
    assert page.eval("document.querySelector('.toast.ok .toast-text').textContent").startswith("2 notes sent to the agent · NOTLAR.md")
    assert page.eval("document.getElementById('b-send').disabled")
    d = server.dir
    md = open(os.path.join(d, "NOTLAR.md"), encoding="utf-8").read()
    assert "first note" in md and "ok watch note" in md and ".kurgu/notes/" in md
    lines = open(os.path.join(d, ".kurgu", "inbox.jsonl"), encoding="utf-8").read().splitlines()
    assert len(lines) == 1 and len(json.loads(lines[0])["notes"]) == 2
    for n in json.load(open(os.path.join(d, "project.json")))["notes"]:
        assert os.path.getsize(os.path.join(d, ".kurgu", "notes", n["id"] + ".jpg")) > 500
    page.click_sel("#b-undo")                                                  # the send is one undo step (UI side)
    page.wait_for("__kurgu.S.project.notes.every(n=>!n.sent)")


def test_4_agent_marks_note_done_ui_shows_fixed_with_reply(page, server):
    page.eval("__kurgu.S.undo.length")                                         # (undo made the notes look unsent locally; send again)
    page.click_sel("#b-send")
    page.wait_for("__kurgu.S.project.notes.every(n=>n.sent)", 90)
    nid = page.eval("__kurgu.S.project.notes[0].id")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "notes", "done", server.dir, nid, "--reply", "Moved the title 0.8 s earlier"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    page.wait_for("__kurgu.S.project.notes.find(n=>n.id==%s).done===true" % json.dumps(nid), 20)
    page.click_sel("#b-notes")
    page.wait_for("!!document.querySelector('.np-row .np-reply')")
    assert page.eval("document.querySelector('.np-row.done .np-state').textContent") == "fixed"
    assert page.eval("document.querySelector('.np-row .np-reply').textContent") == "Moved the title 0.8 s earlier"


def test_5_a_letter_typed_in_another_field_does_not_start_a_note(page):
    page.click_sel("#b-watch")
    page.wait_for("!document.getElementById('watch').hidden")
    page.eval("(()=>{const i=document.createElement('input');i.id='other-field';document.getElementById('watch').append(i);i.focus()})()")
    page.key("q", code="KeyQ", text="q")
    assert page.eval("document.getElementById('w-input').value") == "" and page.eval("document.activeElement.id") == "other-field"
    page.eval("document.getElementById('other-field').remove(); document.activeElement.blur()")
    page.key("q", code="KeyQ", text="q")                                          # with the focus on the page itself a letter still starts a note
    page.wait_for("document.activeElement.id==='w-input'")
    page.key("Escape", code="Escape")
    page.key("Escape", code="Escape")
    page.wait_for("document.getElementById('watch').hidden")


def test_6_f_toggles_fullscreen_but_keeps_the_watch_overlay(page):
    page.click_sel("#b-watch")
    page.wait_for("!document.getElementById('watch').hidden")
    page.key("f", code="KeyF", text="f")
    time.sleep(0.8)
    page.key("f", code="KeyF", text="f")                                          # leaves fullscreen (when the headless browser granted it) ...
    time.sleep(0.8)
    assert page.eval("!document.getElementById('watch').hidden")                  # ... and the overlay is still there
    page.key("Escape", code="Escape")
    page.wait_for("document.getElementById('watch').hidden")
