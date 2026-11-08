"""The agent's wake status in the editor (real headless Chrome, own server): "Agent is working…" on the sent notes, a persistent error when the
woken agent exits non-zero ("Agent didn't run: Not logged in" + hint + hook.log link), and "finished but N notes still open" after a clean exit.
The hook is a fake that does what the control file says. Skipped without Chrome / websocket-client."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

try:
    import websocket  # noqa: F401
    HAVE_WS = True
except Exception:  # pragma: no cover
    HAVE_WS = False

pytestmark = pytest.mark.skipif(not (cdp.find_chrome() and HAVE_WS), reason="Chrome / websocket-client not available")

HOOK = """import os, sys, time
ctl, rel = sys.argv[1], sys.argv[2]
mode = open(ctl).read().strip()
if mode == 'hold':
    t0 = time.time()
    while not os.path.exists(rel) and time.time() - t0 < 60:
        time.sleep(0.05)
    mode = open(ctl).read().strip()
if mode == 'fail':
    print('Not logged in \\u00b7 Please run /login')
    sys.exit(1)
"""


@pytest.fixture(scope="module")
def env():
    tmp = tempfile.mkdtemp(prefix="kurgu-wakeui-")
    ctl, rel, script = (os.path.join(tmp, n) for n in ("mode", "release", "hook.py"))
    open(script, "w").write(HOOK)
    open(ctl, "w").write("hold")
    cfgdir = os.path.join(tmp, "conf")
    os.makedirs(cfgdir)
    json.dump({"on_send": [sys.executable, script, ctl, rel]}, open(os.path.join(cfgdir, "config.json"), "w"))
    old = {k: os.environ.get(k) for k in ("KURGU_CONFIG", "KURGU_ON_SEND")}
    os.environ["KURGU_CONFIG"] = cfgdir
    os.environ.pop("KURGU_ON_SEND", None)
    s = cdp.Server()
    c = cdp.Chrome(width=1440, height=900)
    p = c.new_page()
    p.open(s.url("/"), lang="en")
    yield p, ctl, rel
    p.close()
    c.close()
    s.close()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


WAIT = 60 if cdp.CI else 20      # a slow runner (macOS CI) can need much longer than a laptop for the editor to come back after a reload


def add_and_send(page, text):
    # the note box must be there and laid out before the click: right after test_1's reloads a slow runner may still be bringing the editor up
    page.wait_for("(()=>{const e=document.querySelector('#nb-input');if(!e)return false;const r=e.getBoundingClientRect();"
                  "return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'})()", WAIT)
    page.click_sel("#nb-input")
    page.send("Input.insertText", text=text)
    page.key("Enter", code="Enter")
    page.wait_for("__kurgu.S.project.notes.some(n=>n.text===%s)" % json.dumps(text), WAIT)
    page.click_sel("#b-send")
    page.wait_for("__kurgu.S.project.notes.find(n=>n.text===%s).sent" % json.dumps(text), WAIT)


def test_1_working_then_failure_with_cause_hint_and_log_link(env):
    page, ctl, rel = env
    page.click_sel("#b-notes")
    page.wait_for("!!document.querySelector('#notes-panel')")
    add_and_send(page, "first")
    page.wait_for("!!document.querySelector('#notes-panel .np-state.wk-working')")
    assert page.eval("document.querySelector('#notes-panel .np-state.wk-working').textContent") == "Agent is working…"
    assert page.eval("!document.querySelector('#notes-panel .wk-banner')")
    open(ctl, "w").write("fail")
    open(rel, "w").close()
    page.wait_for("!!document.querySelector('#notes-panel .wk-banner.err')")
    assert page.eval("document.querySelector('#notes-panel .wk-title').textContent") == "Agent didn't run: Not logged in · Please run /login"
    assert "log in" in page.eval("document.querySelector('#notes-panel .wk-hint').textContent")
    a = page.eval("(()=>{const a=document.querySelector('#notes-panel .wk-log');return [a.textContent,a.getAttribute('href')]})()")
    assert a == ["show hook.log", "/api/notes/hook-log"]
    assert page.eval("!document.querySelector('#notes-panel .wk-working')")          # no longer "working"
    page.eval("localStorage.setItem('kurgu.lang','tr')")                              # persistent: still there after a reload; Turkish wording
    page.reload_and_wait("window.__kurgu && __kurgu.S.project && document.querySelector('.row')")
    page.wait_for("!!document.querySelector('#notes-panel .wk-banner.err')")
    assert page.eval("document.querySelector('#notes-panel .wk-title').textContent").startswith("Ajan çalışmadı: Not logged in")
    assert page.eval("document.querySelector('#notes-panel .wk-log').textContent") == "hook.log'u göster"
    page.eval("localStorage.setItem('kurgu.lang','en')")
    page.reload_and_wait("window.__kurgu && __kurgu.S.project && document.querySelector('.row')")


def test_2_clean_exit_with_a_note_still_open_says_so(env):
    page, ctl, rel = env
    open(ctl, "w").write("ok")
    page.wait_for("!!document.querySelector('#notes-panel')")
    add_and_send(page, "second")
    page.wait_for("!!document.querySelector('#notes-panel .wk-banner.warn')")
    txt = page.eval("document.querySelector('#notes-panel .wk-banner.warn .wk-title').textContent")
    assert txt in ("Agent finished but 2 notes are still open", "Agent finished but 1 note is still open"), txt
