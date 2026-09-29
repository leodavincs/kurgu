"""Tiny Chrome-DevTools-Protocol harness for the UI smoke test and the screenshot script.

Starts server.py on a private copy of a project, launches headless Chrome with a remote-debugging port and gives a
`Page` object with eval / click / drag / screenshot helpers. Needs `websocket-client` and a Chrome / Chromium binary.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "basic")

WIN_CHROME = [os.path.join(os.environ.get(v, ""), *rest) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
              for rest in (("Google", "Chrome", "Application", "chrome.exe"),)] if sys.platform.startswith("win") else []
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
] + WIN_CHROME
CI = bool(os.environ.get("CI"))
STARTUP_WAIT = 60 if CI else 20      # seconds a spawned server / Chrome gets to come up


def find_chrome():
    """KURGU_CHROME / CHROME_PATH / CHROME_BIN (what browser-actions/setup-chrome exposes), then well-known places, then PATH."""
    for var in ("KURGU_CHROME", "CHROME_PATH", "CHROME_BIN"):
        c = os.environ.get(var, "").strip()
        if c and os.path.exists(c):
            return c
    for c in CHROME_CANDIDATES:
        if c and os.path.exists(c):
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def chrome_flags(port, profile, width, height, extra=()):
    """Command-line flags shared by every test that starts headless Chrome. On Linux (CI runs as a user without a usable
    sandbox / with a tiny /dev/shm) --no-sandbox and --disable-dev-shm-usage are needed."""
    flags = ["--headless=new", "--remote-debugging-port=%d" % port, "--remote-allow-origins=*", "--user-data-dir=" + profile,
             "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--window-size=%d,%d" % (width, height)]
    if sys.platform.startswith("linux") or os.environ.get("CI"):
        flags += ["--no-sandbox", "--disable-dev-shm-usage"]
    return flags + list(extra) + ["about:blank"]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_http(url, timeout=None):
    timeout = timeout or STARTUP_WAIT
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return r.read()
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("timeout waiting for " + url)


class Server:
    """server.py on a temp copy of examples/basic (optionally edited by `prepare(project_dict)`)."""

    def __init__(self, prepare=None, fake_agent=False):
        self.tmp = tempfile.mkdtemp(prefix="kurgu-ui-")
        self.dir = os.path.join(self.tmp, "proj")
        shutil.copytree(EXAMPLE, self.dir, ignore=shutil.ignore_patterns(".kurgu", "output*.mp4"))
        if prepare:
            pj = os.path.join(self.dir, "project.json")
            p = json.load(open(pj))
            prepare(p)
            json.dump(p, open(pj, "w"), indent=2)
        self.port = free_port()
        env = dict(os.environ, KURGU_CACHE=os.path.join(self.tmp, "cache"))
        if fake_agent:  # the test double of the AI agent (tests/fake_agent.py) so the Ask-AI box can be driven
            import shlex
            env["KURGU_AGENT_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(os.path.join(ROOT, "tests", "fake_agent.py")))
        self.log = os.path.join(self.tmp, "server.log")
        with open(self.log, "wb") as lf:
            self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), self.dir, "--port", str(self.port), "--no-open"],
                                         cwd=ROOT, env=env, stdout=lf, stderr=subprocess.STDOUT)
        try:
            wait_http(self.url("/api/project"))
        except RuntimeError as e:      # say why (dead process? slow start?) in the CI log
            try:
                tail = open(self.log, encoding="utf-8", errors="replace").read()[-1500:]
            except OSError:
                tail = ""
            raise RuntimeError("%s (server exit code %s)\n%s" % (e, self.proc.poll(), tail))

    def url(self, path="/"):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def project(self):
        return json.loads(urllib.request.urlopen(self.url("/api/project")).read())

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Chrome:
    def __init__(self, width=1600, height=1000, scale=1):
        import websocket  # noqa: F401  (import error -> caller skips)
        self.tmp = tempfile.mkdtemp(prefix="kurgu-chrome-")
        self.port = free_port()
        self.proc = subprocess.Popen([find_chrome()] + chrome_flags(self.port, self.tmp, width, height, ["--autoplay-policy=no-user-gesture-required", "--hide-scrollbars=false"]),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_http("http://127.0.0.1:%d/json/version" % self.port)
        self.width, self.height, self.scale = width, height, scale

    def new_page(self):
        req = urllib.request.Request("http://127.0.0.1:%d/json/new?about:blank" % self.port, method="PUT")
        tab = json.loads(urllib.request.urlopen(req, timeout=10).read())
        pg = Page(tab["webSocketDebuggerUrl"], self.width, self.height, self.scale)
        pg.tab_id, pg.chrome_port = tab["id"], self.port
        return pg

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Page:
    def __init__(self, ws_url, width, height, scale):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.n = 0
        self.lock = threading.Lock()
        self.errors = []      # console errors / exceptions / failed requests
        self.pending = {}
        self.dead = False
        threading.Thread(target=self._reader, daemon=True).start()
        for m in ("Page.enable", "Runtime.enable", "Log.enable", "Network.enable"):
            self.send(m)
        self.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=scale, mobile=False)

    # -- plumbing
    def _reader(self):
        while not self.dead:
            try:
                raw = self.ws.recv()
            except Exception:
                return
            if not raw:
                continue
            m = json.loads(raw)
            if "id" in m:
                with self.lock:
                    slot = self.pending.pop(m["id"], None)
                if slot:
                    slot["res"] = m
                    slot["ev"].set()
                continue
            meth, p = m.get("method"), m.get("params", {})
            if meth == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
                self.errors.append("console." + p["type"] + ": " + " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))
            elif meth == "Runtime.exceptionThrown":
                d = p["exceptionDetails"]
                self.errors.append("exception: " + (d.get("exception", {}).get("description") or d.get("text", "")))
            elif meth == "Log.entryAdded" and p["entry"].get("level") == "error":
                e = p["entry"]
                self.errors.append("log: " + e.get("text", "") + " " + e.get("url", ""))

    def send(self, method, **params):
        with self.lock:
            self.n += 1
            i = self.n
            slot = {"ev": threading.Event(), "res": None}
            self.pending[i] = slot
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        if not slot["ev"].wait(60):
            raise TimeoutError(method)
        r = slot["res"]
        if "error" in r:
            raise RuntimeError("%s: %s" % (method, r["error"]))
        return r.get("result", {})

    def eval(self, js, await_promise=False):
        r = self.send("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            raise RuntimeError("eval failed: " + json.dumps(r["exceptionDetails"])[:400] + "  <<" + js[:200])
        return r["result"].get("value")

    def wait_for(self, js, timeout=10, interval=0.1):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                v = self.eval('!!(' + js + ')')
            except RuntimeError:
                v = None
            if v:
                return v
            time.sleep(interval)
        raise TimeoutError("condition never true: " + js)

    # -- navigation
    def open(self, url, lang=None, theme=None, view="layers", ready="window.__kurgu && __kurgu.S.project && document.querySelector('.row')"):
        self.send("Page.navigate", url=url)
        time.sleep(0.2)
        # set persisted prefs, then reload so that boot() sees them
        pre = []
        if view:   # the product default is the track view; the older tests were written for one row per layer
            pre.append("localStorage.setItem('kurgu.layerView','%s')" % view)
        if lang:
            pre.append("localStorage.setItem('kurgu.lang','%s')" % lang)
        if theme:
            pre.append("localStorage.setItem('kurgu.theme','%s')" % theme)
        if pre:
            self.eval(";".join(pre))
            self.send("Page.reload")
        self.wait_for(ready, 60 if os.environ.get("CI") else 20)
        time.sleep(0.6)

    # -- input
    def mouse(self, kind, x, y, button="left", buttons=0, clicks=1, modifiers=0):
        self.send("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button, buttons=buttons, clickCount=clicks, modifiers=modifiers)

    def click(self, x, y, clicks=1, modifiers=0):
        self.mouse("mouseMoved", x, y)
        for c in range(1, clicks + 1):
            self.mouse("mousePressed", x, y, buttons=1, clicks=c, modifiers=modifiers)
            self.mouse("mouseReleased", x, y, buttons=0, clicks=c, modifiers=modifiers)

    def drag(self, x0, y0, x1, y1, steps=8):
        self.mouse("mouseMoved", x0, y0)
        self.mouse("mousePressed", x0, y0, buttons=1)
        for i in range(1, steps + 1):
            self.mouse("mouseMoved", x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps, buttons=1)
            time.sleep(0.02)
        self.mouse("mouseReleased", x1, y1)

    def key(self, key, code=None, modifiers=0, text=None):
        base = {"key": key, "code": code or key, "modifiers": modifiers}
        self.send("Input.dispatchKeyEvent", type="keyDown", **base, **({"text": text} if text else {}))
        self.send("Input.dispatchKeyEvent", type="keyUp", **base)

    def center(self, selector):
        """Centre of the first element matching the selector, in viewport css px (None when missing / hidden)."""
        return self.eval("(()=>{const e=document.querySelector(%s);if(!e)return null;e.scrollIntoView({block:'center',inline:'nearest'});const r=e.getBoundingClientRect();if(!r.width||!r.height)return null;return [r.left+r.width/2,r.top+r.height/2]})()" % json.dumps(selector))

    def click_sel(self, selector, **kw):
        c = self.center(selector)
        if not c:
            raise AssertionError("not clickable: " + selector)
        self.click(c[0], c[1], **kw)

    def screenshot(self, path, clip=None):
        args = {"format": "png"}
        if clip:
            args["clip"] = dict(clip, scale=1)
        data = self.send("Page.captureScreenshot", **args)["data"]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

    def close(self):
        self.dead = True
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/json/close/%s" % (self.chrome_port, self.tab_id), timeout=5).read()
        except Exception:
            pass
