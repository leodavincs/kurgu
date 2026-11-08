"""The editor shows the agent's question on an `asked` note and the answer goes back to the agent (new inbox seq)."""
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

import cdp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = cdp.find_chrome()
pytestmark = pytest.mark.skipif(not CHROME, reason="Chrome not found")
PORT, DEBUG = 8861, 9361
NOTES = [
    {"id": "n_a", "t": 1.2, "text": "make the start more dynamic", "created": "2026-11-07T10:00:00", "sent": "2026-11-07T10:01:00",
     "question": "Faster cut or a zoom?", "asked": "2026-11-07T10:02:00"},
    {"id": "n_b", "t": 3.4, "text": "sound a bit loud here", "created": "2026-11-07T10:01:00"},
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
    cdp.stop_chrome(chrome, DEBUG, prof)
    server.terminate()



def saved(proj):
    return {n["id"]: n for n in json.load(open(os.path.join(proj, "project.json"), encoding="utf-8"))["notes"]}


def test_asked_note_shows_question_and_answer_goes_back_to_the_agent(env):
    pg = Page(f"http://127.0.0.1:{PORT}/")
    pg.wait("!!(window.__kurgu && window.__kurgu.S.project)")
    pg.js("localStorage.clear(); localStorage.setItem('kurgu.lang', 'en'); localStorage.setItem('kurgu.mode', 'edit')")
    pg.send("Page.navigate", url=f"http://127.0.0.1:{PORT}/")
    pg.wait("!!(window.__kurgu && window.__kurgu.S.project && document.querySelector('#b-notes'))")
    pg.click("#b-notes")
    pg.wait("document.querySelectorAll('.np-row').length === 2")
    assert pg.js("document.querySelectorAll('#notes-panel .ask-box.open').length") == 1
    assert "Faster cut or a zoom?" in pg.js("document.querySelector('#notes-panel .ask-q').textContent")
    assert pg.js("document.querySelector('#notes-panel .np-state.asked').textContent") == "question for you"
    assert pg.js("document.querySelectorAll('#notes-panel .ask-box').length") == 1                     # the plain note has no box
    pg.click("#notes-panel .ask-in")
    for ch in "a zoom":
        pg.send("Input.dispatchKeyEvent", type="char", text=ch)
    pg.click("#notes-panel .ask-go")
    pg.wait("document.querySelectorAll('#notes-panel .ask-box.open').length === 0")
    n = saved(env["proj"])["n_a"]
    assert n["answer"] == "a zoom" and not n.get("asked") and n["sent"] > "2026-11-07T10:02:00"
    assert "a zoom" in pg.js("document.querySelector('#notes-panel .ask-a').textContent")
    inbox = os.path.join(env["proj"], ".kurgu", "inbox.jsonl")
    for _ in range(int(cdp.STARTUP_WAIT * 10)):                                   # the answer is saved first, the inbox line follows: on a slow runner a moment later
        if os.path.exists(inbox) and "a zoom" in open(inbox, encoding="utf-8").read():
            break
        time.sleep(0.1)
    lines = open(inbox, encoding="utf-8").read().splitlines()
    assert json.loads(lines[-1])["notes"][0]["answer"] == "a zoom"
