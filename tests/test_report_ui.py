"""Headless UI: refusal panels (plain language, persistent, dismissible), and the menu -> Error report window -> Copy."""
import json
import os
import sys

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


def with_notes(p):
    p["notes"] = [{"id": "n_1", "t": 1.0, "text": "make the title bigger", "author": "user"}]


def new_server(monkeypatch, tmp_path, hook=False):
    monkeypatch.setenv("KURGU_CONFIG", str(tmp_path / "conf"))
    if hook:
        monkeypatch.setenv("KURGU_ON_SEND", json.dumps([sys.executable, "-c", "print('hi')"]))
    else:
        monkeypatch.delenv("KURGU_ON_SEND", raising=False)
    return cdp.Server(prepare=with_notes)


def panel(page):
    page.wait_for("!!document.getElementById('notice-panel')", 30)
    return page.eval("document.getElementById('notice-panel').textContent")


def test_symlinked_inbox_refuses_the_send_and_the_panel_says_why(chrome, monkeypatch, tmp_path):
    s = new_server(monkeypatch, tmp_path)
    try:
        os.makedirs(os.path.join(s.dir, ".kurgu"), exist_ok=True)
        os.symlink(tmp_path / "target", os.path.join(s.dir, ".kurgu", "inbox.jsonl"))
        p = chrome.new_page()
        p.open(s.url("/"), lang="en")
        text = panel(p)                                                                   # shown at load: /api/notes/info reports the problem
        assert "shortcut to somewhere else" in text and ".kurgu/inbox.jsonl" in text and "Remove the shortcut" in text
        p.click_sel("#notice-panel .idle-x")
        assert p.eval("!document.getElementById('notice-panel')")                         # dismissible
        p.click_sel("#b-send")
        assert "Remove the shortcut" in panel(p)                                          # the send itself is refused: a panel, never a success toast
        assert p.eval("!document.querySelector('.toast.ok')") and p.eval("!document.getElementById('idle-panel')")
        assert not (tmp_path / "target").exists()
        p.close()
    finally:
        s.close()


def test_symlinked_hook_log_panel_in_turkish(chrome, monkeypatch, tmp_path):
    s = new_server(monkeypatch, tmp_path, hook=True)
    try:
        os.makedirs(os.path.join(s.dir, ".kurgu"), exist_ok=True)
        os.symlink(tmp_path / "zshrc", os.path.join(s.dir, ".kurgu", "hook.log"))
        p = chrome.new_page()
        p.open(s.url("/"), lang="tr")
        assert "başka bir yere kısayol" in panel(p)
        p.eval("document.getElementById('notice-panel').remove()")
        p.click_sel("#b-send")
        p.wait_for("__kurgu.S.project.notes.every(n=>n.sent)", 60)                          # the notes ARE saved ...
        assert "Kısayolu kaldırıp gerçek bir klasör yap" in panel(p)                        # ... and the panel says the agent was not started, why
        assert p.eval("!document.querySelector('.toast.ok')")
        assert not (tmp_path / "zshrc").exists()
        p.close()
    finally:
        s.close()


def test_windows_special_character_message_names_the_character_and_the_path(chrome, monkeypatch, tmp_path):
    s = new_server(monkeypatch, tmp_path)
    try:
        for lang, needle in (("tr", "Windows'un özel karakterlerinden biri var (& %)"), ("en", "one of Windows' special characters (& %)")):
            p = chrome.new_page()
            p.open(s.url("/"), lang=lang)
            p.eval("import('/web/js/watch.js').then(m => m.showReason({code: 'shell_chars', params: {chars: '& %', path: 'C:\\\\films\\\\a & b%'}}))")
            text = panel(p)
            assert needle in text and "C:\\films\\a & b%" in text
            p.close()
    finally:
        s.close()


def test_menu_error_report_shows_the_text_and_copies_it(chrome, monkeypatch, tmp_path):
    s = new_server(monkeypatch, tmp_path)
    try:
        p = chrome.new_page()
        p.open(s.url("/"), lang="en")
        p.click_sel("#b-menu")
        p.wait_for("!![...document.querySelectorAll('#main-menu .mm-item')].find(b => b.textContent.includes('Error report'))")
        p.eval("[...document.querySelectorAll('#main-menu .mm-item')].find(b => b.textContent.includes('Error report')).click()")
        p.wait_for("!!document.querySelector('#report-modal .rp-text') && document.querySelector('#report-modal .rp-text').value.startsWith('Kurgu report')", 60)
        txt = p.eval("document.querySelector('#report-modal .rp-text').value")
        assert "Agent setup" in txt and "hook.log" in txt and os.path.expanduser("~") not in txt.replace("~", "")
        p.eval("window.__copied=[]; navigator.clipboard.writeText=t=>{window.__copied.push(t);return Promise.resolve()}")
        p.click_sel("#report-modal .rp-copy")
        p.wait_for("window.__copied.length===1")
        assert p.eval("window.__copied[0]") == txt                                           # what was shown is exactly what was copied
        p.click_sel("#report-modal .rp-close")
        assert p.eval("!document.getElementById('report-modal')")
        p.close()
    finally:
        s.close()


def test_report_window_agent_output_checkbox_is_off_by_default(chrome, monkeypatch, tmp_path):
    s = new_server(monkeypatch, tmp_path)
    try:
        os.makedirs(os.path.join(s.dir, ".kurgu"), exist_ok=True)
        open(os.path.join(s.dir, ".kurgu", "hook.log"), "w").write("[2026-01-01T00:00:00] run (send #1)\n| AGENT QUOTES YOUR NOTE\n  finished, exit code 0\n")
        p = chrome.new_page()
        p.open(s.url("/"), lang="en")
        p.click_sel("#b-menu")
        p.wait_for("!![...document.querySelectorAll('#main-menu .mm-item')].find(b => b.textContent.includes('Error report'))")
        p.eval("[...document.querySelectorAll('#main-menu .mm-item')].find(b => b.textContent.includes('Error report')).click()")
        p.wait_for("document.querySelector('#report-modal .rp-text') && document.querySelector('#report-modal .rp-text').value.startsWith('Kurgu report')", 60)
        assert p.eval("document.getElementById('rp-agent').checked") is False
        assert "may quote your notes" in p.eval("document.querySelector('#report-modal .rp-opt').textContent")
        assert "AGENT QUOTES" not in p.eval("document.querySelector('#report-modal .rp-text').value")
        p.click_sel("#rp-agent")
        p.wait_for("document.querySelector('#report-modal .rp-text').value.includes('AGENT QUOTES YOUR NOTE')", 30)
        p.eval("window.__copied=[]; navigator.clipboard.writeText=t=>{window.__copied.push(t);return Promise.resolve()}")
        p.wait_for("!document.querySelector('#report-modal .rp-copy').disabled")
        p.click_sel("#report-modal .rp-copy")
        p.wait_for("window.__copied.length===1")
        assert "AGENT QUOTES YOUR NOTE" in p.eval("window.__copied[0]") and p.eval("window.__copied[0]") == p.eval("document.querySelector('#report-modal .rp-text').value")
        p.click_sel("#rp-agent")                                                              # off again: the text goes back
        p.wait_for("!document.querySelector('#report-modal .rp-text').value.includes('AGENT QUOTES')", 30)
        p.close()
    finally:
        s.close()
