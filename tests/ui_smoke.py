"""UI smoke test: drives the real editor in headless Chrome (CDP). Skipped when Chrome or websocket-client is missing.

It is the safety net for visual work: it only relies on element ids / classes the JS itself depends on, never on looks.
Run:  python3 -m pytest tests/ui_smoke.py -q     (also collected by a plain `pytest`, see conftest / pytest.ini)
"""
import json
import math
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

SHOTS = os.environ.get("KURGU_SHOTS")  # when set, tests also drop screenshots there


def _bench_errors(page):
    return [e for e in page.errors if "favicon" not in e]


@pytest.fixture(scope="module")
def chrome():
    c = cdp.Chrome()
    yield c
    c.close()


@pytest.fixture(scope="module")
def server():
    s = cdp.Server(fake_agent=True)
    yield s
    s.close()


@pytest.fixture(scope="module")
def page(chrome, server):
    p = chrome.new_page()
    p.open(server.url("/"), lang="en")
    yield p
    p.close()


def _sel(page, ids):
    page.eval("__kurgu.S.selection = %s; __kurgu.emit('selection')" % json.dumps(ids))
    time.sleep(0.25)


# ------------------------------------------------------------------------------------------ load
@pytest.mark.parametrize("lang", ["en", "tr"])
def test_loads_without_errors(chrome, server, lang):
    p = chrome.new_page()
    try:
        p.open(server.url("/"), lang=lang)
        assert p.eval("document.documentElement.lang") == lang
        assert p.eval("document.querySelectorAll('.row').length") >= 10
        # no unresolved i18n keys or glyph fallbacks leaked into the top bar
        assert p.eval("[...document.querySelectorAll('#topbar [data-i18n]')].every(e=>e.textContent.trim().length>0)")
        # select a text layer + a video layer + the audio layer + nothing, to exercise every inspector variant
        for ids in (["k_buyuk"], ["k_a"], ["k_muzik"], ["k_slate"], ["k_a", "k_b"], []):
            p.eval("__kurgu.S.selection=%s;__kurgu.emit('selection')" % json.dumps(ids))
            time.sleep(0.2)
        p.eval("window.__kurgu.setLang && 0")
        time.sleep(0.5)
        assert _bench_errors(p) == [], _bench_errors(p)
    finally:
        p.close()


# ------------------------------------------------------------------------------------------ top bar
def test_topbar_controls_exist(page):
    for sel in ["#b-panel", "#project-name", "#b-seq", "#b-undo", "#b-redo", "#b-add", "#b-note", "#b-snap", "#b-frame",
                "#lang button[data-lang=en]", "#lang button[data-lang=tr]", "#b-export", "#b-play", "#b-start", "#b-end", "#timecode",
                "#zoom-select", "#b-guides", "#canvas", "#overlay-canvas", "#inspector", "#layers", "#media-panel", "#splitter"]:
        assert page.center(sel) is not None, "control missing or invisible: " + sel


def test_export_button_has_no_shortcut_hint(page):
    assert page.eval("!document.querySelector('#b-export kbd')")
    assert "⌘" not in page.eval("document.getElementById('b-export').textContent")


def test_export_dialog_opens(page):
    page.click_sel("#b-export")
    page.wait_for("document.querySelector('.xp-overlay') && !document.querySelector('.xp-overlay').hidden")
    assert page.eval("!!document.querySelector('.xp-box select')")
    if SHOTS:
        page.screenshot(os.path.join(SHOTS, "export-dialog.png"))
    page.key("Escape")
    page.wait_for("document.querySelector('.xp-overlay').hidden")


def test_sequence_dialog_opens(page):
    page.click_sel("#b-seq")
    page.wait_for("document.getElementById('seq-modal')")
    assert page.eval("document.querySelectorAll('#seq-modal .seq-preset').length") >= 7
    assert page.eval("!!document.querySelector('#seq-modal canvas')")
    if SHOTS:
        page.screenshot(os.path.join(SHOTS, "sequence-dialog.png"))
    page.key("Escape")
    page.wait_for("!document.getElementById('seq-modal')")


def test_add_layer_menu(page):
    page.click_sel("#b-add")
    page.wait_for("!document.getElementById('menu').hidden")
    assert page.eval("document.querySelectorAll('#menu .menu-item').length") == 3
    page.key("Escape")
    page.wait_for("document.getElementById('menu').hidden")


def test_media_panel_toggle_and_list(page):
    assert page.eval("document.querySelectorAll('#media-panel .mp-item').length") >= 4
    was = page.eval("document.getElementById('main').classList.contains('mp-collapsed')")
    page.click_sel("#b-panel")
    time.sleep(0.4)
    assert page.eval("document.getElementById('main').classList.contains('mp-collapsed')") != was
    page.click_sel("#b-panel")
    time.sleep(0.4)
    assert page.eval("document.getElementById('main').classList.contains('mp-collapsed')") == was
    if SHOTS:
        page.eval("document.querySelector('.mp-view') && document.querySelector('.mp-view').click()")
        time.sleep(0.5)
        page.screenshot(os.path.join(SHOTS, "media-grid.png"))
        page.eval("document.querySelector('.mp-view').click()")
    # fonts tab
    page.eval("document.querySelectorAll('.mp-tab')[1].click()")
    time.sleep(0.3)
    assert page.eval("!!document.querySelector('.mp-list')")
    page.eval("document.querySelectorAll('.mp-tab')[0].click()")
    time.sleep(0.3)


def test_language_switch(page):
    en = page.eval("document.getElementById('b-note').textContent")
    page.click_sel("#lang button[data-lang=tr]")
    page.wait_for("document.documentElement.lang==='tr'")
    tr = page.eval("document.getElementById('b-note').textContent")
    assert tr != en
    page.click_sel("#lang button[data-lang=en]")
    page.wait_for("document.documentElement.lang==='en'")
    assert page.eval("document.getElementById('b-note').textContent") == en
    assert page.eval("document.querySelector('#lang button[data-lang=en]').classList.contains('on')")


def test_theme_toggle_persists(page):
    was = page.eval("document.documentElement.dataset.theme")
    page.click_sel("#b-theme")
    now = page.eval("document.documentElement.dataset.theme")
    assert now in ("dark", "light") and now != was
    assert page.eval("localStorage.getItem('kurgu.theme')") == now
    # tokens really switch (panel colour differs) and the canvas-drawn colours follow
    bg = page.eval("getComputedStyle(document.documentElement).getPropertyValue('--panel').trim()")
    assert bg == ("#fbfaf8" if now == "light" else "#151413")
    page.click_sel("#b-theme")
    assert page.eval("document.documentElement.dataset.theme") == was


def test_every_icon_resolves_to_a_sprite_symbol(page):
    _sel(page, ["k_a"])
    bad = page.eval("[...document.querySelectorAll('svg use')].map(u=>u.getAttribute('href')).filter(h=>!document.querySelector(h))")
    assert bad == [], bad
    assert page.eval("document.querySelectorAll('svg.ic').length") > 30
    # no emoji / pictograph glyphs left as icons in the chrome
    txt = page.eval("document.getElementById('topbar').innerText + document.getElementById('media-panel').innerText + document.getElementById('layers').innerText")
    import re
    assert not re.search("[\U0001F300-\U0001FAFF\u2190-\u21FF\u25A0-\u25FF\u2600-\u27BF\u23E9-\u23FF]", txt), txt


def test_snap_and_note_toggles(page):
    was = page.eval("document.getElementById('b-snap').classList.contains('on')")
    page.click_sel("#b-snap")
    assert page.eval("document.getElementById('b-snap').classList.contains('on')") != was
    page.click_sel("#b-snap")
    page.click_sel("#b-note")
    page.wait_for("!document.getElementById('note-box').hidden")
    page.key("Escape")
    page.wait_for("document.getElementById('note-box').hidden")


