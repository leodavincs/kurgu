"""Minimal headless-Chrome driver over CDP for parity tests (own profile, own debug port, never the user's Chrome)."""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    import websocket
except ImportError:                                  # pragma: no cover
    websocket = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import chrome_flags, find_chrome, STARTUP_WAIT      # noqa: E402,F401  (one launcher / one binary lookup for every Chrome test)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Chrome:
    def __init__(self, exe, width=1400, height=900):
        self.port = free_port()
        self.profile = tempfile.mkdtemp(prefix="kurgu-chrome-")
        self.proc = subprocess.Popen(
            [exe] + chrome_flags(self.port, self.profile, width, height, ["--force-color-profile=srgb", "--hide-scrollbars"]), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws = None
        self._id = 0
        deadline = time.time() + STARTUP_WAIT
        while time.time() < deadline:
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=1))
                page = next(t for t in tabs if t.get("type") == "page")
                self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
                break
            except Exception:
                time.sleep(0.2)
        if self.ws is None:
            self.close()
            raise RuntimeError("could not connect to Chrome")

    def call(self, method, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]

    def eval(self, js):
        r = self.call("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:600])
        return r["result"].get("value")

    def goto(self, url):
        self.call("Page.enable")
        self.call("Page.navigate", url=url)
        end = time.time() + STARTUP_WAIT                 # wait until the navigation left about:blank (slow on Windows runners)
        while time.time() < end:
            try:
                if self.eval("location.href").startswith(url.split("#")[0][:12]) and self.eval("document.readyState") != "loading":
                    return
            except RuntimeError:
                pass
            time.sleep(0.1)

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def decode_data_url(u):
    return base64.b64decode(u.split(",", 1)[1])
