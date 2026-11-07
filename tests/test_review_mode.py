"""Review ("İnceleme") vs Edit ("Düzen") mode in headless Chrome: Review is the default, the hamburger menu opens / closes / switches mode,
the right panel lists the notes, Edit still shows the media panel and the inspector, and the choice is remembered without a reload."""
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
VIS = "(s=>{const e=document.querySelector(s);return !!e&&getComputedStyle(e).display!=='none'&&e.getBoundingClientRect().width>0})"


def seed(p):
    p["notes"] = [{"id": "n_a", "t": 2.0, "text": "title is late"}, {"id": "n_b", "t": 5.0, "text": "sound too loud", "sent": "2026-11-07T10:00:00"},
                  {"id": "n_c", "t": 8.0, "text": "fade the end", "sent": "2026-11-07T10:00:00", "done": True, "reply": "Added a 1 s fade"}]


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
    p.open(server.url("/"), lang="en", view=None, mode=None, ready=READY)
    p.eval("localStorage.removeItem('kurgu.mode')")                                            # every test starts from the real default
    p.send("Page.reload")
    p.wait_for(READY, 20)
    time.sleep(0.5)
    yield p
    p.close()


def vis(page, sel):
    return page.eval(VIS + "(%r)" % sel)


def test_review_is_the_default_and_hides_the_editing_chrome(page):
    assert page.eval("document.body.dataset.mode") == "review"
    for sel in ["#project-name", "#note-bar", "#b-undo", "#b-redo", "#b-send", "#b-menu", "#b-play", "#b-watch", "#review-panel", ".av-main"]:
        assert vis(page, sel), sel
    for sel in ["#media-panel", "#inspector", "#scrub", "#b-export", "#lang", "#b-theme", "#b-add", "#b-seq", "#b-guides", "#zoom-select", ".lp-seg"]:
        assert not vis(page, sel), sel + " should be hidden in Review mode"


def test_notes_panel_lists_notes_with_state_and_clicking_seeks(page):
    assert page.eval("document.querySelectorAll('#review-panel .w-note').length") == 3
    rows = page.eval("[...document.querySelectorAll('#review-panel .w-note')].map(r=>r.textContent)")
    assert "sent ✓" in rows[1] and "fixed ✓" in rows[2] and "Added a 1 s fade" in rows[2] and "sent" not in rows[0]
    assert not any(".mp4" in r or ".jpg" in r or ".png" in r for r in rows)             # scene names are never raw file names
    page.click_sel("#review-panel .w-note:nth-child(2)")
    page.wait_for("Math.abs(__kurgu.S.t-5)<0.01")
    assert page.eval("document.querySelector('#rv-send .send-badge').textContent") == "1"
    assert page.eval("document.querySelector('#note-bar #nb-input').placeholder") == "Add a note…"


def test_hamburger_menu_opens_navigates_closes_and_switches_to_edit(page):
    page.click_sel("#b-menu")
    page.wait_for("!!document.getElementById('main-menu')")
    assert page.eval("document.getElementById('b-menu').getAttribute('aria-expanded')") == "true"
    heads = page.eval("[...document.querySelectorAll('#main-menu .mm-head')].map(h=>h.textContent)")
    assert heads == ["Mode", "Project", "Edit", "View", "App"]
    assert page.eval("document.activeElement.getAttribute('aria-checked')") == "true"         # focus starts on the current mode
    page.key("ArrowDown", code="ArrowDown")
    assert page.eval("document.activeElement.textContent").startswith("Edit")
    page.key("Escape", code="Escape")
    page.wait_for("!document.getElementById('main-menu')")
    page.click_sel("#b-menu")
    page.wait_for("!!document.getElementById('main-menu')")
    page.click(5, 500)                                                                          # click outside closes
    page.wait_for("!document.getElementById('main-menu')")
    page.click_sel("#b-menu")
    page.wait_for("!!document.getElementById('main-menu')")
    page.click_sel("#main-menu .mm-item:nth-of-type(2)")                                        # 2nd button = "Edit (full editor)"
    page.wait_for("document.body.dataset.mode==='edit'")
    assert vis(page, "#media-panel") and vis(page, "#inspector") and vis(page, "#b-export") and vis(page, ".lp-seg")
    assert not vis(page, "#review-panel")
    assert page.eval("localStorage.getItem('kurgu.mode')") == "edit"
    assert page.eval("!!window.__kurgu.S.project") and page.eval("__kurgu.S.t") >= 0           # no reload happened


def test_shift_e_toggles_and_the_choice_is_remembered(page):
    page.key("E", code="KeyE", modifiers=8, text="E")
    page.wait_for("document.body.dataset.mode==='edit'")
    page.send("Page.reload")
    page.wait_for(READY, 20)
    page.wait_for("document.body.dataset.mode==='edit'")                                       # mode is applied right after READY; do not race it
    assert page.eval("document.body.dataset.mode") == "edit"
    page.key("E", code="KeyE", modifiers=8, text="E")
    page.wait_for("document.body.dataset.mode==='review'")