# ------------------------------------------------------------------------------------------ selection / inspector
def test_select_layer_shows_inspector_and_ask_ai(page):
    page.eval("__kurgu.S.selection=[];__kurgu.emit('selection');document.querySelector('.lp-body').scrollTop=0")
    time.sleep(0.2)
    # click a row's name in the layer panel
    c = page.eval("(()=>{const r=[...document.querySelectorAll('.row')].find(x=>x.querySelector('.name')&&x.querySelector('.name').textContent==='Big title');const n=r.querySelector('.name').getBoundingClientRect();return [n.left+n.width/2,n.top+n.height/2]})()")
    page.click(*c)
    page.wait_for("__kurgu.S.selection.join()==='k_buyuk'")
    assert page.eval("document.querySelectorAll('#inspector .section').length") >= 4
    assert page.eval("!!document.querySelector('#inspector .ai-box textarea.ai-input')")
    assert page.eval("!!document.querySelector('#inspector .ai-box .ai-go')")
    assert page.eval("!!document.querySelector('#inspector .font-btn')")
    assert page.eval("document.querySelectorAll('#inspector input.field-input').length") >= 8
    if SHOTS:
        page.screenshot(os.path.join(SHOTS, "editor-selected.png"))
        box = page.eval("(()=>{const r=document.querySelector('.ai-box').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
        page.screenshot(os.path.join(SHOTS, "ask-ai.png"), clip={"x": box[0] - 8, "y": box[1] - 8, "width": box[2] + 16, "height": min(box[3] + 16, 400)})


def test_ask_ai_box_runs_the_agent_and_can_be_undone(page):
    _sel(page, ["k_buyuk"])
    o0 = page.eval("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').opacity")
    page.eval("document.querySelector('#inspector .ai-box').scrollIntoView({block:'center'})")
    page.click_sel("#inspector .ai-box textarea.ai-input")
    page.send("Input.insertText", text="make it a bit see-through")
    assert page.eval("!document.querySelector('#inspector .ai-go').disabled")
    page.click_sel("#inspector .ai-go")
    page.wait_for("document.querySelector('#inspector .ai-msg.agent') || document.querySelector('#inspector .ai-status.error')", 30)
    assert page.eval("!!document.querySelector('#inspector .ai-msg.agent:not(.err)')"), page.eval("document.querySelector('#inspector .ai-box').innerText")
    page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').opacity!==%s" % o0, 10)
    if SHOTS:
        page.screenshot(os.path.join(SHOTS, "ask-ai-done.png"))
    page.key("z", modifiers=4 if sys.platform == "darwin" else 2)
    page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').opacity===%s" % o0, 10)


def test_command_field_focuses_the_ask_box(page):
    _sel(page, ["k_a"])
    page.click_sel("#b-ask")
    page.wait_for("document.activeElement && document.activeElement.classList.contains('ai-input')")


def test_font_picker_opens(page):
    _sel(page, ["k_buyuk"])
    page.click_sel("#inspector .font-btn")
    page.wait_for("!!document.querySelector('.font-menu .font-item')")
    assert page.eval("document.querySelectorAll('.font-menu .font-item').length") > 3
    if SHOTS:
        page.screenshot(os.path.join(SHOTS, "font-picker.png"))
    page.key("Escape")
    page.wait_for("!document.querySelector('.font-menu')")


def test_number_field_typing_changes_layer(page):
    _sel(page, ["k_buyuk"])
    x0 = page.eval("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').x")
    c = page.eval("(()=>{const f=[...document.querySelectorAll('#inspector .field')].find(f=>f.querySelector('.field-label').textContent==='X');f.scrollIntoView({block:'center'});const i=f.querySelector('input').getBoundingClientRect();return [i.left+i.width/2,i.top+i.height/2]})()")
    page.click(*c)
    page.key("a", modifiers=4 if sys.platform == "darwin" else 2)
    page.send("Input.insertText", text="1000")
    page.key("Enter")
    page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').x===1000")
    page.click_sel("#b-undo")
    page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').x===%s" % x0)


# ------------------------------------------------------------------------------------------ viewer interaction
def _project_x(server, lid):
    return [l for l in server.project()["project"]["layers"] if l["id"] == lid][0]["x"] if "project" in server.project() else \
        [l for l in server.project()["layers"] if l["id"] == lid][0]["x"]


def _viewer_point(page, lid):
    """Viewport css px of a layer's box centre at the current time."""
    return page.eval("""(()=>{const l=__kurgu.S.project.layers.find(x=>x.id===%s);const b=__kurgu.layerBox(l);const v=__kurgu.viewInfo();
      const r=document.getElementById('overlay-canvas').getBoundingClientRect();return [r.left+v.ox+b.cx*v.z, r.top+v.oy+b.cy*v.z, v.z]})()""" % json.dumps(lid))


def test_click_text_in_viewer_selects_and_drag_moves(page, server):
    page.eval("__kurgu.S.selection=[];__kurgu.S.t=2;__kurgu.emit('time');__kurgu.emit('selection')")
    time.sleep(0.4)
    x, y, z = _viewer_point(page, "k_buyuk")
    x0 = page.eval("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').x")
    page.click(x, y)
    page.wait_for("__kurgu.S.selection.join()==='k_buyuk'")
    dx = 80
    page.drag(x, y, x + dx, y)
    page.wait_for("Math.abs(__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').x-%s)>20" % x0)
    x1 = page.eval("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').x")
    assert abs((x1 - x0) - dx / z) <= 12 / z + 1, (x0, x1, z)

    def saved():
        pr = server.project()
        pr = pr.get("project", pr)
        return [l for l in pr["layers"] if l["id"] == "k_buyuk"][0]["x"]
    t0 = time.time()
    while time.time() - t0 < 8 and saved() != x1:
        time.sleep(0.2)
    assert saved() == x1, "drag did not reach /api/project"
    # undo restores it
    page.click_sel("#b-undo")
    page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').x===%s" % x0)
    time.sleep(1.2)
    assert saved() == x0


# ------------------------------------------------------------------------------------------ time
def test_playback_advances_time(page):
    page.eval("__kurgu.S.selection=[];__kurgu.S.t=0;__kurgu.emit('time');__kurgu.emit('selection')")
    time.sleep(0.2)
    page.click_sel("#b-play")
    page.wait_for("__kurgu.S.playing===true")
    time.sleep(0.9)
    t = page.eval("__kurgu.S.t")
    assert t > 0.4, t
    assert page.eval("document.getElementById('timecode').textContent.startsWith('00:0')")
    page.click_sel("#b-play")
    page.wait_for("__kurgu.S.playing===false")


def test_ruler_scrub(page):
    dur = page.eval("__kurgu.S.project.duration")
    r = page.eval("(()=>{const r=document.querySelector('.lp-ruler-strip').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()")
    x = r[0] + r[2] * 0.5
    y = r[1] + r[3] / 2
    page.click(x, y)
    t = page.eval("__kurgu.S.t")
    assert abs(t - dur * 0.5) < 0.6, (t, dur)
    page.drag(x, y, r[0] + r[2] * 0.25, y)
    t = page.eval("__kurgu.S.t")
    assert abs(t - dur * 0.25) < 0.6, t
    # transport scrub bar as well
    s = page.eval("(()=>{const r=document.getElementById('scrub')?.getBoundingClientRect();return r?[r.left,r.top,r.width,r.height]:null})()")
    if s and s[2] > 10:
        page.click(s[0] + s[2] * 0.8, s[1] + s[3] / 2)
        assert abs(page.eval("__kurgu.S.t") - dur * 0.8) < 0.8


def test_layers_timeline_bars_render(page):
    assert page.eval("document.querySelectorAll('.row .bar').length") >= 10
    assert page.eval("!!document.querySelector('.lp-line')")


# ------------------------------------------------------------------------------------------ more behaviour that layout work could break
def _undo_all(page, n=1):
    for _ in range(n):
        page.click_sel("#b-undo")
        time.sleep(0.15)


def test_layer_row_toggles_and_rename(page):
    page.eval("document.querySelector('.lp-body').scrollTop=0")
    time.sleep(0.2)
    row = "(()=>{const r=[...document.querySelectorAll('.row')].find(x=>x.querySelector('.name')&&x.querySelector('.name').textContent==='Left aligned');return r})()"
    page.eval("window.__row=%s" % row)
    for cls, field in (("eye", "hidden"), ("lock", "locked")):
        c = page.eval("(()=>{const b=window.__row.querySelector('.icon-btn.%s');const r=b.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()" % cls)
        page.click(*c)
        page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_sol').%s===true" % field)
        page.click(*c)
        page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_sol').%s===false" % field)
    # double click renames
    c = page.eval("(()=>{const n=window.__row.querySelector('.name').getBoundingClientRect();return [n.left+n.width/2,n.top+n.height/2]})()")
    page.click(*c, clicks=2)
    page.wait_for("!!document.querySelector('.name-edit')")
    page.key("a", modifiers=4 if sys.platform == "darwin" else 2)
    page.send("Input.insertText", text="Renamed")
    page.key("Enter")
    page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_sol').name==='Renamed'")
    _undo_all(page)
    page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_sol').name==='Left aligned'")


def test_add_text_layer_from_menu_and_note(page):
    n0 = page.eval("__kurgu.S.project.layers.length")
    page.click_sel("#b-add")
    page.wait_for("!document.getElementById('menu').hidden")
    page.click_sel("#menu .menu-item")
    page.wait_for("__kurgu.S.project.layers.length===%d" % (n0 + 1))
    _undo_all(page)
    page.wait_for("__kurgu.S.project.layers.length===%d" % n0)
    nn = page.eval("__kurgu.S.project.notes.length")
    page.click_sel("#b-note")
    page.wait_for("!document.getElementById('note-box').hidden")
    page.send("Input.insertText", text="hello")
    page.key("Enter")
    page.wait_for("__kurgu.S.project.notes.length===%d" % (nn + 1))
    _undo_all(page)
    page.wait_for("__kurgu.S.project.notes.length===%d" % nn)


def test_zoom_buttons_and_guides_menu(page):
    z0 = page.eval("__kurgu.viewInfo().z")
    page.click_sel("#b-zoom-in")
    page.wait_for("__kurgu.viewInfo().z>%s" % z0)
    page.click_sel("#b-zoom-out")
    page.eval("__kurgu.setZoom('fit')")
    page.click_sel("#b-guides")
    page.wait_for("!document.getElementById('menu').hidden")
    page.click_sel("#menu .menu-item")
    page.wait_for("__kurgu.S.view.safe===true")
    assert page.eval("document.querySelector('#b-guides').classList.contains('on')")
    page.click_sel("#b-guides")
    page.click_sel("#menu .menu-item")
    page.wait_for("__kurgu.S.view.safe===false")


def test_real_frame_toggle(page):
    page.click_sel("#b-frame")
    page.wait_for("__kurgu.S.realFrame===true")
    assert page.eval("document.getElementById('b-frame').classList.contains('on')")
    time.sleep(1.2)
    page.click_sel("#b-frame")
    page.wait_for("__kurgu.S.realFrame===false")


def test_media_panel_drag_to_timeline_adds_a_layer(page):
    n0 = page.eval("__kurgu.S.project.layers.length")
    page.eval("document.querySelector('.lp-body').scrollTop=0")
    src = page.center("#media-panel .mp-item")
    dst = page.eval("(()=>{const r=document.querySelector('.lp-ruler-strip').getBoundingClientRect();const b=document.querySelector('.lp-body').getBoundingClientRect();return [r.left+r.width*0.3, b.top+b.height*0.6]})()")
    page.drag(src[0], src[1], dst[0], dst[1], steps=12)
    page.wait_for("__kurgu.S.project.layers.length===%d" % (n0 + 2))       # a video with sound arrives as picture + linked sound (default)
    _undo_all(page)
    page.wait_for("__kurgu.S.project.layers.length===%d" % n0)


def test_splitter_resizes_the_timeline(page):
    h0 = page.eval("document.getElementById('layers').getBoundingClientRect().height")
    c = page.center("#splitter")
    page.drag(c[0], c[1], c[0], c[1] - 60, steps=6)
    h1 = page.eval("document.getElementById('layers').getBoundingClientRect().height")
    assert abs((h1 - h0) - 60) < 6, (h0, h1)
    assert abs(page.eval("document.getElementById('splitter').getBoundingClientRect().top+5") - (c[1] - 60)) < 6  # handle follows the pointer
    c2 = page.center("#splitter")
    page.drag(c2[0], c2[1], c2[0], c2[1] + 60, steps=6)


def test_sequence_dialog_applies_fps(page):
    fps0 = page.eval("__kurgu.S.project.fps")
    page.click_sel("#b-seq")
    page.wait_for("document.getElementById('seq-modal')")
    page.eval("[...document.querySelectorAll('.seq-fps .btn')].find(b=>b.textContent==='24').click()")
    page.wait_for("!document.querySelector('#seq-modal .btn.primary').disabled")
    page.click_sel("#seq-modal .btn.primary")
    page.wait_for("__kurgu.S.project.fps===24")
    _undo_all(page)
    page.wait_for("__kurgu.S.project.fps===%s" % fps0)


def test_draft_render_shows_progress_and_cancels(page):
    page.click_sel("#b-export")
    page.wait_for("!document.querySelector('.xp-overlay').hidden")
    page.eval("[...document.querySelectorAll('.xp-foot .btn')].find(b=>!b.classList.contains('primary') && !b.hidden).click()")
    page.wait_for("!document.getElementById('progress').hidden", 20)
    assert page.eval("document.getElementById('progress').textContent.length") > 0
    page.click_sel("#b-cancel")
    page.wait_for("document.getElementById('progress').hidden", 30)


# ------------------------------------------------------------------------------------------ Turkish / long strings must not clip
CLIP_JS = """(()=>{
  const sel='#topbar .btn, #topbar .seg button, #topbar .ask span, #topbar #seq-label, #media-panel .seg button, #media-panel .mp-drophint span, #media-panel .mp-sort, #media-panel .mp-dir .name,'
   +'#inspector .field-label, #inspector .pbtn, #inspector .section-head span, #inspector .hint, #inspector .ai-title span, #inspector .ai-go, #inspector .ai-scope, #inspector .effect-name, #inspector .panel-head .sub,'
   +'#transport #zoom-select, #transport #tc-now, #layers .lp-title, #layers .lp-btn, .menu-item, .font-group, .xp-box label, .xp-foot .btn, .xp-warn, .xp-head b, .seq-box b, .seq-h, .seq-foot .btn, .seq-hint, .seq-pt span, .seq-radio span, .xp-adv summary';
  const bad=[];
  for (const e of document.querySelectorAll(sel)) {
    const r=e.getBoundingClientRect(); if(!r.width||!r.height) continue;
    if (e.tagName==='SELECT') continue;
    if (e.scrollWidth > e.clientWidth + 1) bad.push((e.id||e.className||e.tagName)+': '+(e.textContent||'').trim().slice(0,40)+' ['+e.scrollWidth+'>'+e.clientWidth+']');
  }
  return bad;
})()"""


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_no_clipped_labels(chrome, server, lang):
    p = chrome.new_page()
    try:
        p.open(server.url("/"), lang=lang)
        found = []
        for ids in ([], ["k_buyuk"], ["k_a"], ["k_muzik"], ["k_a", "k_b"]):
            p.eval("__kurgu.S.selection=%s;__kurgu.emit('selection')" % json.dumps(ids))
            time.sleep(0.3)
            found += ["%s | %s" % (ids, b) for b in p.eval(CLIP_JS)]
        p.click_sel("#b-add"); time.sleep(0.3)
        found += ["menu | " + b for b in p.eval(CLIP_JS)]
        p.key("Escape")
        p.click_sel("#b-export"); p.wait_for("!document.querySelector('.xp-overlay').hidden"); time.sleep(0.3)
        p.eval("document.querySelector('.xp-adv').open=true"); time.sleep(0.2)
        found += ["export | " + b for b in p.eval(CLIP_JS)]
        p.key("Escape")
        p.click_sel("#b-seq"); p.wait_for("document.getElementById('seq-modal')"); time.sleep(0.4)
        found += ["sequence | " + b for b in p.eval(CLIP_JS)]
        assert found == [], "\n".join(found)
    finally:
        p.close()


# ------------------------------------------------------------------------------------------ no errors overall
def test_no_console_errors_in_session(page):
    time.sleep(0.5)
    assert _bench_errors(page) == [], _bench_errors(page)


# ------------------------------------------------------------------------------------------ pixel comparison with render.py
def _text_only(p):
    p["layers"] = [l for l in p["layers"] if l["id"] in ("k_buyuk", "k_orta")]
    for l in p["layers"]:
        l["fade_in"] = 0
        l["fade_out"] = 0
        l["start"] = 0
        l["end"] = 6
        l["shadow"]["enabled"] = False
    p["global"].update(vignette=0, grain=0, fade_out=0)
    p["background"] = "#000000"


def _bbox(img, thr=90):
    import numpy as np
    a = np.asarray(img.convert("RGB")).max(axis=2) > thr
    ys, xs = np.where(a)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None


def test_preview_text_matches_render_frame(chrome):
    from PIL import Image
    import base64
    import io
    srv = cdp.Server(prepare=_text_only)
    p = chrome.new_page()
    try:
        p.open(srv.url("/?t=2"), lang="en", ready="window.__kurgu && __kurgu.S.project && document.querySelector('.row')")
        # wait until the fonts are loaded and the canvas stops changing
        last, box = None, None
        t0 = time.time()
        while time.time() - t0 < 15:
            data = p.eval("document.getElementById('canvas').toDataURL('image/png')")
            img = Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1])))
            box = _bbox(img)
            if box and box == last:
                break
            last = box
            time.sleep(0.5)
        assert box, "preview canvas is empty"
        out = os.path.join(srv.tmp, "frame.png")
        subprocess.run([sys.executable, os.path.join(cdp.ROOT, "render.py"), srv.dir, "--frame", "2", "--output", out],
                       check=True, cwd=cdp.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=dict(os.environ, KURGU_CACHE=os.path.join(srv.tmp, "cache")))
        ref = _bbox(Image.open(out))
        assert ref, "render.py frame is empty"
        assert img.size == Image.open(out).size
        print('bbox preview', box, 'render', ref)   # Windows: Chrome (DirectWrite) sets Inter\'s line box / descenders ~12 px differently from Pillow
        for a, b in zip(box, ref):
            assert abs(a - b) <= (16 if sys.platform.startswith("win") else 3), ("preview bbox", box, "render bbox", ref)
    finally:
        p.close()
        srv.close()


