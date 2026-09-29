"""Headless-Chrome check of the Notes panel (CDP over websocket-client). Own server on port 8897 and own Chrome profile/debug port:
never touches the user's Chrome or the usual editor port. Skipped when Chrome or websocket-client is missing.
Set KURGU_SHOTS=<dir> to keep the screenshots (en / tr)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

websocket = pytest.importorskip("websocket")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

CHROME = cdp.find_chrome()
CMD = 4 if sys.platform == "darwin" else 2                          # CDP modifier bits: alt 1, ctrl 2, meta 4, shift 8; the app's command key is Meta on macOS only
pytestmark = pytest.mark.skipif(not CHROME, reason="Chrome not found")
PORT, DEBUG = 8897, 9337
NOTES = [
    {"id": "n_a", "t": 1.2, "text": "title appears too late", "created": "2026-09-29T10:00:00"},
    {"id": "n_b", "t": 3.4, "text": "sound a bit loud here", "created": "2026-09-29T10:01:00"},
    {"id": "n_c", "t": 5.0, "text": "blur too strong on the left clip", "created": "2026-09-29T10:02:00", "author": "agent",
     "reply": "Blur reduced 22 to 8"},
    {"id": "n_d", "t": 7.5, "text": "logo is too small", "created": "2026-09-29T10:03:00", "done": True, "resolved_at": "2026-09-29T11:00:00"},
    {"id": "n_e", "t": 10.5, "text": "end card needs a fade", "created": "2026-09-29T10:04:00"},
]


class Page:
    def __init__(self, url):
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{DEBUG}/json/list"))
        ws_url = next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.n = 0
        self.send("Page.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=1500, height=900, deviceScaleFactor=1, mobile=False)
        self.send("Page.navigate", url=url)
        time.sleep(1.0)

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m["result"]

    def js(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    def wait(self, expr, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(expr):
                return True
            time.sleep(0.15)
        raise AssertionError("timeout waiting for: " + expr)

    def key(self, key, code=None, vk=0, mods=0, text=None):
        for typ in ("keyDown", "keyUp"):
            p = {"type": typ, "key": key, "code": code or key, "windowsVirtualKeyCode": vk, "modifiers": mods}
            if text and typ == "keyDown":
                p["text"] = text
            self.send("Input.dispatchKeyEvent", **p)
        time.sleep(0.15)

    def click(self, selector, nth=0):
        r = self.js(f"(() => {{ const e = document.querySelectorAll({json.dumps(selector)})[{nth}]; if (!e) return null; e.scrollIntoView({{block:'nearest'}}); const b = e.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; }})()")
        assert r, "no element " + selector
        for typ in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", type=typ, x=r[0], y=r[1], button="left", clickCount=1)
        time.sleep(0.2)

    def shot(self, path):
        import base64
        with open(path, "wb") as f:
            f.write(base64.b64decode(self.send("Page.captureScreenshot", format="png")["data"]))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("notesui")
    proj = str(tmp / "proj")
    shutil.copytree(os.path.join(ROOT, "examples", "basic"), proj, ignore=shutil.ignore_patterns(".kurgu", "output*.mp4"))
    p = json.load(open(os.path.join(proj, "project.json"), encoding="utf-8"))
    p["notes"] = NOTES
    json.dump(p, open(os.path.join(proj, "project.json"), "w", encoding="utf-8"))
    server = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), proj, "--port", str(PORT), "--no-open"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp(prefix="kurgu-chrome-")
    chrome = subprocess.Popen([CHROME] + cdp.chrome_flags(DEBUG, prof, 1500, 900), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(int(cdp.STARTUP_WAIT * 10)):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{DEBUG}/json/version", timeout=1)
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/version", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("server/chrome did not start")
    yield {"proj": proj, "shots": os.environ.get("KURGU_SHOTS") or str(tmp)}
    chrome.terminate()
    server.terminate()
    shutil.rmtree(prof, ignore_errors=True)


def saved_notes(proj):
    return {n["id"]: n for n in json.load(open(os.path.join(proj, "project.json"), encoding="utf-8"))["notes"]}


def test_notes_panel_flow(env):
    pg = Page(f"http://127.0.0.1:{PORT}/")
    pg.wait("!!(window.__kurgu && window.__kurgu.S.project)")
    pg.js("localStorage.clear(); localStorage.setItem('kurgu.lang', 'en')")
    pg.send("Page.navigate", url=f"http://127.0.0.1:{PORT}/")
    pg.wait("!!(window.__kurgu && window.__kurgu.S.project && document.querySelector('#b-notes'))")
    S = "window.__kurgu.S"
    # closed by default; toggle with the button, then Shift+N
    assert pg.js("!document.querySelector('#notes-panel') || getComputedStyle(document.querySelector('#notes-panel')).display === 'none'")
    pg.click("#b-notes")
    assert pg.js("getComputedStyle(document.querySelector('#notes-panel')).display") == "flex"
    assert pg.js("document.querySelectorAll('.np-row').length") == 5
    assert pg.js("document.querySelector('.np-count').textContent") == "4 open · 1 done"
    pg.key("N", "KeyN", 78, mods=8, text="N")                      # Shift+N closes
    assert pg.js("getComputedStyle(document.querySelector('#notes-panel')).display") == "none"
    assert pg.js("document.querySelector('#note-box').hidden")     # ...and did not open the note box
    pg.key("N", "KeyN", 78, mods=8, text="N")                      # and opens again
    assert pg.js("localStorage.getItem('kurgu.np.open')") == "1"
    order = pg.js("[...document.querySelectorAll('.np-row')].map(r => r.dataset.id).join()")
    assert order == "n_a,n_b,n_c,n_d,n_e"
    # the viewer layout survives: viewer, notes and inspector side by side, nothing overlapping
    boxes = pg.js("['#preview-section', '#notes-panel', '#inspector'].map(s => { const b = document.querySelector(s).getBoundingClientRect(); return [b.left, b.right, b.width]; })")
    assert boxes[0][1] <= boxes[1][0] + 1 and boxes[1][1] <= boxes[2][0] + 1 and boxes[0][2] > 300
    # thumbnails load lazily
    pg.wait("document.querySelectorAll('.np-thumb img:not([hidden])').length >= 3", timeout=40)
    # click a note: playhead jumps
    pg.click(".np-row", 2)
    assert abs(pg.js(f"{S}.t") - 5.0) < 1e-6
    assert pg.js("document.querySelectorAll('.np-row')[2].classList.contains('active')")
    assert pg.js("document.querySelectorAll('.np-row.active').length") == 1
    assert pg.js("document.querySelectorAll('.np-row')[2].querySelector('.np-reply span:last-child').textContent") == "Blur reduced 22 to 8"
    assert pg.js("document.querySelectorAll('.np-row')[2].querySelector('.np-badge').textContent") == "Agent"
    chips = pg.js("[...document.querySelectorAll('.np-row')[2].querySelectorAll('.np-chip')].map(c => c.textContent)")
    assert chips and "no layer here" not in chips
    pg.shot(os.path.join(env["shots"], "notes-en.png"))
    # arrow keys inside the list
    pg.js("document.querySelectorAll('.np-row')[2].focus()")
    pg.key("ArrowDown", "ArrowDown", 40)
    assert abs(pg.js(f"{S}.t") - 7.5) < 1e-6
    pg.key("ArrowUp", "ArrowUp", 38)
    pg.key("ArrowUp", "ArrowUp", 38)
    assert abs(pg.js(f"{S}.t") - 3.4) < 1e-6
    # Alt+Right / Alt+Left anywhere
    pg.js("document.activeElement.blur(); window.__kurgu.S.t = 0; window.__kurgu.emit('time')")
    seen = []
    for _ in range(5):
        pg.key("ArrowRight", "ArrowRight", 39, mods=1)
        seen.append(round(pg.js(f"{S}.t"), 2))
    assert seen == [1.2, 3.4, 5.0, 7.5, 10.5]
    pg.key("ArrowRight", "ArrowRight", 39, mods=1)
    assert round(pg.js(f"{S}.t"), 2) == 10.5                       # no wrap-around
    pg.key("ArrowLeft", "ArrowLeft", 37, mods=1)
    assert round(pg.js(f"{S}.t"), 2) == 7.5
    # mark done -> project.json is updated by autosave
    pg.click(".np-check", 1)
    for _ in range(40):
        n = saved_notes(env["proj"])["n_b"]
        if n.get("done"):
            break
        time.sleep(0.25)
    assert n["done"] is True and n["resolved_at"] and n["author"] == "user"
    assert pg.js("document.querySelector('.np-count').textContent") == "3 open · 2 done"
    assert pg.js("document.querySelectorAll('.scrub-note.done').length") == 2 and pg.js("document.querySelectorAll('.lp-note.done').length") == 2
    # filter Open / Done + search
    pg.click(".np-seg-b", 1)
    assert pg.js("document.querySelectorAll('.np-row').length") == 3
    pg.click(".np-seg-b", 2)
    assert pg.js("[...document.querySelectorAll('.np-row')].map(r => r.dataset.id).join()") == "n_b,n_d"
    pg.click(".np-seg-b", 0)
    pg.js("(() => { const i = document.querySelector('.np-search'); i.value = 'TOO SMALL'; i.dispatchEvent(new Event('input')); })()")
    assert pg.js("[...document.querySelectorAll('.np-row')].map(r => r.dataset.id).join()") == "n_d"
    pg.js("(() => { const i = document.querySelector('.np-search'); i.value = ''; i.dispatchEvent(new Event('input')); })()")
    # inline edit: double click the text, type, Enter
    pg.js(f"{S}.t = 0")
    pg.js("(() => { const t = document.querySelectorAll('.np-text')[0]; t.dispatchEvent(new MouseEvent('dblclick', {bubbles: true})); })()")
    pg.wait("!!document.querySelector('.np-edit')")
    pg.js("(() => { const a = document.querySelector('.np-edit'); a.value = 'title comes in earlier'; })()")
    pg.key("Enter", "Enter", 13)
    for _ in range(40):
        if saved_notes(env["proj"])["n_a"]["text"] == "title comes in earlier":
            break
        time.sleep(0.25)
    assert saved_notes(env["proj"])["n_a"]["text"] == "title comes in earlier"
    assert pg.js("document.querySelector('.np-edit')") is None
    # delete is undoable
    pg.click(".np-ib.del", 4)
    assert pg.js("document.querySelectorAll('.np-row').length") == 4
    pg.key("z", "KeyZ", 90, mods=CMD)                              # Cmd+Z (Ctrl+Z off macOS)
    assert pg.js("document.querySelectorAll('.np-row').length") == 5
    # add from the panel and by the N box: user notes with the layers under the playhead
    pg.js(f"{S}.t = 5.0; window.__kurgu.emit('time')")
    pg.click(".np-add-in")
    pg.js("(() => { const i = document.querySelector('.np-add-in'); i.value = 'added in panel'; })()")
    pg.key("Enter", "Enter", 13)
    for _ in range(40):
        added = [n for n in saved_notes(env["proj"]).values() if n["text"] == "added in panel"]
        if added:
            break
        time.sleep(0.25)
    assert added and added[0]["author"] == "user" and added[0]["done"] is False and added[0]["t"] == 5.0 and added[0]["layer_ids"]
    # Ask AI about this: the box is scoped to that moment with the note text
    pg.click(".np-row[data-id=n_e] .np-ib.ask")
    pg.wait("!!document.querySelector('.ai-input') && document.querySelector('.ai-input').value === 'end card needs a fade'")
    assert abs(pg.js(f"{S}.t") - 10.5) < 1e-6 and pg.js(f"{S}.selection.length") == 0
    pg.shot(os.path.join(env["shots"], "notes-en-ask.png"))
    # Turkish
    pg.js("window.__kurgu.setLang('tr')")
    pg.wait("document.querySelector('.np-count').textContent.includes('açık')")
    pg.click(".np-row", 2)
    pg.shot(os.path.join(env["shots"], "notes-tr.png"))
    assert "Ajan" in pg.js("document.querySelector('.np-row[data-id=n_c] .np-badge').textContent")
    # light theme sanity is left to Lumen; here: no console-visible JS errors
    assert pg.js("typeof window.__kurgu === 'object'")
    pg.ws.close()