# ------------------------------------------------------------------------------------------ keyframes (web/js/keyframes.js)
def _layer(page, lid):
    return page.eval("JSON.parse(JSON.stringify(__kurgu.S.project.layers.find(l=>l.id===%s)))" % json.dumps(lid))


def _at(page, lid, t):
    page.eval("__kurgu.S.selection=[%s];__kurgu.S.t=%s;__kurgu.emit('time');__kurgu.emit('selection')" % (json.dumps(lid), t))
    time.sleep(0.35)


def _type_in_field(page, prop, text):
    sel = ".field[data-prop=%s] input.field-input" % json.dumps(prop)
    page.click_sel(sel)
    page.send("Input.insertText", text=text)
    page.key("Enter")
    time.sleep(0.3)


def test_kf_stopwatch_creates_a_key_and_autokey_adds_the_second(page):
    _at(page, "k_buyuk", 2.0)           # layer starts at 1 s -> local 1.0
    assert "keys" not in _layer(page, "k_buyuk")
    page.click_sel(".field[data-prop=rotation] .kf-sw")
    page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_buyuk').keys")
    ks = _layer(page, "k_buyuk")["keys"]["rotation"]
    assert len(ks) == 1 and abs(ks[0]["t"] - 1.0) < 1e-6 and ks[0]["v"] == 0, ks
    assert page.eval("document.querySelector('.field[data-prop=rotation] .kf-sw').classList.contains('on')")
    _at(page, "k_buyuk", 3.0)           # local 2.0
    _type_in_field(page, "rotation", "90")
    ks = _layer(page, "k_buyuk")["keys"]["rotation"]
    assert len(ks) == 2 and abs(ks[1]["t"] - 2.0) < 1e-6 and ks[1]["v"] == 90, ks
    # the field now shows the evaluated value at the playhead, halfway between the keys
    _at(page, "k_buyuk", 2.5)
    assert abs(float(page.eval("document.querySelector('.field[data-prop=rotation] input').value").replace(",", ".")) - 45) < 0.6
    # ◀ ▶ hop between keys
    page.click_sel(".field[data-prop=rotation] .kf-next")
    assert abs(page.eval("__kurgu.S.t") - 3.0) < 1e-6
    page.click_sel(".field[data-prop=rotation] .kf-prev")
    assert abs(page.eval("__kurgu.S.t") - 2.0) < 1e-6
    # saved with the stamped `requires`
    t0 = time.time()
    while time.time() - t0 < 8 and "rotation" not in (_saved_keys(page) or {}):
        time.sleep(0.2)
    assert "transform" in (json.loads(page.eval("fetch('/api/project').then(r=>r.text())", await_promise=True))["project"].get("requires") or [])


def _saved_keys(page):
    pr = json.loads(page.eval("fetch('/api/project').then(r=>r.text())", await_promise=True))["project"]
    return [l for l in pr["layers"] if l["id"] == "k_buyuk"][0].get("keys")


def test_kf_lane_diamonds_retime_and_ease_picker(page):
    _at(page, "k_buyuk", 3.0)
    page.click_sel(".row[data-id=k_buyuk]:not(.lane) .twirl")
    page.wait_for("document.querySelectorAll('.row.lane[data-id=k_buyuk][data-prop=rotation] .kf').length===2")
    assert page.eval("document.querySelectorAll('.row[data-id=k_buyuk]:not(.lane) .kf-sum i').length") == 2   # collapsed-row summary ticks
    d = page.eval("[...document.querySelectorAll('.row.lane[data-id=k_buyuk][data-prop=rotation] .kf')].map(e=>{const r=e.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})")
    pps = (d[1][0] - d[0][0]) / 1.0
    # easing picker: double-click the first diamond, pick "Ease out"
    page.click(d[0][0], d[0][1], clicks=2)
    page.wait_for("document.querySelector('.kf-ease-pop')")
    assert page.eval("document.querySelectorAll('.kf-ease-tile').length") == 9 and page.eval("!!document.querySelector('.kf-bez')")
    page.click_sel(".kf-ease-tile[title='Ease out']")
    time.sleep(0.3)
    assert _layer(page, "k_buyuk")["keys"]["rotation"][0]["ease"] == "ease-out"
    assert page.eval("!!document.querySelector('.row.lane[data-id=k_buyuk] .kf.k-ease')")            # shape follows the easing
    # custom curve: drag a bezier handle -> ease becomes a 4-number array
    h = page.eval("(()=>{const r=document.querySelector('.kf-bez .h1').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()")
    page.drag(h[0], h[1], h[0] + 20, h[1] - 25)
    e = _layer(page, "k_buyuk")["keys"]["rotation"][0]["ease"]
    assert isinstance(e, list) and len(e) == 4, e
    page.key("Escape")
    page.wait_for("!document.querySelector('.kf-ease-pop')")
    # hold from the popover (E with the key selected) -> square diamond
    page.click(d[0][0], d[0][1])
    page.key("e")
    page.wait_for("document.querySelector('.kf-ease-pop')")
    page.click_sel(".kf-ease-tile[title='Hold']")
    assert _layer(page, "k_buyuk")["keys"]["rotation"][0].get("hold") is True
    page.key("Escape")
    # drag the second diamond +0.5 s: retimed (frame-snapped), one undo step
    n_undo = page.eval("__kurgu.S.undo.length")
    page.drag(d[1][0], d[1][1], d[1][0] + 0.5 * pps, d[1][1], steps=10)
    t2 = _layer(page, "k_buyuk")["keys"]["rotation"][1]["t"]
    assert abs(t2 - 2.5) < 0.06, t2
    assert page.eval("__kurgu.S.undo.length") - n_undo == 1
    # Delete removes the selected key (and not the layer)
    page.key("Delete")
    time.sleep(0.3)
    ks = _layer(page, "k_buyuk")["keys"]["rotation"]
    assert len(ks) == 1 and page.eval("!!__kurgu.S.project.layers.find(l=>l.id==='k_buyuk')")
    if SHOTS:
        page.screenshot(os.path.join(SHOTS, "keyframe-lane.png"))
    _at(page, "k_buyuk", 3.0)


def test_kf_rotate_handle_writes_a_rotation_key(page):
    _at(page, "k_buyuk", 2.5)           # local 1.5: no key there yet
    n = len(_layer(page, "k_buyuk")["keys"]["rotation"])
    v0 = page.eval("(async()=>{const k=await import('/web/js/keyframes.js');return k.valueAt(__kurgu.S.project.layers.find(l=>l.id==='k_buyuk'),'rotation')})()", await_promise=True)
    hd = page.eval("import('/web/js/preview.js').then(m=>m.debugHandles('k_buyuk'))", await_promise=True)
    cx, cy = hd["anchor"]
    rx, ry = hd["rot"]
    a = math.atan2(ry - cy, rx - cx) + math.pi / 2       # a quarter turn clockwise around the anchor
    r = math.hypot(rx - cx, ry - cy)
    page.drag(rx, ry, cx + r * math.cos(a), cy + r * math.sin(a), steps=12)
    ks = _layer(page, "k_buyuk")["keys"]["rotation"]
    assert len(ks) == n + 1, ks
    new = [k for k in ks if abs(k["t"] - 1.5) < 1e-6][0]
    assert abs(new["v"] - (v0 + 90)) < 3, (new, v0)


def test_kf_side_handle_stretches_one_axis_and_keeps_the_opposite_edge(page):
    _at(page, "k_orta", 3.0)            # static text layer 2 - 6 s
    page.eval("__kurgu.change(P=>{const l=P.layers.find(x=>x.id==='k_orta');l.rotation=30})")
    time.sleep(0.3)
    hd = page.eval("import('/web/js/preview.js').then(m=>m.debugHandles('k_orta'))", await_promise=True)
    before = hd["w"]
    ex, ey = hd["e"]
    # pull the east handle further along the layer's own (rotated) x axis
    ang = math.radians(30)
    page.drag(ex, ey, ex + 90 * math.cos(ang), ey + 90 * math.sin(ang), steps=10)
    l = _layer(page, "k_orta")
    assert l["scale_x"] > 1.1 and abs(l.get("scale_y", 1) - 1) < 1e-9 and abs(l["scale"] - 1) < 1e-9, l
    after = page.eval("import('/web/js/preview.js').then(m=>m.debugHandles('k_orta'))", await_promise=True)["w"]
    assert abs(after[0] - before[0]) < 1.5 and abs(after[1] - before[1]) < 1.5, (before, after)   # opposite (west) edge did not move
    assert "width" in page.eval("(async()=>{const s=await import('/web/js/summary.js');const a=JSON.parse(JSON.stringify(__kurgu.S.project));a.layers.find(x=>x.id==='k_orta').scale_x=1;return s.makeSummary(a,__kurgu.S.project).join('|')})()", await_promise=True)
    page.eval("__kurgu.change(P=>{const l=P.layers.find(x=>x.id==='k_orta');delete l.rotation;delete l.scale_x})")


def test_kf_alt_drag_anchor_keeps_the_layer_in_place(page):
    _at(page, "k_orta", 3.0)
    hd = page.eval("import('/web/js/preview.js').then(m=>m.debugHandles('k_orta'))", await_promise=True)
    ax, ay = hd["anchor"]
    nw0 = hd["nw"]
    page.mouse("mouseMoved", ax, ay, modifiers=1)
    page.mouse("mousePressed", ax, ay, buttons=1, modifiers=1)
    for i in range(1, 9):
        page.mouse("mouseMoved", ax + 6 * i, ay + 3 * i, buttons=1, modifiers=1)
    page.mouse("mouseReleased", ax + 48, ay + 24, modifiers=1)
    time.sleep(0.3)
    l = _layer(page, "k_orta")
    assert l["anchor_x"] > 0.5 and l["anchor_y"] > 0.5, l
    nw1 = page.eval("import('/web/js/preview.js').then(m=>m.debugHandles('k_orta'))", await_promise=True)["nw"]
    assert abs(nw1[0] - nw0[0]) < 1.5 and abs(nw1[1] - nw0[1]) < 1.5, (nw0, nw1)
    page.eval("__kurgu.change(P=>{const l=P.layers.find(x=>x.id==='k_orta');delete l.anchor_x;delete l.anchor_y;l.x=%s;l.y=%s})" % (960, 540))


def test_kf_animate_menu_bakes_a_preset(page, server):
    _at(page, "k_orta", 3.0)
    page.click_sel(".kf-animate")
    page.wait_for("document.querySelector('.kf-menu')")
    assert page.eval("document.querySelectorAll('.kf-menu-group').length") == 4
    page.click_sel(".kf-menu-item[data-preset='in.fade-up']")
    page.wait_for("__kurgu.S.project.layers.find(l=>l.id==='k_orta').keys && __kurgu.S.project.layers.find(l=>l.id==='k_orta').keys.opacity")
    l = _layer(page, "k_orta")
    assert l["keys"]["opacity"][0]["v"] == 0 and "y" in l["keys"]
    assert not page.eval("document.querySelector('.kf-menu')")


# ------------------------------------------------------------------------------------------ timeline groups (colour, header chip, override)
def test_groups_have_header_chip_count_and_a_colour_rule(page):
    page.eval("__kurgu.S.selection=[];__kurgu.emit('selection')")
    time.sleep(0.3)
    heads = page.eval("[...document.querySelectorAll('.row.group')].map(h=>({name:h.querySelector('.name').textContent,chip:!!h.querySelector('.grp-chip .ic'),count:h.querySelector('.grp-count').textContent,grp:h.style.getPropertyValue('--grp')}))")
    assert len(heads) >= 2 and all(h["chip"] and h["count"].isdigit() and h["grp"].startswith("var(--grp-") for h in heads), heads
    assert len({h["grp"] for h in heads}) == len(heads)                       # distinct colours per group
    # rows inherit the group colour and carry the rule / band classes
    assert page.eval("document.querySelectorAll('.row.in-group:not(.group)').length") >= 8
    assert page.eval("getComputedStyle(document.querySelector('.row.in-group:not(.group) .left')).boxShadow.includes('inset')")


def test_group_colour_override_is_one_undo_step_and_saved(page, server):
    n = page.eval("__kurgu.S.undo.length")
    page.eval("document.querySelector('.row.group .left').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:400,clientY:600}))")
    page.wait_for("document.querySelector('.grp-pop')")
    assert page.eval("document.querySelectorAll('.grp-sw').length") == 8
    name = page.eval("document.querySelector('.row.group .name').textContent")
    page.click_sel(".grp-sw[data-color='#c47a5c']")
    page.wait_for("__kurgu.S.project.groups")
    assert page.eval("__kurgu.S.project.groups[%s].color" % json.dumps(name)) == "#c47a5c"
    assert page.eval("__kurgu.S.undo.length") - n == 1
    assert page.eval("document.querySelector('.row.group').style.getPropertyValue('--grp')") == "#c47a5c"
    t0 = time.time()
    while time.time() - t0 < 8 and "groups" not in server.project()["project"]:
        time.sleep(0.2)
    assert server.project()["project"]["groups"][name]["color"] == "#c47a5c"
    page.click_sel("#b-undo")
    page.wait_for("!__kurgu.S.project.groups")
    assert page.eval("document.querySelector('.row.group').style.getPropertyValue('--grp')").startswith("var(--grp-")
# ================================================================================== editor UX (split, ripple, clipboard, J/K/L, empty state, import, merge, platform keys)
MAC = sys.platform == "darwin"
CMD = 4 if MAC else 2   # CDP modifier bits: alt 1, ctrl 2, meta 4, shift 8


@pytest.fixture(scope="module")
def ux(chrome):
    """A private server + page so these tests may edit the project freely."""
    s = cdp.Server()
    p = chrome.new_page()
    p.open(s.url("/"), lang="en")
    yield p, s
    p.close()
    s.close()


def _layers(p):
    return p.eval("__kurgu.S.project.layers.map(l=>({id:l.id,name:l.name,start:l.start,end:l.end,group:l.group,type:l.type}))")


def _undo_len(p):
    return p.eval("__kurgu.S.undo.length")


def test_shortcut_hints_follow_the_platform(chrome, server):
    """Windows/Linux see Ctrl+Z / Ctrl+Shift+Z in tooltips and Ctrl (not Win/⌘) drives the shortcuts."""
    p = chrome.new_page()
    try:
        p.send("Emulation.setUserAgentOverride", userAgent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0", platform="Windows")
        p.open(server.url("/"), lang="en")
        assert p.eval("navigator.platform") in ("Windows", "Win32")
        titles = p.eval("[...document.querySelectorAll('[title]')].map(e=>e.title).join('|')")
        assert "Undo (Ctrl+Z)" in titles and "Redo (Ctrl+Shift+Z)" in titles and "Split the selected layer at the playhead (Ctrl+K)" in titles
        assert "⌘" not in titles and "{mod}" not in titles and "{shift}" not in titles
        # Ctrl+Z / Ctrl+Y undo & redo; the Meta key (Windows key) does nothing
        p.eval("__kurgu.change(P=>{P.name='zz-test'})")
        p.key("z", modifiers=4)
        assert p.eval("__kurgu.S.project.name") == "zz-test"
        p.key("z", modifiers=2)
        p.wait_for("__kurgu.S.project.name!=='zz-test'")
        p.key("y", modifiers=2)
        p.wait_for("__kurgu.S.project.name==='zz-test'")
        p.key("z", modifiers=2)
        p.wait_for("__kurgu.S.project.name!=='zz-test'")
        p.key("z", modifiers=2 | 8)                       # Ctrl+Shift+Z = redo
        p.wait_for("__kurgu.S.project.name==='zz-test'")
        assert _bench_errors(p) == []
    finally:
        p.close()


def test_shortcut_hints_on_mac_use_symbols(chrome, server):
    p = chrome.new_page()
    try:
        p.send("Emulation.setUserAgentOverride", userAgent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/130.0", platform="MacIntel")
        p.open(server.url("/"), lang="tr")
        titles = p.eval("[...document.querySelectorAll('[title]')].map(e=>e.title).join('|')")
        assert "Geri al (⌘Z)" in titles and "Yinele (⌘⇧Z)" in titles and "Ctrl+" not in titles
    finally:
        p.close()


def test_split_button_and_shortcut_are_one_undo_step_with_a_summary(ux):
    p, s = ux
    _sel(p, ["k_a"])
    p.eval("__kurgu.S.t=3.0;__kurgu.emit('time')")
    time.sleep(0.2)
    assert p.eval("!document.getElementById('b-split').disabled")
    n0, u0 = len(_layers(p)), _undo_len(p)
    p.click_sel("#b-split")
    p.wait_for("__kurgu.S.project.layers.length===%d" % (n0 + 1))
    assert _undo_len(p) == u0 + 1
    first = next(l for l in _layers(p) if l["id"] == "k_a")
    assert first["end"] == pytest.approx(3.0, abs=1e-3)
    p.wait_for("__kurgu.S.saveState==='saved'")
    cf = next(f for f in (os.path.join(s.dir, "changes.md"), os.path.join(s.dir, ".kurgu", "changes.md")) if os.path.exists(f))
    changes = open(cf, encoding="utf8").read()
    assert "Layer added" in changes
    # with the playhead outside the selected layer the button is disabled and the key explains why
    p.eval("__kurgu.S.selection=['k_a'];__kurgu.S.t=11;__kurgu.emit('selection');__kurgu.emit('time')")
    p.wait_for("document.getElementById('b-split').disabled")
    p.key("k", modifiers=CMD)
    p.wait_for("[...document.querySelectorAll('.toast')].some(t=>/playhead/i.test(t.textContent))")
    _undo_all(p, 1)
    p.wait_for("__kurgu.S.project.layers.length===%d" % n0)


def test_ripple_delete_closes_the_gap_in_the_group(ux):
    p, s = ux
    ids = ["r_1", "r_2", "r_3"]
    p.eval("""__kurgu.change(P=>{ P.layers.unshift(
      {id:'r_1',type:'color',name:'R1',group:'RippleG',start:20,end:22,color:'#111111',x:960,y:540,scale:1,opacity:1,effects:[]},
      {id:'r_2',type:'color',name:'R2',group:'RippleG',start:22,end:25,color:'#222222',x:960,y:540,scale:1,opacity:1,effects:[]},
      {id:'r_3',type:'color',name:'R3',group:'RippleG',start:26,end:28,color:'#333333',x:960,y:540,scale:1,opacity:1,effects:[]}); P.duration=Math.max(P.duration,28); })""")
    _sel(p, ["r_2"])
    u0 = _undo_len(p)
    p.key("Delete", modifiers=8)
    p.wait_for("!__kurgu.S.project.layers.some(l=>l.id==='r_2')")
    by = {l["id"]: l for l in _layers(p)}
    assert by["r_1"]["start"] == 20 and by["r_1"]["end"] == 22          # before the cut: untouched
    assert by["r_3"]["start"] == pytest.approx(23, abs=1e-3) and by["r_3"]["end"] == pytest.approx(25, abs=1e-3)   # 3 s gap closed
    assert _undo_len(p) == u0 + 1
    p.wait_for("[...document.querySelectorAll('.toast')].some(t=>/closed the gap/i.test(t.textContent))")
    # plain Delete leaves a gap
    _sel(p, ["r_1"])
    p.key("Delete")
    p.wait_for("!__kurgu.S.project.layers.some(l=>l.id==='r_1')")
    assert {l["id"]: l for l in _layers(p)}["r_3"]["start"] == pytest.approx(23, abs=1e-3)
    _undo_all(p, 3)
    p.wait_for("!__kurgu.S.project.layers.some(l=>/^r_/.test(l.id))")


def test_copy_cut_paste_layers_through_the_clipboard(ux):
    p, s = ux
    # a real key press (no clipboard event in this headless setup) takes the async-clipboard fallback and still confirms
    _sel(p, ["k_buyuk"])
    p.eval("document.querySelectorAll('.toast').forEach(t=>t.remove())")
    p.key("c", modifiers=CMD)
    p.wait_for("[...document.querySelectorAll('.toast')].some(t=>/Copied 1 layer/.test(t.textContent))")
    _sel(p, ["k_buyuk", "k_orta"])
    p.eval("""window.__dt = new DataTransfer(); document.body.dispatchEvent(new ClipboardEvent('copy', {clipboardData: window.__dt, bubbles: true, cancelable: true}));""")
    payload = json.loads(p.eval("window.__dt.getData('text/plain')"))
    assert payload["kurgu"] == "kurgu-layers" and len(payload["layers"]) == 2
    n0, u0 = len(_layers(p)), _undo_len(p)
    p.eval("__kurgu.S.t=7.5;__kurgu.emit('time')")
    p.eval("document.body.dispatchEvent(new ClipboardEvent('paste', {clipboardData: window.__dt, bubbles: true, cancelable: true}))")
    p.wait_for("__kurgu.S.project.layers.length===%d" % (n0 + 2))
    assert _undo_len(p) == u0 + 1
    src = {l["id"]: l for l in payload["layers"]}
    fresh = p.eval("__kurgu.S.selection.map(id=>__kurgu.S.project.layers.find(l=>l.id===id)).map(l=>({id:l.id,start:l.start,end:l.end}))")
    assert len(fresh) == 2 and all(f["id"] not in src for f in fresh)
    t0 = min(l["start"] for l in payload["layers"])
    for f, o in zip(sorted(fresh, key=lambda f: f["start"]), sorted(payload["layers"], key=lambda l: l["start"])):
        assert f["start"] - 7.5 == pytest.approx(o["start"] - t0, abs=2e-3)        # relative timing kept, earliest at the playhead
        assert f["end"] - f["start"] == pytest.approx(o["end"] - o["start"], abs=2e-3)
    # cut removes and puts the payload on the clipboard; a foreign clipboard text pastes nothing
    n1 = len(_layers(p))
    p.eval("""window.__dt2 = new DataTransfer(); document.body.dispatchEvent(new ClipboardEvent('cut', {clipboardData: window.__dt2, bubbles: true, cancelable: true}));""")
    p.wait_for("__kurgu.S.project.layers.length===%d" % (n1 - 2))
    assert json.loads(p.eval("window.__dt2.getData('text/plain')"))["kurgu"] == "kurgu-layers"
    p.eval("""(()=>{const dt=new DataTransfer(); dt.setData('text/plain','just some text'); document.body.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));})()""")
    time.sleep(0.3)
    assert len(_layers(p)) == n1 - 2
    p.eval("document.body.dispatchEvent(new ClipboardEvent('paste', {clipboardData: window.__dt2, bubbles: true, cancelable: true}))")
    p.wait_for("__kurgu.S.project.layers.length===%d" % n1)
    _undo_all(p, 3)
    p.wait_for("__kurgu.S.project.layers.length===%d" % n0)


def test_jkl_shuttle(ux):
    p, s = ux
    p.eval("__kurgu.S.selection=[];__kurgu.emit('selection');__kurgu.S.t=6;__kurgu.emit('time')")
    p.key("l")
    p.wait_for("__kurgu.S.playing")
    assert p.eval("__kurgu.S.shuttle") == 0
    p.key("l")
    p.wait_for("__kurgu.S.shuttle===2")
    assert p.eval("!document.getElementById('tc-rate').hidden && document.getElementById('tc-rate').textContent") == "2×"
    p.key("l")
    p.wait_for("__kurgu.S.shuttle===4")
    p.key("k")
    p.wait_for("!__kurgu.S.playing && __kurgu.S.shuttle===0")
    assert p.eval("document.getElementById('tc-rate').hidden")
    t0 = p.eval("__kurgu.S.t")
    p.key("j")
    p.wait_for("__kurgu.S.shuttle===-1")
    time.sleep(0.5)
    assert p.eval("__kurgu.S.t") < t0 - 0.2 and not p.eval("__kurgu.S.playing")
    p.key("j")
    p.wait_for("__kurgu.S.shuttle===-2")
    p.key(" ")
    p.wait_for("__kurgu.S.shuttle===0 && !__kurgu.S.playing")
    assert _bench_errors(p) == []


def test_empty_project_shows_the_start_panel(chrome):
    s = cdp.Server(prepare=lambda pj: pj.update(layers=[], notes=[]))
    p = chrome.new_page()
    try:
        p.open(s.url("/"), lang="en", ready="window.__kurgu && __kurgu.S.project")
        p.wait_for("!document.getElementById('empty-state').hidden")
        txt = p.eval("document.getElementById('empty-state').textContent")
        assert "Drop video, images, music or fonts" in txt and "Or ask your coding agent:" in txt and "1920×1080" in txt
        assert p.eval("document.querySelectorAll('#empty-state .es-prompt').length") == 3
        assert p.eval("!!document.querySelector('#empty-state .es-import')")
        p.click_sel("#empty-state .es-prompt")
        p.wait_for("[...document.querySelectorAll('.toast')].some(t=>/opied|copy/i.test(t.textContent))")
        # the panel is in the viewer's rectangle and disappears with the first layer
        assert p.eval("(()=>{const a=document.getElementById('empty-state').getBoundingClientRect(),b=document.getElementById('stage').getBoundingClientRect();return a.width>200&&a.left>=b.left-1&&a.right<=b.right+1})()")
        p.eval("__kurgu.change(P=>{P.layers.push({id:'c1',type:'color',name:'C',start:0,end:2,color:'#ff0000',x:960,y:540,scale:1,opacity:1,effects:[]})})")
        p.wait_for("document.getElementById('empty-state').hidden")
        _undo_all(p, 1)
        p.wait_for("!document.getElementById('empty-state').hidden")
        p.eval("__kurgu.setLang('tr')")
        p.wait_for("/ya da kodlama ajanına|Ya da kodlama/i.test(document.getElementById('empty-state').textContent)")
        assert _bench_errors(p) == []
    finally:
        p.close()
        s.close()


WAV_JS = """(n)=>{const sr=8000,len=Math.round(sr*n),b=new ArrayBuffer(44+len*2),v=new DataView(b);const w=(o,s)=>{for(let i=0;i<s.length;i++)v.setUint8(o+i,s.charCodeAt(i));};
w(0,'RIFF');v.setUint32(4,36+len*2,true);w(8,'WAVEfmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,sr,true);v.setUint32(28,sr*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);w(36,'data');v.setUint32(40,len*2,true);
for(let i=0;i<len;i++)v.setInt16(44+i*2,Math.round(Math.sin(i/9)*3000),true);return new File([b],'jingle.wav',{type:'audio/wav'});}"""


def test_import_toast_and_frame_grid_start(ux):
    p, s = ux
    p.eval("__kurgu.S.t=3.180834621329212;__kurgu.emit('time')")
    p.eval("(async()=>{const m=await import('/web/js/import.js');await m.importFiles([(%s)(1.0)]);})()" % WAV_JS, await_promise=True)
    p.wait_for("__kurgu.S.project.layers.some(l=>l.src==='media/jingle.wav')")
    l = p.eval("__kurgu.S.project.layers.find(l=>l.src==='media/jingle.wav')")
    assert l["start"] == pytest.approx(round(3.180834621329212 * 30) / 30, abs=1e-4) and len(str(l["start"])) <= 7    # 3.1667, not 3.180834621329212
    assert l["end"] - l["start"] == pytest.approx(1.0, abs=0.04)
    toast = p.eval("[...document.querySelectorAll('.toast')].map(t=>t.textContent).join('|')")
    assert "audio" in toast and "jingle" in toast and "3.17 s" in toast
    # several files at once -> one summary toast
    p.eval("(async()=>{const m=await import('/web/js/import.js');const f=(%s)(0.5);const g=new File([f],'jingle2.wav',{type:'audio/wav'});const h=new File([f],'jingle3.wav',{type:'audio/wav'});await m.importFiles([g,h]);})()" % WAV_JS, await_promise=True)
    p.wait_for("[...document.querySelectorAll('.toast')].some(t=>/Added 2 items: 2 × audio/.test(t.textContent))")
    _undo_all(p, 3)


def test_media_panel_shows_proxy_preparation(ux):
    p, s = ux
    p.eval("""(()=>{ const real = window.fetch.bind(window); window.__fake = 0;
      window.fetch = (u, o) => { if (String(u).includes('/api/media-info') && String(u).includes('a.mp4')) { window.__fake++; return Promise.resolve(new Response(JSON.stringify({duration:6,width:3840,height:2160,has_audio:true,proxy:'/media?path=x',proxy_ready:false,proxy_progress:0.4}),{status:200,headers:{'Content-Type':'application/json'}})); } return real(u, o); };
      __kurgu.S.media.delete('media/a.mp4'); __kurgu.emit('files'); })()""")
    p.wait_for("document.querySelector('.mp-item[data-path=\"media/a.mp4\"] .mp-prep')", 10)
    assert "40 %" in p.eval("document.querySelector('.mp-item[data-path=\"media/a.mp4\"] .mp-prep').textContent")
    assert p.eval("document.querySelector('.mp-item[data-path=\"media/a.mp4\"] .mp-prep i').style.width") == "40%"
    # ready -> the indicator goes away
    p.eval("window.__ready = true")
    p.eval("""(()=>{ const prev = window.fetch; window.fetch = (u, o) => (String(u).includes('/api/media-info') && String(u).includes('a.mp4') && window.__ready)
      ? Promise.resolve(new Response(JSON.stringify({duration:6,width:3840,height:2160,has_audio:true,proxy:'/media?path=x',proxy_ready:true}),{status:200,headers:{'Content-Type':'application/json'}})) : prev(u, o); })()""")
    p.wait_for("!document.querySelector('.mp-item[data-path=\"media/a.mp4\"] .mp-prep')", 8)


def _put(s, project, version, summary="test"):
    import urllib.request
    req = urllib.request.Request(s.url("/api/project"), data=json.dumps({"project": project, "summary": [summary], "base_version": version}).encode(), method="PUT", headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def test_save_conflict_merges_instead_of_dropping_the_edit(chrome):
    s = cdp.Server()
    p = chrome.new_page()
    try:
        p.open(s.url("/"), lang="en")
        cur = s.project()
        agent = cur["project"]
        for l in agent["layers"]:
            if l["id"] == "k_sol":
                l["opacity"] = 0.25      # the agent's edit: another field of the same layer
            if l["id"] == "k_orta":
                l["y"] = 111             # a field we both change
        _put(s, agent, cur["version"], "agent")
        # our unsaved edit (made on the old base): k_sol.x, k_orta.y (clash), k_buyuk.text
        p.eval("__kurgu.change(P=>{const g=id=>P.layers.find(l=>l.id===id); g('k_sol').x=123; g('k_orta').y=222; g('k_buyuk').text='mine';})")
        p.wait_for("[...document.querySelectorAll('.toast')].some(t=>/agent edited the project/i.test(t.textContent))", 40)
        p.wait_for("__kurgu.S.saveState==='saved'", 30)
        disk = {l["id"]: l for l in s.project()["project"]["layers"]}
        assert disk["k_sol"]["x"] == 123 and disk["k_sol"]["opacity"] == 0.25          # both edits of different fields survived
        assert disk["k_buyuk"]["text"] == "mine"
        assert disk["k_orta"]["y"] == 111                                             # same field: the agent's value kept
        toast = p.eval("[...document.querySelectorAll('.toast')].map(t=>t.textContent).join('|')")
        assert "Kept from you" in toast and "Kept from the agent" in toast and "y" in toast
        live = {l["id"]: l for l in p.eval("__kurgu.S.project.layers")}
        assert live["k_sol"]["x"] == 123 and live["k_orta"]["y"] == 111
        assert [e for e in _bench_errors(p) if "409" not in e] == []      # the 409 itself is the expected trigger
    finally:
        p.close()
        s.close()


# ================================================================================== track view (Premiere style rows)
from tracks_fixture import synthetic  # noqa: E402


@pytest.fixture(scope="module")
def tk(chrome):
    """Synthetic 40-layer project (Kartlar 4 / Altyazı 15 / Görüntü 18 with crossfades / Ses 3), private server, track view (the default)."""
    s = cdp.Server(prepare=synthetic)
    p = chrome.new_page()
    p.open(s.url("/"), lang="en", view=None)          # view=None: nothing stored, so this is the real default
    p.eval("document.documentElement.style.setProperty('--lp-h','640px')")
    time.sleep(0.3)
    yield p, s
    p.close()
    s.close()


def _clip_rect(p, lid):
    return p.eval("(()=>{const e=document.querySelector('.clip[data-id=%s]');if(!e)return null;e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()" % lid)


def _track_of(p, lid):
    return p.eval("(()=>{const e=document.querySelector('.clip[data-id=%s]');const r=e&&e.closest('.row.track');return r?[+r.dataset.si,+r.dataset.k]:null})()" % lid)


def _tracks_state(p):
    return p.eval("[...document.querySelectorAll('.lp-list > .row')].filter(r=>r.classList.contains('group')||r.classList.contains('track')).map(r=>r.classList.contains('group')?r.querySelector('.name').textContent:r.querySelector('.trk-tag').textContent+':'+r.querySelectorAll('.clip').length)")


def test_tracks_is_the_default_view_and_packs_rows(tk):
    p, _ = tk
    assert p.eval("document.querySelector('.lp-seg-btn.on').dataset.view") == "tracks"
    assert p.eval("document.querySelectorAll('.clip').length") == 40
    st = _tracks_state(p)
    assert st == ["Kartlar", "T1:4", "Altyazı", "C2:14", "C1:1", "Görüntü", "V2:9", "V1:9", "Ses", "A2:1", "A1:2"], st   # upper rows carry the higher number, like Premiere
    assert p.eval("document.querySelectorAll('.row.track').length") == 7          # was 40 rows in the layer view
    assert p.eval("getComputedStyle(document.querySelector('.row.in-group.track .left')).boxShadow.includes('inset')")
    assert p.eval("[...document.querySelectorAll('.row.group')].every(h=>h.querySelector('.grp-chip .ic')&&h.querySelector('.grp-count').textContent)")


def test_view_switch_is_persisted_and_pure(tk, chrome):
    p, s = tk
    before = json.dumps(s.project()["project"]["layers"])
    p.click_sel(".lp-seg-btn[data-view=layers]")
    p.wait_for("document.querySelectorAll('.row:not(.group):not(.lane)').length===40")
    assert p.eval("localStorage.getItem('kurgu.layerView')") == "layers"
    assert p.eval("__kurgu.S.undo.length") == 0 and p.eval("__kurgu.S.saveState") != "dirty"       # pure view: no edit, nothing to save
    p.click_sel(".lp-seg-btn[data-view=tracks]")
    p.wait_for("document.querySelectorAll('.row.track').length===7")
    assert p.eval("localStorage.getItem('kurgu.layerView')") == "tracks"
    assert json.dumps(s.project()["project"]["layers"]) == before


def _mouse_drag(p, x0, y0, moves, up=True):
    p.mouse("mouseMoved", x0, y0)
    p.mouse("mousePressed", x0, y0, buttons=1)
    for x, y in moves:
        p.mouse("mouseMoved", x, y, buttons=1)
        time.sleep(0.03)
    if up:
        p.mouse("mouseReleased", moves[-1][0], moves[-1][1])


def test_horizontal_drag_moves_in_time_rows_stay_put_and_repack_on_drop(tk):
    p, _ = tk
    p.eval("__kurgu.S.snap=false")
    x, y, w, h = _clip_rect(p, "alt0")
    start0 = p.eval("__kurgu.S.project.layers.find(l=>l.id==='alt0').start")
    n = p.eval("__kurgu.S.undo.length")
    tr0 = _track_of(p, "alt0")
    # drag alt0 far to the right over alt1..alt3: it overlaps them, but the rows must not jump while the button is down
    pps = p.eval("(()=>{const a=document.querySelector('.clip[data-id=alt0]'),b=document.querySelector('.clip[data-id=alt1]');return (b.getBoundingClientRect().left-a.getBoundingClientRect().left)/(%s)})()" % 4.6)
    _mouse_drag(p, x + w / 2, y + h / 2, [(x + w / 2 + 30, y + h / 2), (x + w / 2 + 120, y + h / 2)], up=False)
    assert _track_of(p, "alt0") == tr0 and _track_of(p, "alt1") == _track_of(p, "alt1")
    p.mouse("mouseReleased", x + w / 2 + 120, y + h / 2)
    time.sleep(0.3)
    start1 = p.eval("__kurgu.S.project.layers.find(l=>l.id==='alt0').start")
    assert abs(start1 - (start0 + 120 / pps)) < 0.2, (start0, start1)
    assert p.eval("__kurgu.S.undo.length") - n == 1
    # after the drop it overlaps alt1, so the pack gives one of them the other row
    assert _track_of(p, "alt0")[1] != _track_of(p, "alt1")[1]
    p.click_sel("#b-undo")
    p.wait_for("__kurgu.S.project.layers.find(l=>l.id==='alt0').start===%s" % start0)
    assert _track_of(p, "alt0") == tr0


def test_click_selects_and_hover_shows_name_and_time(tk):
    p, _ = tk
    x, y, w, h = _clip_rect(p, "alt3")
    p.click(x + w / 2, y + h / 2)
    p.wait_for("__kurgu.S.selection.length===1 && __kurgu.S.selection[0]==='alt3'")
    assert p.eval("document.querySelector('.clip[data-id=alt3]').classList.contains('selected')")
    ttl = p.eval("document.querySelector('.clip[data-id=alt3]').title")
    assert "anlatacağım" in ttl and "–" in ttl and "s" in ttl


def test_vertical_drag_reorders_z_and_is_one_undo_step(tk):
    p, _ = tk
    p.eval("__kurgu.S.snap=false")
    order = lambda: p.eval("__kurgu.S.project.layers.map(l=>l.id)")                                   # noqa: E731
    o0, n = order(), p.eval("__kurgu.S.undo.length")
    assert _track_of(p, "gor1")[1] == 1 and _track_of(p, "gor0")[1] == 0
    x, y, w, h = _clip_rect(p, "gor1")
    top = p.eval("(()=>{const r=document.querySelector('.row.track[data-si=\"2\"][data-k=\"0\"]').getBoundingClientRect();return [r.top,r.height]})()")
    tx = x + w / 2
    _mouse_drag(p, tx, y + h / 2, [(tx, y + h / 2 - 12), (tx, top[0] + top[1] * 0.1)], up=False)
    assert p.eval("document.querySelectorAll('.row.track.drop-on').length") == 1                    # the row it will land on is highlighted while dragging
    assert p.eval("document.querySelector('.row.track.drop-on').dataset.k") == "0"
    p.mouse("mouseReleased", tx, top[0] + top[1] * 0.1)
    time.sleep(0.4)
    o1 = order()
    assert o1.index("gor1") < o1.index("gor0") and sorted(o1) == sorted(o0)                            # z-order changed: now in front of its neighbours
    assert p.eval("__kurgu.S.undo.length") - n == 1
    assert _track_of(p, "gor1") == [2, 0]                                                              # ...and it sits on the upper row, alone
    p.click_sel("#b-undo")
    p.wait_for("__kurgu.S.project.layers.map(l=>l.id).join()===%s" % json.dumps(",".join(o0)))
    assert _track_of(p, "gor1") == [2, 1]


def test_drag_to_another_groups_track_changes_the_group(tk):
    p, _ = tk
    n = p.eval("__kurgu.S.undo.length")
    p.eval("document.querySelector('.lp-body').scrollTop=0")
    x, y, w, h = _clip_rect(p, "alt10")
    dst = p.eval("(()=>{const r=document.querySelector('.row.track[data-si=\"2\"][data-k=\"0\"]').getBoundingClientRect();return r.top+r.height/2})()")
    tx = x + w / 2
    _mouse_drag(p, tx, y + h / 2, [(tx, y + h / 2 + 20), (tx, dst)])
    time.sleep(0.4)
    assert p.eval("__kurgu.S.project.layers.find(l=>l.id==='alt10').group") == "Görüntü"
    assert p.eval("__kurgu.S.undo.length") - n == 1
    p.click_sel("#b-undo")
    p.wait_for("__kurgu.S.project.layers.find(l=>l.id==='alt10').group==='Altyazı'")


def test_media_drop_on_a_track_uses_x_as_time_and_the_row_as_group(tk):
    p, _ = tk
    n0 = p.eval("__kurgu.S.project.layers.length")
    p.eval("document.querySelector('.lp-body').scrollTop=0")
    if not p.eval("!!document.querySelector('#media-panel .mp-item')"):
        p.click_sel("#b-panel")
    p.wait_for("document.querySelector('#media-panel .mp-item')")
    src = p.center("#media-panel .mp-item")
    dst = p.eval("(()=>{const r=document.querySelector('.row.track[data-si=\"3\"][data-k=\"0\"] .strip').getBoundingClientRect();return [r.left+r.width*0.5, r.top+r.height/2]})()")
    p.drag(src[0], src[1], dst[0], dst[1], steps=12)
    p.wait_for("__kurgu.S.project.layers.length===%d" % (n0 + 2))
    lay = p.eval("__kurgu.S.project.layers.filter(l=>__kurgu.S.selection.includes(l.id))[0]")
    assert lay["group"] == "Ses", lay["group"]
    assert 30 < lay["start"] < 45, lay["start"]                                                        # 50 % of a 75 s strip
    _undo_all(page=p)


def test_double_click_a_clip_with_keys_opens_its_lanes_under_the_track(tk):
    p, _ = tk
    p.eval("__kurgu.change(P=>{const l=P.layers.find(x=>x.id==='card1');l.keys={opacity:[{t:0,v:0},{t:1,v:1}],rotation:[{t:0,v:0},{t:2,v:10}]}})")
    p.wait_for("document.querySelectorAll('.clip[data-id=card1] .clip-keys i').length===3")               # key-time ticks on the clip (0, 1, 2 s)
    assert p.eval("document.querySelectorAll('.row.lane').length") == 0
    x, y, w, h = _clip_rect(p, "card1")
    p.click(x + w / 2, y + h / 2, clicks=2)
    p.wait_for("document.querySelectorAll('.row.lane[data-id=card1]').length===2")
    assert p.eval("(()=>{const t=document.querySelector('.clip[data-id=card1]').closest('.row.track');return t.nextElementSibling.classList.contains('lane')})()")   # right under the track
    p.click(x + w / 2, y + h / 2, clicks=2)
    p.wait_for("document.querySelectorAll('.row.lane').length===0")
    _undo_all(page=p)


def test_long_project_stays_smooth(tk):
    p, _ = tk
    r = p.eval("""(()=>{const S=__kurgu.S; const ids=S.project.layers.map(l=>l.id);
      let t0=performance.now(); for(let i=0;i<120;i++){ __kurgu.S.t=i*0.6; __kurgu.emit('time'); } const scrub=(performance.now()-t0)/120;
      t0=performance.now(); for(let i=0;i<40;i++){ S.selection=[ids[i%ids.length]]; __kurgu.emit('selection'); } const sel=(performance.now()-t0)/40;
      t0=performance.now(); for(let i=0;i<20;i++){ __kurgu.change(P=>{P.layers[6].start+= (i%2?-0.01:0.01); P.layers[6].end+=(i%2?-0.01:0.01);},{key:'perf'}); } const edit=(performance.now()-t0)/20;
      return {scrub, sel, edit};})()""")
    print('perf ms', r)
    assert r["scrub"] < 4 and r["sel"] < 25 and r["edit"] < 40, r


def test_tracks_i18n_keys_match_in_both_languages():
    en = json.load(open(os.path.join(os.path.dirname(cdp.__file__), "..", "web", "i18n", "en.json")))
    tr = json.load(open(os.path.join(os.path.dirname(cdp.__file__), "..", "web", "i18n", "tr.json")))
    assert set(en) == set(tr)
    for k in ("layers.viewTracks", "layers.viewLayers", "tracks.track", "tracks.newTrack"):
        assert k in en and en[k] != tr[k]


# ------------------------------------------------------------------------------------------ linked clips (video + its sound, SPEC 1.1a)
def _with_pair(p):
    base = {"hidden": False, "locked": False, "fade_in": 0, "fade_out": 0, "start": 2, "end": 6, "src": "media/a.mp4", "src_in": 0, "link": "lk_t"}
    vid = dict(base, id="pv", type="video", name="Pair video", group="Picture", muted=True, volume_db=0, envelope=[], x=960, y=540, scale=1, opacity=1, effects=[], fit="cover")
    aud = dict(base, id="pa", type="audio", name="Pair audio", group="Sound", muted=False, volume_db=-3, envelope=[])
    p["layers"].insert(3, vid)
    p["layers"].append(aud)
    return p


@pytest.fixture(scope="module")
def lk(chrome):
    s = cdp.Server(prepare=_with_pair)
    p = chrome.new_page()
    p.open(s.url("/"), lang="en", view=None)
    p.eval("document.documentElement.style.setProperty('--lp-h','520px')")
    time.sleep(0.3)
    yield p, s
    p.close()
    s.close()


def _lay(p, lid):
    return p.eval("__kurgu.S.project.layers.find(l=>l.id===%s)" % json.dumps(lid))


def _reset_pair(p):
    p.eval("__kurgu.change(P=>{for(const [id,ss] of [['pv',2],['pa',2]]){const l=P.layers.find(x=>x.id===id);if(l){l.start=ss;l.end=ss+4;l.src_in=0;l.link='lk_t'}}})")
    p.eval("__kurgu.S.selection=[];__kurgu.emit('selection')")
    time.sleep(0.2)


def test_clicking_a_linked_clip_selects_its_partner_and_alt_selects_one(lk):
    p, _ = lk
    x, y, w, h = _clip_rect(p, "pv")
    p.click(x + w / 2, y + h / 2)
    p.wait_for("__kurgu.S.selection.length===2")
    assert sorted(p.eval("__kurgu.S.selection")) == ["pa", "pv"]
    assert p.eval("document.querySelectorAll('.clip.linked.link-hl').length") == 2
    assert p.eval("!!document.querySelector('.clip[data-id=pv] .lk-glyph:not([hidden])')")
    p.eval("__kurgu.select([])")
    p.click(x + w / 2, y + h / 2, modifiers=1)                       # Alt = this clip only
    p.wait_for("__kurgu.S.selection.length===1")
    assert p.eval("__kurgu.S.selection") == ["pv"]
    assert p.eval("document.querySelector('.clip[data-id=pa]').classList.contains('link-hl')")    # the partner is still highlighted
    p.eval("__kurgu.select([])")


def test_inspector_shows_one_clip_and_link_buttons_for_a_pair(lk):
    p, _ = lk
    x, y, w, h = _clip_rect(p, "pa")
    p.click(x + w / 2, y + h / 2)
    p.wait_for("__kurgu.S.selection.length===2")
    assert p.eval("__kurgu.S.selection[1]") == "pa"                      # the clicked clip is the primary one
    p.wait_for("[...document.querySelectorAll('.pbtn')].some(b=>b.textContent==='Unlink')")
    assert p.eval("[...document.querySelectorAll('.section-head span')].some(s=>s.textContent==='Audio')")     # its own audio section is reachable
    p.eval("__kurgu.select([])")


def test_dragging_a_linked_clip_moves_both_in_one_undo_step(lk):
    p, _ = lk
    _reset_pair(p)
    x, y, w, h = _clip_rect(p, "pv")
    n = p.eval("__kurgu.S.undo.length")
    p.drag(x + w / 2, y + h / 2, x + w / 2 + 90, y + h / 2, steps=8)
    p.wait_for("__kurgu.S.project.layers.find(l=>l.id==='pv').start>2.5")
    v, a = _lay(p, "pv"), _lay(p, "pa")
    assert abs(v["start"] - a["start"]) < 1e-6 and abs(v["end"] - a["end"]) < 1e-6, (v["start"], a["start"])
    assert p.eval("__kurgu.S.undo.length") == n + 1                        # ONE undo step
    assert p.eval("document.querySelectorAll('.lk-off:not([hidden])').length") == 0
    _undo_all(p)
    assert _lay(p, "pa")["start"] == 2 and _lay(p, "pv")["start"] == 2


def test_alt_drag_moves_one_clip_and_shows_the_offset_badge_then_resync(lk):
    p, _ = lk
    _reset_pair(p)
    x, y, w, h = _clip_rect(p, "pa")
    p.mouse("mouseMoved", x + w / 2, y + h / 2)
    p.mouse("mousePressed", x + w / 2, y + h / 2, buttons=1, modifiers=1)
    for i in range(1, 9):
        p.mouse("mouseMoved", x + w / 2 + i * 10, y + h / 2, buttons=1, modifiers=1)
    p.mouse("mouseReleased", x + w / 2 + 80, y + h / 2, modifiers=1)
    p.wait_for("__kurgu.S.project.layers.find(l=>l.id==='pa').start>2.3")
    assert _lay(p, "pv")["start"] == 2                                     # the partner did not move
    p.wait_for("document.querySelector('.clip[data-id=pa] .lk-off:not([hidden])')")
    txt = p.eval("document.querySelector('.clip[data-id=pa] .lk-off').textContent")
    assert txt.startswith("+") and txt.endswith(" s"), txt
    p.click_sel(".clip[data-id=pa] .lk-off")                               # the badge re-syncs
    p.wait_for("Math.abs(__kurgu.S.project.layers.find(l=>l.id==='pa').start-2)<1e-3")
    assert p.eval("document.querySelectorAll('.lk-off:not([hidden])').length") == 0
    _reset_pair(p)


def test_split_and_delete_act_on_both_halves_of_a_pair(lk):
    p, _ = lk
    _reset_pair(p)
    n0 = p.eval("__kurgu.S.project.layers.length")
    p.eval("__kurgu.select(['pv'])")
    assert sorted(p.eval("__kurgu.S.selection")) == ["pa", "pv"]
    p.eval("__kurgu.S.t=4;__kurgu.splitLayers(__kurgu.S.selection,4)")
    assert p.eval("__kurgu.S.project.layers.length") == n0 + 2
    halves = p.eval("__kurgu.S.project.layers.filter(l=>l.link).map(l=>[l.id,l.link,l.start,l.end])")
    firsts = [h for h in halves if h[3] == 4]
    seconds = [h for h in halves if h[2] == 4]
    assert len(firsts) == 2 and len(seconds) == 2 and firsts[0][1] == firsts[1][1] == "lk_t"
    assert seconds[0][1] == seconds[1][1] and seconds[0][1] != "lk_t"      # the second halves are a new pair
    p.eval("__kurgu.removeLayers(__kurgu.S.selection)")                    # the selection (second halves) goes together
    assert p.eval("__kurgu.S.project.layers.length") == n0
    _undo_all(p, 2)
    assert p.eval("__kurgu.S.project.layers.length") == n0
    _reset_pair(p)


def test_context_menu_unlink_then_link_again(lk):
    p, _ = lk
    _reset_pair(p)
    x, y, w, h = _clip_rect(p, "pv")
    p.mouse("mousePressed", x + w / 2, y + h / 2, button="right", buttons=2)
    p.mouse("mouseReleased", x + w / 2, y + h / 2, button="right")
    p.wait_for("!document.getElementById('menu').hidden")
    items = p.eval("[...document.querySelectorAll('#menu .menu-item')].map(b=>b.textContent)")
    assert "Unlink" in items and "Link" not in items, items
    p.eval("[...document.querySelectorAll('#menu .menu-item')].find(b=>b.textContent==='Unlink').click()")
    p.wait_for("!__kurgu.S.project.layers.find(l=>l.id==='pv').link")
    assert not _lay(p, "pa").get("link")
    p.eval("__kurgu.select([])")
    x, y, w, h = _clip_rect(p, "pv")
    p.click(x + w / 2, y + h / 2)                                          # independent now: selects only itself
    assert p.eval("__kurgu.S.selection") == ["pv"]
    p.eval("__kurgu.select(['pv','pa'])")
    assert p.eval("__kurgu.LK.available(['pv','pa']).link") is True
    p.eval("__kurgu.LK.linkLayers(['pv','pa'])")
    assert _lay(p, "pv")["link"] == _lay(p, "pa")["link"] != None
    _reset_pair(p)


def test_separate_audio_and_merge_back_are_inverse_single_undo_steps(lk):
    p, _ = lk
    _reset_pair(p)
    before = _lay(p, "k_a")
    n0, u0 = p.eval("__kurgu.S.project.layers.length"), p.eval("__kurgu.S.undo.length")
    p.eval("__kurgu.change(P=>{const l=P.layers.find(x=>x.id==='k_a');l.volume_db=-4;l.envelope=[[1,0],[2,-6]]})")
    src = _lay(p, "k_a")
    p.eval("__kurgu.S.undo.length")
    u1 = p.eval("__kurgu.S.undo.length")
    aid = p.eval("__kurgu.LK.separateAudio('k_a')")
    assert aid and p.eval("__kurgu.S.undo.length") == u1 + 1
    v, a = _lay(p, "k_a"), _lay(p, aid)
    assert v["muted"] is True and v["link"] == a["link"] and a["type"] == "audio" and a["group"] == "Sound"
    assert (a["src"], a["src_in"], a["start"], a["end"], a["volume_db"], a["envelope"]) == (src["src"], src["src_in"], src["start"], src["end"], -4, [[1, 0], [2, -6]])
    assert p.eval("__kurgu.S.project.layers.length") == n0 + 1
    u2 = p.eval("__kurgu.S.undo.length")
    assert p.eval("__kurgu.LK.mergeAudio(['k_a'])") == "k_a" and p.eval("__kurgu.S.undo.length") == u2 + 1
    assert _lay(p, "k_a") == src and p.eval("__kurgu.S.project.layers.length") == n0     # exactly the legacy single layer again
    _undo_all(p, 3)
    assert _lay(p, "k_a") == before and p.eval("__kurgu.S.project.layers.length") == n0
    _ = u0


def test_import_makes_a_pair_and_the_setting_turns_it_off(lk):
    p, _ = lk
    n0 = p.eval("__kurgu.S.project.layers.length")
    p.eval("document.querySelector('.lp-body').scrollTop=0")
    if not p.eval("!!document.querySelector('#media-panel .mp-item')"):
        p.click_sel("#b-panel")
    p.wait_for("document.querySelector('#media-panel .mp-item')")

    def drop():
        src = p.center("#media-panel .mp-item")
        dst = p.eval("(()=>{const r=document.querySelector('.lp-ruler-strip').getBoundingClientRect();const b=document.querySelector('.lp-body').getBoundingClientRect();return [r.left+r.width*0.3, b.top+b.height*0.6]})()")
        p.drag(src[0], src[1], dst[0], dst[1], steps=12)
    drop()
    p.wait_for("__kurgu.S.project.layers.length===%d" % (n0 + 2))
    new = p.eval("__kurgu.S.project.layers.filter(l=>l.src==='media/a.mp4'&&l.link&&l.id!=='pv'&&l.id!=='pa')")
    assert {l["type"] for l in new} == {"video", "audio"} and new[0]["link"] == new[1]["link"]
    v = next(l for l in new if l["type"] == "video"); a = next(l for l in new if l["type"] == "audio")
    assert v["muted"] is True and a["group"] == "Sound" and v["group"] == "Picture"
    assert (v["start"], v["end"], v["src_in"], v["src"]) == (a["start"], a["end"], a["src_in"], a["src"])
    _undo_all(p)
    p.wait_for("__kurgu.S.project.layers.length===%d" % n0)
    p.click_sel("#media-panel .mp-sep")                                    # setting off: old single-layer behaviour
    assert p.eval("localStorage.getItem('kurgu.importSeparateAudio')") == "0"
    drop()
    p.wait_for("__kurgu.S.project.layers.length===%d" % (n0 + 1))
    assert not p.eval("__kurgu.S.project.layers.some(l=>l.src==='media/a.mp4'&&l.link&&l.id!=='pv'&&l.id!=='pa')")
    _undo_all(p)
    p.click_sel("#media-panel .mp-sep")
    assert p.eval("localStorage.getItem('kurgu.importSeparateAudio')") == "1"


@pytest.mark.parametrize("lang,expect", [("en", ["Unlink", "Link", "Separate audio", "Merge audio back", "Out of sync"]), ("tr", ["Bağlantıyı kopar", "Bağla", "Sesi ayır", "Sesi birleştir", "Senkron dışı"])])
def test_link_wording_in_both_languages(lang, expect):
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "i18n", lang + ".json")
    tr = json.load(open(d, encoding="utf-8"))
    got = [tr["link.unlink"], tr["link.link"], tr["link.separate"], tr["link.merge"], tr["link.outOfSync"]]
    assert got == expect


def test_change_summary_is_readable_for_link_commands(lk):
    p, _ = lk
    _reset_pair(p)
    js = """(async()=>{const m=await import('/web/js/summary.js');const a=JSON.parse(JSON.stringify(__kurgu.S.project));
      __kurgu.LK.separateAudio('k_a');const b=JSON.parse(JSON.stringify(__kurgu.S.project));return m.makeSummary(a,b)})()"""
    lines = p.eval(js, await_promise=True)
    assert any("linked with" in l for l in lines) and any("muted" in l for l in lines) and any("Layer added" in l for l in lines), lines
    _undo_all(p)


def test_linked_clips_raise_no_console_errors(lk):
    p, _ = lk
    assert _bench_errors(p) == [], _bench_errors(p)
