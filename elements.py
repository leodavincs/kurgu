"""kurgu elements: where is every piece of text / every marked object in a video that is made by a web page?

Opens a "frame-function" page headless (any page that exposes `window.renderFrame(i)` or `window.captureFrame(i)`, a pure
function of the frame: cinecode, HyperFrames-style pages, our own motor), calls it for each requested frame and writes the
bounding boxes of the visible DOM text and of the elements marked `data-kurgu-id` as an element map (`*.kurgu.json`).

Stdlib only: a tiny DevTools-protocol client over a hand-written WebSocket, headless Chrome found on the machine.
Text, shapes and images drawn with the 2D canvas API (fillText, fill/stroke, fillRect, drawImage, Path2D) are recorded by wrapping the context
before any page script runs (nothing to add to the page); WebGL / three.js output is pixels and cannot be captured: the output says so.
Set KURGU_ELEMENTS_PORT to move Chrome's debugging port range.

Page conventions (all optional):
    data-kurgu-id="title"          a stable id for the element (else a generated one, stable for the same DOM position)
    data-kurgu-src="scene.js:41"   the source file:line that produces it (written to `file` / `line`)
    window.kurguElements(i)        canvas engines: returns [{id,label,kind,l,t,r,b,file?,line?}] in page px, called after renderFrame(i)
    window.READY                   a promise the page resolves when it can draw (awaited); may carry {fps, width, height}
"""
import base64
import hashlib
import json
import kurgu_paths
import threading
import http.server
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

PORT_BASE = int(os.environ.get("KURGU_ELEMENTS_PORT", "8940"))   # Chrome's debugging port: first free port from here up (local only)
STARTUP_WAIT = 60 if os.environ.get("CI") else 25
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]
if sys.platform.startswith("win"):
    CHROME_CANDIDATES += [os.path.join(os.environ.get(v, ""), "Google", "Chrome", "Application", "chrome.exe")
                          for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]


class ElementsError(Exception):
    pass


# ---------------------------------------------------------------- frames / sizes
def parse_frames(spec):
    """"0,24,48" or "a:b:step" (b inclusive, step default 1) or a mix separated by commas -> sorted unique ints."""
    out = set()
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        try:
            if ":" in part:
                bits = part.split(":")
                if len(bits) not in (2, 3):
                    raise ValueError
                a, b = int(bits[0]), int(bits[1])
                step = int(bits[2]) if len(bits) == 3 else 1
                if step < 1 or b < a:
                    raise ValueError
                out.update(range(a, b + 1, step))
                out.add(b)                                    # the last frame of a range is always measured
            else:
                out.add(int(part))
        except ValueError:
            raise ElementsError(f"bad --frames part {part!r}: use a list like 0,24,48 or a range a:b:step (b inclusive)")
    if not out or min(out) < 0:
        raise ElementsError("--frames needs at least one frame number >= 0")
    return sorted(out)


def parse_size(s):
    m = re.fullmatch(r"\s*(\d{2,5})\s*[xX*,]\s*(\d{2,5})\s*", str(s or ""))
    if not m:
        raise ElementsError(f"bad --size {s!r}: use WxH, e.g. 1920x1080")
    return int(m[1]), int(m[2])


def page_url(page, params=""):
    """A local html path or an http(s)/file URL -> URL (with extra query params, e.g. "w=1920&h=1080&capture=1")."""
    if re.match(r"^(https?|file)://", page):
        url = page
    else:
        p = os.path.abspath(os.path.expanduser(page))
        if not os.path.isfile(p):
            raise ElementsError(f"page not found: {page}")
        url = "file://" + urllib.parse.quote(p.replace(os.sep, "/") if os.name != "nt" else "/" + p.replace(os.sep, "/"))
    if params:
        url += ("&" if "?" in url else "?") + params.lstrip("?&")
    return url


# ---------------------------------------------------------------- Chrome + a tiny DevTools client
def find_chrome():
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
    raise ElementsError("Chrome / Chromium was not found. Install it or set KURGU_CHROME=/path/to/chrome")


def _free_port(start=PORT_BASE):
    """A free local port from a random point of a 1000-port window: parallel runs (several agents, a test suite) would otherwise all pick the first free
    one and end up talking to each other's Chrome."""
    import random
    n = 1000
    off = random.randrange(n)
    for p in (start + (off + k) % n for k in range(n)):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise ElementsError("no free local port for Chrome's debugging socket")


class _WebSocket:
    """Just enough RFC 6455 for DevTools: text messages, masked client frames, ping/pong, fragmentation."""

    def __init__(self, url, timeout=60):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ElementsError("Chrome closed the DevTools socket during the handshake")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ElementsError("DevTools WebSocket handshake refused")

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ElementsError("Chrome closed the DevTools socket")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text, opcode=1):
        data = text.encode("utf-8") if isinstance(text, str) else text
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        message = b""
        while True:
            b0, b1 = self._need(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._need(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._need(8))[0]
            payload = self._need(n)          # server frames are unmasked
            if opcode == 8:
                raise ElementsError("Chrome closed the DevTools socket")
            if opcode == 9:
                self.send(payload, opcode=10)
                continue
            if opcode == 10:
                continue
            message += payload
            if b0 & 0x80:
                return message.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class _RootServer:
    """Serves ONE folder on 127.0.0.1 and doubles as the only HTTP proxy the measuring Chrome may use: a request for any other host or port gets 403,
    CONNECT (https) gets 403, and a path that leaves the folder (`..`, absolute, or a symlink pointing out) gets 404. So a scene page can load its own files
    and nothing else: no `file://` reads, no other local service, no internet."""

    def __init__(self, root):
        import http.server
        self.root = os.path.realpath(root)
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _deny(self, code):
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _serve(self, head):
                raw = self.path
                if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):                  # proxy form: absolute URI
                    u = urllib.parse.urlsplit(raw)
                    if u.scheme != "http" or u.hostname != "127.0.0.1" or u.port != outer.port:
                        return self._deny(403)
                    raw = u.path
                else:
                    raw = urllib.parse.urlsplit(raw).path
                rel = urllib.parse.unquote(raw).lstrip("/")
                if "\x00" in rel:
                    return self._deny(404)
                p = os.path.realpath(os.path.join(outer.root, rel))
                if not (p == outer.root or p.startswith(outer.root + os.sep)) or not os.path.isfile(p):
                    return self._deny(404)
                import mimetypes
                ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
                if p.lower().endswith((".js", ".mjs")):
                    ctype = "text/javascript"
                try:
                    with open(p, "rb") as f:
                        data = f.read()
                except OSError:
                    return self._deny(404)
                self.send_response(200)
                self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if not head:
                    self.wfile.write(data)

            def do_GET(self):
                self._serve(False)

            def do_HEAD(self):
                self._serve(True)

            def do_CONNECT(self):
                self._deny(403)

            def _no(self):
                self._deny(405)
            do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def contains(self, path):
        p = os.path.realpath(path)
        return p == self.root or p.startswith(self.root + os.sep)

    def url_for(self, path, params=""):
        rel = os.path.relpath(os.path.realpath(path), self.root).replace(os.sep, "/")
        url = f"http://127.0.0.1:{self.port}/" + urllib.parse.quote(rel)
        return url + (("&" if "?" in url else "?") + params.lstrip("?&") if params else "")

    def close(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


def chrome_died_message(what, stderr_path=None, exit_code=None):
    """What to tell the person when Chrome stops answering: the likely causes in plain words, plus the tail of Chrome's own stderr."""
    tail = ""
    try:
        with open(stderr_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4000))
            lines = [re.sub(r"[\x00-\x08\x0b-\x1f]", "", l).strip() for l in f.read().decode("utf-8", "replace").splitlines()]
        tail = " | ".join(l[:200] for l in [x for x in lines if x][-4:])
    except (OSError, TypeError):
        pass
    code = f" (exit code {exit_code})" if exit_code is not None else ""
    return (f"{what}: Chrome most likely failed to start or crashed{code}. Check that Chrome is installed and starts "
            "(set KURGU_CHROME=/path/to/chrome to choose one); sandbox, keychain and profile-folder problems also end here."
            + (f" Chrome said: {tail}" if tail else " Chrome printed nothing."))


class Browser:
    """Headless Chrome with its own throw-away profile, muted, local debugging port. Always `close()` it (or use `with`)."""

    def __init__(self, width=1920, height=1080, root=None):
        """`root`: the folder whose pages this Chrome may open. It is served over HTTP on loopback and every other request (other hosts, other local ports,
        file://) is refused: a hostile scene page cannot read your files or reach the network. root=None is for a page the user named by URL on the
        command line: no network block (WebRTC stays off)."""
        why = kurgu_paths.chrome_blocked_reason()
        if why:
            raise ElementsError(why)
        self.profile = tempfile.mkdtemp(prefix="kurgu-elements-")
        self.port = _free_port()
        self.server = _RootServer(root) if root else None
        flags = ["--headless=new", f"--remote-debugging-port={self.port}", "--remote-allow-origins=*", f"--user-data-dir={self.profile}",
                 "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--mute-audio", "--hide-scrollbars", "--disable-extensions",
                 "--force-color-profile=srgb", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", f"--window-size={width},{height}"]
        flags += kurgu_paths.WEBRTC_FLAGS + kurgu_paths.no_sandbox_flags()
        if self.server:
            flags += [f"--proxy-server=http://127.0.0.1:{self.server.port}", "--proxy-bypass-list=<-loopback>", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]
        self.err_path = os.path.join(self.profile, "chrome-stderr.log")
        self._err = open(self.err_path, "wb")
        self.proc = subprocess.Popen([find_chrome()] + flags + ["about:blank"], stdout=subprocess.DEVNULL, stderr=self._err)
        import atexit
        atexit.register(lambda p=self.proc: p.poll() is None and kurgu_paths.stop_chrome(p, None, None, 3))      # no Chrome outlives the command
        self.ws = None
        self._id = 0
        end = time.time() + STARTUP_WAIT
        while time.time() < end and self.ws is None and self.proc.poll() is None:     # a Chrome that already exited will not answer
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=1) as r:
                    tabs = json.load(r)
                page = next(t for t in tabs if t.get("type") == "page")
                self.ws = _WebSocket(page["webSocketDebuggerUrl"])
            except Exception:
                time.sleep(0.2)
        if self.ws is None:
            msg = chrome_died_message("could not start headless Chrome", self.err_path, self.proc.poll())
            self.close()
            raise ElementsError(msg)
        try:
            self.call("Page.enable")
            self.call("Page.addScriptToEvaluateOnNewDocument", source=kurgu_paths.WEBRTC_JS)
        except ElementsError:
            pass

    def call(self, method, **params):
        self._id += 1
        try:
            self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get("id") == self._id:
                    break
        except (ElementsError, OSError) as e:
            if isinstance(e, ElementsError) and "closed the DevTools socket" not in str(e):
                raise
            time.sleep(0.2)                                   # let the process finish dying so its exit code and last words are there
            raise ElementsError(chrome_died_message("Chrome closed the DevTools connection", self.err_path, self.proc.poll()))
        if "error" in msg:
            raise ElementsError(f"{method}: {msg['error'].get('message', msg['error'])}")
        return msg["result"]

    def eval(self, js, timeout_ms=None):
        params = {"expression": js, "awaitPromise": True, "returnByValue": True}
        if timeout_ms:
            params["timeout"] = timeout_ms
        r = self.call("Runtime.evaluate", **params)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise ElementsError(((d.get("exception") or {}).get("description") or d.get("text") or "script error").splitlines()[0])
        return r["result"].get("value")

    def goto(self, url, timeout=30):
        self.call("Page.enable")
        self.call("Page.navigate", url=url)
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.eval("document.readyState") in ("interactive", "complete") and self.eval("location.href") != "about:blank":
                    return
            except ElementsError as e:
                if "Chrome closed" in str(e):
                    raise
            time.sleep(0.1)
        raise ElementsError("the page did not load")

    def close(self):
        def graceful():
            if self.ws:
                self.ws.send(json.dumps({"id": 10 ** 6, "method": "Browser.close"}))        # CDP: Chrome quits itself
        kurgu_paths.stop_chrome(self.proc, graceful, None)
        if self.ws:
            self.ws.close()
        try:
            self._err.close()
        except (AttributeError, OSError):
            pass
        kurgu_paths.stop_chrome(self.proc, None, self.profile)
        if getattr(self, "server", None):
            self.server.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# ---------------------------------------------------------------- what runs inside the page
_COLLECT_JS = r"""
(function (selector) {
  var SKIP = {SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TITLE: 1, HEAD: 1, META: 1, LINK: 1, TEMPLATE: 1, CANVAS: 1, SVG: 0};
  var vw = innerWidth, vh = innerHeight, out = [], seen = new Set(), canvases = 0;
  function pathOf(el) {
    var parts = [];
    for (var e = el; e && e.nodeType === 1 && e !== document.documentElement; e = e.parentElement) {
      if (e.id) { parts.unshift(e.tagName.toLowerCase() + '#' + e.id); break; }
      var n = 1, s = e;
      while ((s = s.previousElementSibling)) if (s.tagName === e.tagName) n++;
      parts.unshift(e.tagName.toLowerCase() + ':' + n);
    }
    return parts.join('>');
  }
  function alpha(el) {
    var a = 1;
    for (var e = el; e && e.nodeType === 1; e = e.parentElement) a *= parseFloat(getComputedStyle(e).opacity);
    return a;
  }
  function textBox(el) {
    var l = 1e9, t = 1e9, r = -1e9, b = -1e9, text = '';
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType !== 3 || !n.textContent.trim()) continue;
      text += (text ? ' ' : '') + n.textContent.trim();
      var rg = document.createRange(); rg.selectNodeContents(n);
      var rs = rg.getClientRects();
      for (var i = 0; i < rs.length; i++) {
        if (rs[i].width <= 0 || rs[i].height <= 0) continue;
        l = Math.min(l, rs[i].left); t = Math.min(t, rs[i].top); r = Math.max(r, rs[i].right); b = Math.max(b, rs[i].bottom);
      }
    }
    return r > l ? {box: [l, t, r, b], text: text} : {box: null, text: text};
  }
  function consider(el, wantText) {
    if (seen.has(el) || SKIP[el.tagName] === 1) return;
    seen.add(el);
    var cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || alpha(el) < 0.02) return;
    var tb = textBox(el), box = tb.box, kind = 'text';
    var kid = el.getAttribute('data-kurgu-id');
    if (!box) {
      if (!kid && wantText) return;
      var rc = el.getBoundingClientRect();
      if (rc.width <= 0 || rc.height <= 0) return;
      box = [rc.left, rc.top, rc.right, rc.bottom]; kind = tb.text ? 'text' : 'element';
    }
    if (box[2] < 0 || box[3] < 0 || box[0] > vw || box[1] > vh) return;
    var src = el.getAttribute('data-kurgu-src') || '', file = '', line = 0, m = /^(.*):(\d+)$/.exec(src);
    if (m) { file = m[1]; line = +m[2]; } else file = src;
    out.push({key: kid ? 'id:' + kid : pathOf(el), kid: kid || '', htmlId: el.id || '', label: tb.text || kid || el.tagName.toLowerCase(),
              kind: kind, box: box, file: file, line: line});
  }
  var all = document.body ? document.body.querySelectorAll('*') : [];
  if (selector) {
    document.querySelectorAll(selector).forEach(function (e) { consider(e, false); });
    document.querySelectorAll('[data-kurgu-id]').forEach(function (e) { consider(e, false); });
  } else {
    for (var i = 0; i < all.length; i++) {
      var e = all[i];
      if (e.hasAttribute('data-kurgu-id')) { consider(e, false); continue; }
      for (var n = e.firstChild; n; n = n.nextSibling) if (n.nodeType === 3 && n.textContent.trim()) { consider(e, true); break; }
    }
  }
  document.querySelectorAll('canvas').forEach(function (c) {
    var r = c.getBoundingClientRect();
    if (r.width * r.height > 0.25 * vw * vh && getComputedStyle(c).display !== 'none') canvases++;
  });
  return {elements: out, canvases: canvases, vw: vw, vh: vh};
})
"""

# Injected before any page script (CDP Page.addScriptToEvaluateOnNewDocument): records what the page draws with the 2D canvas API
# (text, shapes, images) without any cooperation from the page. Nothing is executed from the page's source; the page is only observed.
_CANVAS_JS = r"""
(function () {
  if (window.__kurguCanvas) return;
  var PI2 = Math.PI * 2, gen = 1, order = 0, touched = [], store = new WeakMap(), paths = new WeakMap(), p2 = new WeakMap();
  var glList = [], cache = new Map(), RAW_CAP = 8000, MAX_SHAPES = 60, MIN_AREA = 0.0015, BG_AREA = 0.95, NEAR = 2, NEARP = 20, DUST = 0.00002;
  var API = window.__kurguCanvas = { on: true, reset: function () { gen++; order = 0; touched = []; }, read: read };

  function lst(cv, make) {
    var s = store.get(cv);
    if (!s || s.g !== gen) { if (!make) return null; s = {g: gen, l: []}; store.set(cv, s); touched.push(cv); }
    return s.l;
  }
  function put(cv, rec) { var l = lst(cv, true); if (l.length < RAW_CAP) { rec.o = ++order; rec.cv = cv; l.push(rec); } }
  function mat(c) { var m = c.getTransform(); return [m.a, m.b, m.c, m.d, m.e, m.f]; }
  function bnd(m, p) {
    var l = 1e18, t = 1e18, r = -1e18, b = -1e18;
    for (var i = 0; i < p.length; i += 2) {
      var x = p[i], y = p[i + 1], X = m ? m[0] * x + m[2] * y + m[4] : x, Y = m ? m[1] * x + m[3] * y + m[5] : y;
      if (X < l) l = X; if (X > r) r = X; if (Y < t) t = Y; if (Y > b) b = Y;
    }
    return [l, t, r, b];
  }
  function rp(x, y, w, h) { return [x, y, x + w, y, x + w, y + h, x, y + h]; }
  function ok(bb) { return isFinite(bb[0] + bb[1] + bb[2] + bb[3]) && bb[0] <= bb[2]; }
  function styleAlpha(s) {
    if (typeof s !== 'string') return 1;
    if (s === 'transparent') return 0;
    var m = /^(?:rgba|hsla)\(.*?[,\/]\s*([\d.]+)(%?)\s*\)$/.exec(s);
    if (m) return parseFloat(m[1]) / (m[2] ? 100 : 1);
    if (/^#[0-9a-f]{8}$/i.test(s)) return parseInt(s.slice(7), 16) / 255;
    return 1;
  }
  function hex2(n) { n = Math.max(0, Math.min(255, Math.round(+n))); return (n < 16 ? '0' : '') + n.toString(16); }
  function colorOf(s) {
    if (typeof s !== 'string') return typeof s === 'object' && s && s.addColorStop ? 'gradient' : 'pattern';
    var m = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/.exec(s);
    return m ? '#' + hex2(m[1]) + hex2(m[2]) + hex2(m[3]) : s;
  }
  function covers(bb, cv) { return bb[0] <= 1 && bb[1] <= 1 && bb[2] >= cv.width - 1 && bb[3] >= cv.height - 1; }

  // ---- paths: the bounds of the current path (canvas px) and of every Path2D (its own space)
  function newAcc() { return {l: 1e18, t: 1e18, r: -1e18, b: -1e18, ops: 0, hint: null}; }
  function opPts(n, a) {
    switch (n) {
      case 'moveTo': case 'lineTo': return [a[0], a[1]];
      case 'arc': var r = Math.abs(a[2]); return rp(a[0] - r, a[1] - r, 2 * r, 2 * r);
      case 'ellipse': var q = Math.max(Math.abs(a[2]), Math.abs(a[3])); return rp(a[0] - q, a[1] - q, 2 * q, 2 * q);
      case 'arcTo': case 'quadraticCurveTo': return [a[0], a[1], a[2], a[3]];
      case 'rect': case 'roundRect': return rp(a[0], a[1], a[2], a[3]);
      case 'bezierCurveTo': return [a[0], a[1], a[2], a[3], a[4], a[5]];
    }
    return null;
  }
  function hintOf(n, a) {
    if (n === 'arc') return Math.abs(a[4] - a[3]) >= PI2 - 1e-3 ? 'circle' : null;
    if (n === 'ellipse') return Math.abs(a[6] - a[5]) >= PI2 - 1e-3 ? (Math.abs(a[2] - a[3]) < 0.5 ? 'circle' : 'ellipse') : null;
    return n === 'rect' || n === 'roundRect' ? 'rect' : null;
  }
  function accAdd(acc, bb) {
    if (!ok(bb)) return false;
    acc.l = Math.min(acc.l, bb[0]); acc.t = Math.min(acc.t, bb[1]); acc.r = Math.max(acc.r, bb[2]); acc.b = Math.max(acc.b, bb[3]);
    return true;
  }
  function accOp(acc, m, n, a) {
    var p = opPts(n, a);
    if (!p || !accAdd(acc, bnd(m, p))) return;
    if (n !== 'moveTo') { acc.ops++; acc.hint = acc.ops === 1 ? hintOf(n, a) : null; }
  }

  // ---- records
  function recPath(ctx, args, stroke) {
    var a0 = args[0], m = mat(ctx), acc, pb;
    if (a0 && typeof a0 === 'object' && p2.has(a0)) {
      acc = p2.get(a0);
      if (acc.unknown || !(acc.r >= acc.l)) return;
      pb = bnd(m, rp(acc.l, acc.t, acc.r - acc.l, acc.b - acc.t));
    } else {
      acc = paths.get(ctx);
      if (!acc || !(acc.r >= acc.l)) return;
      pb = [acc.l, acc.t, acc.r, acc.b];
    }
    var style = stroke ? ctx.strokeStyle : ctx.fillStyle, al = ctx.globalAlpha * styleAlpha(style);
    if (al < 0.02 || !ok(pb)) return;
    var pad = stroke ? ctx.lineWidth / 2 * Math.hypot(m[0], m[1]) : 0;
    put(ctx.canvas, {k: 'shape', sk: acc.ops === 1 && acc.hint ? acc.hint : 'path', col: colorOf(style), st: stroke ? 's' : 'f',
                     l: pb[0] - pad, t: pb[1] - pad, r: pb[2] + pad, b: pb[3] + pad, a: al});
  }
  function recRect(ctx, x, y, w, h, stroke) {
    var m = mat(ctx), bb = bnd(m, rp(x, y, w, h)), cv = ctx.canvas, style = stroke ? ctx.strokeStyle : ctx.fillStyle;
    if (!ok(bb)) return;
    if (!stroke) {                                   // an opaque fill over the whole canvas starts that canvas over
      var s = lst(cv, false);
      if (s && s.length && typeof style === 'string' && covers(bb, cv) && ctx.globalAlpha >= 0.99 && styleAlpha(style) >= 0.99 && ctx.globalCompositeOperation === 'source-over') s.length = 0;
    }
    var al = ctx.globalAlpha * styleAlpha(style);
    if (al < 0.02) return;
    var pad = stroke ? ctx.lineWidth / 2 * Math.hypot(m[0], m[1]) : 0;
    put(cv, {k: 'shape', sk: 'rect', col: colorOf(style), st: stroke ? 's' : 'f', l: bb[0] - pad, t: bb[1] - pad, r: bb[2] + pad, b: bb[3] + pad, a: al});
  }
  function recText(ctx, text, x, y, stroke) {
    text = String(text);
    if (!text.trim() || !isFinite(x) || !isFinite(y)) return;
    var style = stroke ? ctx.strokeStyle : ctx.fillStyle, al = ctx.globalAlpha * styleAlpha(style);
    if (al < 0.02) return;
    var key = ctx.font + '|' + ctx.letterSpacing + '|' + ctx.textAlign + '|' + ctx.textBaseline + '|' + ctx.direction + '|' + text;
    var off = cache.get(key);
    if (!off) {
      var m = ctx.measureText(text), v = [m.actualBoundingBoxLeft, m.actualBoundingBoxRight, m.actualBoundingBoxAscent, m.actualBoundingBoxDescent];
      if (isFinite(v[0] + v[1] + v[2] + v[3])) off = [-v[0], -v[2], v[1], v[3], m.width];
      else {                                           // old engines: advance width + font size
        var fs = +((/([\d.]+)px/.exec(ctx.font) || [0, 16])[1]), w = m.width, al_ = ctx.textAlign, bl = ctx.textBaseline;
        var x0 = al_ === 'center' ? -w / 2 : al_ === 'right' || al_ === 'end' ? -w : 0;
        var y0 = bl === 'top' || bl === 'hanging' ? 0 : bl === 'middle' ? -fs / 2 : bl === 'bottom' || bl === 'ideographic' ? -fs : -0.8 * fs;
        off = [x0, y0, x0 + w, y0 + fs, w];
      }
      if (cache.size > 4000) cache.clear();
      cache.set(key, off);
    }
    var m2 = mat(ctx), bb = bnd(m2, rp(x + off[0], y + off[1], off[2] - off[0], off[3] - off[1]));
    if (!ok(bb)) return;
    var pad = stroke ? ctx.lineWidth / 2 * Math.hypot(m2[0], m2[1]) : 0, sc = Math.hypot(m2[0], m2[1]);
    var fm = /([\d.]+)px/.exec(ctx.font), em = (fm ? +fm[1] : (bb[3] - bb[1])) * sc;
    var ta = ctx.textAlign, xs = x - (ta === 'center' ? 0.5 : ta === 'right' || ta === 'end' ? 1 : 0) * off[4], xe = xs + off[4];   // the advance, for gaps between glyphs
    put(ctx.canvas, {k: 'text', text: text, l: bb[0] - pad, t: bb[1] - pad, r: bb[2] + pad, b: bb[3] + pad, a: al,
                     xs: m2[0] * xs + m2[2] * y + m2[4], xe: m2[0] * xe + m2[2] * y + m2[4], ax: m2[0] * x + m2[2] * y + m2[4], ay: m2[1] * x + m2[3] * y + m2[5], em: em, rot: Math.atan2(m2[1], m2[0]),
                     fk: ctx.font + '|' + ctx.textBaseline + '|' + ctx.letterSpacing, col: colorOf(style)});
  }
  function baseOf(img) {
    var s = '';
    try { s = img.currentSrc || img.src || ''; } catch (e) {}
    if (!s || /^(data|blob):/.test(s)) return 'image';
    s = s.split('#')[0].split('?')[0];
    return decodeURIComponent(s.slice(s.lastIndexOf('/') + 1)) || 'image';
  }
  function sig(r) { return r.k + '|' + (r.text || r.col + r.sk + r.st + (r.lbl || '')); }
  function iou(a, b) {
    var w = Math.min(a.r, b.r) - Math.max(a.l, b.l), h = Math.min(a.b, b.b) - Math.max(a.t, b.t);
    if (w <= 0 || h <= 0) return 0;
    var i = w * h, u = (a.r - a.l) * (a.b - a.t) + (b.r - b.l) * (b.b - b.t) - i;
    return u > 0 ? i / u : 1;
  }
  function recImage(ctx, a) {
    var img = a[0], n = a.length;
    var isCv = (typeof HTMLCanvasElement !== 'undefined' && img instanceof HTMLCanvasElement) || (typeof OffscreenCanvas !== 'undefined' && img instanceof OffscreenCanvas);
    var W = img.videoWidth || img.naturalWidth || img.width, H = img.videoHeight || img.naturalHeight || img.height;
    if (typeof W !== 'number' || typeof H !== 'number' || !W || !H) return;
    var sx = 0, sy = 0, sw = W, sh = H, dx, dy, dw, dh;
    if (n >= 9) { sx = a[1]; sy = a[2]; sw = a[3]; sh = a[4]; dx = a[5]; dy = a[6]; dw = a[7]; dh = a[8]; }
    else if (n >= 5) { dx = a[1]; dy = a[2]; dw = a[3]; dh = a[4]; }
    else { dx = a[1]; dy = a[2]; dw = W; dh = H; }
    if (!sw || !sh || !isFinite(dx + dy + dw + dh + sx + sy + sw + sh)) return;
    var m = mat(ctx), cv = ctx.canvas, al = ctx.globalAlpha;
    if (isCv && img !== cv) {
      var src = lst(img, false);
      if (src && src.length) {                         // the source canvas holds recorded text/shapes: carry them over (any scale/offset/rotation)
        var kx = dw / sw, ky = dh / sh, e0 = dx - sx * kx, f0 = dy - sy * ky;
        var M = [m[0] * kx, m[1] * kx, m[2] * ky, m[3] * ky, m[0] * e0 + m[2] * f0 + m[4], m[1] * e0 + m[3] * f0 + m[5]];
        var snap = src.slice(), dest = lst(cv, true);
        for (var i = 0; i < snap.length; i++) {
          var r = snap[i];
          if (r.r < sx || r.l > sx + sw || r.b < sy || r.t > sy + sh) continue;
          var bb = bnd(M, rp(r.l, r.t, r.r - r.l, r.b - r.t));
          if (!ok(bb)) continue;
          r.used = true;
          var c = Object.assign({}, r, {l: bb[0], t: bb[1], r: bb[2], b: bb[3], a: r.a * al, used: false}), s = sig(c), dup = null;
          if (r.k === 'text') {                         // the glyph anchor, size and rotation follow the same map
            c.xs = M[0] * r.xs + M[2] * r.ay + M[4]; c.xe = M[0] * r.xe + M[2] * r.ay + M[4];
            c.ax = M[0] * r.ax + M[2] * r.ay + M[4]; c.ay = M[1] * r.ax + M[3] * r.ay + M[5];
            c.em = r.em * Math.hypot(M[0], M[1]); c.rot = r.rot + Math.atan2(M[1], M[0]);
          }
          for (var j = dest.length - 1; j >= 0 && j >= dest.length - 60; j--) if (sig(dest[j]) === s && iou(dest[j], c) > 0.5) { dup = dest[j]; break; }
          if (dup) { dup.l = c.l; dup.t = c.t; dup.r = c.r; dup.b = c.b; dup.a = Math.max(dup.a, c.a); dup.ax = c.ax; dup.ay = c.ay; dup.xs = c.xs; dup.xe = c.xe; dup.em = c.em; dup.rot = c.rot; }
          else put(cv, c);
        }
        return;
      }
    }
    if (al < 0.02) return;
    var bi = bnd(m, rp(dx, dy, dw, dh));
    if (ok(bi)) put(cv, {k: 'image', sk: 'image', lbl: isCv ? 'canvas' : (img.videoWidth ? 'video' : baseOf(img)), l: bi[0], t: bi[1], r: bi[2], b: bi[3], a: al});
  }

  // ---- installing the wrappers
  function wrap(P, n, after) {
    var o = P[n];
    if (typeof o !== 'function') return;
    P[n] = function () {
      var r = o.apply(this, arguments);
      if (API.on) { try { after(this, arguments); } catch (e) {} }
      return r;
    };
  }
  function install(P) {
    ['moveTo', 'lineTo', 'arc', 'arcTo', 'ellipse', 'rect', 'roundRect', 'quadraticCurveTo', 'bezierCurveTo'].forEach(function (n) {
      wrap(P, n, function (c, a) { var acc = paths.get(c); if (!acc) paths.set(c, acc = newAcc()); accOp(acc, mat(c), n, a); });
    });
    wrap(P, 'beginPath', function (c) { paths.delete(c); });
    wrap(P, 'reset', function (c) { paths.delete(c); });
    wrap(P, 'fill', function (c, a) { recPath(c, a, false); });
    wrap(P, 'stroke', function (c, a) { recPath(c, a, true); });
    wrap(P, 'fillRect', function (c, a) { recRect(c, a[0], a[1], a[2], a[3], false); });
    wrap(P, 'strokeRect', function (c, a) { recRect(c, a[0], a[1], a[2], a[3], true); });
    wrap(P, 'clearRect', function (c, a) {
      var cv = c.canvas, s = lst(cv, false);
      if (s && s.length && covers(bnd(mat(c), rp(a[0], a[1], a[2], a[3])), cv)) s.length = 0;
    });
    wrap(P, 'fillText', function (c, a) { recText(c, a[0], +a[1], +a[2], false); });
    wrap(P, 'strokeText', function (c, a) { recText(c, a[0], +a[1], +a[2], true); });
    wrap(P, 'drawImage', recImage);
  }
  ['CanvasRenderingContext2D', 'OffscreenCanvasRenderingContext2D'].forEach(function (nm) { if (window[nm]) install(window[nm].prototype); });

  if (window.Path2D) {
    var Orig = window.Path2D, PP = Orig.prototype;
    ['moveTo', 'lineTo', 'arc', 'arcTo', 'ellipse', 'rect', 'roundRect', 'quadraticCurveTo', 'bezierCurveTo'].forEach(function (n) {
      wrap(PP, n, function (p, a) { var acc = p2.get(p); if (!acc) p2.set(p, acc = newAcc()); accOp(acc, null, n, a); });
    });
    wrap(PP, 'addPath', function (p, a) {
      var o = p2.get(a[0]), acc = p2.get(p) || (p2.set(p, newAcc()), p2.get(p)), mm = a[1];
      if (!o || o.unknown || !(o.r >= o.l)) { acc.unknown = true; return; }
      accAdd(acc, bnd(mm ? [mm.a, mm.b, mm.c, mm.d, mm.e, mm.f] : null, rp(o.l, o.t, o.r - o.l, o.b - o.t)));
      acc.ops += 2; acc.hint = null;
    });
    try {
      window.Path2D = class extends Orig {            // paths made from another path or an SVG string
        constructor(a) {
          super(a);
          if (a instanceof Orig) { var s = p2.get(a); p2.set(this, s ? Object.assign({}, s) : Object.assign(newAcc(), {unknown: true})); }
          else if (typeof a === 'string') p2.set(this, Object.assign(newAcc(), {unknown: true}));
        }
      };
    } catch (e) {}
  }
  if (window.HTMLCanvasElement) {
    var gc = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (t) {
      try { if (/webgl|webgpu/i.test(String(t))) glList.push(this); } catch (e) {}
      return gc.apply(this, arguments);
    };
  }

  // ---- reading one frame: records of visible canvases -> page px, merged and filtered
  function visInfo(cv) {
    if (typeof HTMLCanvasElement === 'undefined' || !(cv instanceof HTMLCanvasElement) || !cv.isConnected) return null;   // detached / offscreen
    var rect = cv.getBoundingClientRect(), HIDE = {hide: true};
    if (rect.width <= 0 || rect.height <= 0 || !cv.width || !cv.height) return HIDE;
    var op = 1;
    for (var e = cv; e && e.nodeType === 1; e = e.parentElement) {
      var cs = getComputedStyle(e);
      if (cs.display === 'none') return HIDE;
      op *= parseFloat(cs.opacity);
    }
    if (op < 0.02 || getComputedStyle(cv).visibility === 'hidden') return HIDE;
    return {rect: rect, op: op, sx: rect.width / cv.width, sy: rect.height / cv.height};
  }
  function touch(g, b) {
    var d = g.sm && b.sm ? NEARP : NEAR;           // specks (each far below the size floor) gather from further away than big shapes
    return b.l <= g.r + d && b.r >= g.l - d && b.t <= g.b + d && b.b >= g.t - d;
  }
  function area(b) { return Math.max(0, b.r - b.l) * Math.max(0, b.b - b.t); }
  function read() {
    var vw = innerWidth, vh = innerHeight, vis = [], approx = {}, i, j, r;
    NEARP = 0.15 * Math.min(vw, vh);
    for (i = 0; i < touched.length; i++) {
      var cv = touched[i], l = lst(cv, false);
      if (!l) continue;
      var v = visInfo(cv);
      if (!v) { for (j = 0; j < l.length; j++) if (l[j].k === 'text' && !l[j].used) approx[l[j].text] = 1; continue; }
      if (v.hide) continue;
      for (j = 0; j < l.length; j++) {
        r = l[j];
        var a = r.a * v.op;
        if (a < 0.02) continue;
        vis.push({k: r.k, sk: r.sk, text: r.text, col: r.col, st: r.st, lbl: r.lbl, o: r.o, cv: cv, fk: r.fk, rot: r.rot,
                  em: (r.em || 0) * v.sx, xs: r.xs === undefined ? r.l : v.rect.left + r.xs * v.sx, xe: r.xe === undefined ? r.r : v.rect.left + r.xe * v.sx, ax: r.ax === undefined ? 0 : v.rect.left + r.ax * v.sx, ay: r.ay === undefined ? 0 : v.rect.top + r.ay * v.sy, l: v.rect.left + r.l * v.sx, t: v.rect.top + r.t * v.sy,
                  r: v.rect.left + r.r * v.sx, b: v.rect.top + r.b * v.sy});
      }
    }
    vis.sort(function (x, y) { return x.o - y.o; });
    var items = [], seenText = {}, nText = {}, tx = [], groups = {}, gl = 0, VA = vw * vh;
    for (i = 0; i < vis.length; i++) {
      r = vis[i];
      if (r.r < 0 || r.b < 0 || r.l > vw || r.t > vh) continue;
      if (r.k === 'text') {
        var dk = r.text + '|' + Math.round(r.l / 3) + '|' + Math.round(r.t / 3) + '|' + Math.round(r.r / 3) + '|' + Math.round(r.b / 3);
        if (seenText[dk]) continue;                    // fillText + strokeText of the same line
        seenText[dk] = 1;
        tx.push(r);
        continue;
      }
      if (area(r) >= BG_AREA * VA) continue;           // a full-frame background fill / picture
      if (area(r) < DUST * VA) continue;             // film grain and dust: never an element
      r.sm = area(r) < MIN_AREA * VA;
      var ident = r.k === 'image' ? 'image|' + r.lbl : 'shape|' + r.col + '|' + r.st, gs = groups[ident] || (groups[ident] = []), hit = null;
      for (j = 0; j < gs.length; j++) if (touch(gs[j], r)) { hit = gs[j]; break; }
      if (hit) { hit.l = Math.min(hit.l, r.l); hit.t = Math.min(hit.t, r.t); hit.r = Math.max(hit.r, r.r); hit.b = Math.max(hit.b, r.b); hit.cnt++; hit.sm = hit.sm && r.sm; }
      else gs.push({sm: area(r) < MIN_AREA * VA, ident: ident, k: r.k, sk: r.sk, col: r.col, st: r.st, lbl: r.lbl, l: r.l, t: r.t, r: r.r, b: r.b, o: r.o, cnt: 1});
    }
    // glyph by glyph (or word by word) text on one line becomes one element: same canvas, font, fill, rotation, baseline (+-0.15 em), gap < 0.6 em
    var mg = [];
    tx.forEach(function (t) {
      var last = mg[mg.length - 1];
      if (last && last.cv === t.cv && last.fk === t.fk && last.col === t.col && Math.abs(last.rot) < 0.02 && Math.abs(t.rot) < 0.02 && last.em > 0
          && Math.abs(t.ay - last.ay) <= 0.8 * last.em) {   // reveal letters rise from below: a line is read left to right, so the y band is wide
        var gap = t.xs - last.xe;
        if (gap < 0.6 * last.em && gap > -0.3 * last.em) {
          last.text += (gap > 0.25 * last.em ? ' ' : '') + t.text;
          last.l = Math.min(last.l, t.l); last.t = Math.min(last.t, t.t); last.r = Math.max(last.r, t.r); last.b = Math.max(last.b, t.b); last.ay = t.ay; last.xe = t.xe;
          return;
        }
      }
      mg.push(Object.assign({}, t));
    });
    mg.forEach(function (r) {
      var n = nText[r.text] = (nText[r.text] === undefined ? 0 : nText[r.text] + 1);
      items.push({kind: 'text', ident: r.text, n: n, label: r.text, box: [r.l, r.t, r.r, r.b], o: r.o});
    });
    var all = [];
    Object.keys(groups).forEach(function (id) {
      var gs = groups[id], again = true, pass = 0;
      while (again && pass++ < 3) {                    // a group that grew may now touch another one
        again = false;
        for (var x = 0; x < gs.length; x++) for (var y = gs.length - 1; y > x; y--) if (touch(gs[x], gs[y])) {
          var A = gs[x], B = gs[y];
          A.l = Math.min(A.l, B.l); A.t = Math.min(A.t, B.t); A.r = Math.max(A.r, B.r); A.b = Math.max(A.b, B.b); A.cnt += B.cnt; A.sm = A.sm && B.sm; A.o = Math.min(A.o, B.o);
          gs.splice(y, 1); again = true;
        }
      }
      all = all.concat(gs);
    });
    all.sort(function (x, y) { return x.o - y.o; });
    var nth = {}, shapes = [];
    all.forEach(function (g) {
      g.n = nth[g.ident] = (nth[g.ident] === undefined ? 0 : nth[g.ident] + 1);
      if (area(g) < MIN_AREA * VA) return;
      var w = Math.round(g.r - g.l), h = Math.round(g.b - g.t), label;
      if (g.k === 'image') label = 'image ' + g.lbl + (g.cnt > 1 ? ' x' + g.cnt : '');
      else if (g.cnt > 1) label = 'shapes ' + g.col + (g.st === 's' ? ' (stroke)' : '') + ' x' + g.cnt;
      else label = g.sk + ' ' + g.col + (g.st === 's' ? ' (stroke)' : '') + (g.sk === 'circle' ? ' r≈' + Math.round(w / 2) : g.sk === 'rect' ? ' ' + w + '×' + h : '');
      shapes.push({kind: g.k, ident: g.ident, n: g.n, label: label, box: [g.l, g.t, g.r, g.b], o: g.o, area: area(g)});
    });
    if (shapes.length > MAX_SHAPES) shapes = shapes.sort(function (x, y) { return y.area - x.area; }).slice(0, MAX_SHAPES);
    glList.forEach(function (c) { var v = visInfo(c); if (v && !v.hide) gl++; });
    return {items: items.concat(shapes), approx: Object.keys(approx), gl: gl};
  }
})();
"""

_CANVAS_READ_JS = "(function () { return window.__kurguCanvas && window.__kurguCanvas.on ? window.__kurguCanvas.read() : null; })"

_DRAW_JS = """(async function (i) {
  var f = window.renderFrame || window.captureFrame;
  if (typeof f !== 'function') throw new Error('the page has no window.renderFrame(i) or window.captureFrame(i)');
  if (window.__kurguCanvas) window.__kurguCanvas.reset();
  await f(i);
  return true;
})"""

# Opt-in hook for canvas engines: window.kurguElements(i) -> [{id, label, kind, l, t, r, b, file?, line?}] in page px, called after renderFrame(i)
_HOOK_JS = """(async function (i) {
  if (typeof window.kurguElements !== 'function') return null;
  var r = await window.kurguElements(i);
  return Array.isArray(r) ? r : [];
})"""

_READY_JS = """(async function () {
  var info = {};
  for (var k = 0; k < 100 && !window.READY && typeof window.renderFrame !== 'function' && typeof window.captureFrame !== 'function'; k++)
    await new Promise(function (r) { setTimeout(r, 100); });      // a module script may define them a moment after load
  if (window.READY && typeof window.READY.then === 'function') {
    var r = await window.READY;
    if (r && typeof r === 'object') { info.fps = r.fps; info.width = r.width; info.height = r.height; info.frames = r.frames; }
  }
  info.fn = typeof window.renderFrame === 'function' || typeof window.captureFrame === 'function';
  return info;
})()"""


def _iou(a, b):
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    i = w * h
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 1.0


def _overlap_small(a, b):
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / small if small > 0 else 1.0


def _drop_canvas_duplicates(elements):
    """The page's own kurguElements() report wins over what the canvas capture saw at the same place."""
    reported = [e for e in elements if e.get("hook")]
    if not reported:
        return elements
    return [e for e in elements if not (e.get("cv") and any(
        e["kind"] == r["kind"] and _iou(e["box"], r["box"]) > (0.3 if e["label"] == r["label"] else 0.6) for r in reported))]


# ---------------------------------------------------------------- source lines (text only; the page's files are read, never run)
SRC_EXT = (".html", ".htm", ".js", ".mjs", ".cjs", ".json")
SRC_SKIP_DIRS = {"node_modules", ".git", "__pycache__", "renders", "dist", "build"}
SRC_MAX_BYTES = 1_500_000
SRC_MAX_FILES = 400


def _source_files(page, source_root=None):
    """[(relative path, text)]: the page itself first, then same-folder (2 levels) and source-root (4 levels) html/js/mjs/json."""
    if re.match(r"^[a-z]+://", page or ""):
        return []
    page = os.path.abspath(os.path.expanduser(page))
    base = os.path.dirname(page)
    root_abs = os.path.abspath(os.path.expanduser(source_root)) if source_root else None
    out, seen = [], set()

    def add(path):
        path = os.path.abspath(path)
        if path in seen or len(out) >= SRC_MAX_FILES or not path.lower().endswith(SRC_EXT) or path.endswith(".kurgu.json"):
            return
        seen.add(path)
        try:
            if os.path.getsize(path) > SRC_MAX_BYTES:
                return
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return
        rel = os.path.basename(path)
        for anchor in (base, root_abs):
            if anchor and os.path.commonpath([anchor, path]) == anchor:
                rel = os.path.relpath(path, anchor).replace(os.sep, "/")
                break
        out.append((rel, text))

    add(page)
    for root_dir, depth in ((base, 2), (root_abs, 4)):
        if not root_dir or not os.path.isdir(root_dir):
            continue
        for d, dirs, files in os.walk(root_dir):
            level = os.path.relpath(d, root_dir).count(os.sep) + (0 if d == root_dir else 1)
            dirs[:] = sorted(x for x in dirs if x not in SRC_SKIP_DIRS and not x.startswith(".")) if level < depth else []
            for fn in sorted(files):
                add(os.path.join(d, fn))
    return out


def _attach_dom_sources(page, elements, source_root=None):
    """DOM text elements (not drawn into a canvas) get file:line from the page's sources: the literal text first, else the element's id in its markup / getElementById."""
    todo = [e for e in elements if e.get("kind") == "text" and not e.get("file") and not str(e.get("id", "")).startswith(("cv-", "e-"))]
    if not todo:
        return
    _attach_sources(page, [(e, e.get("label", "").rstrip("…")) for e in todo if len(e.get("label", "").rstrip("…").strip()) >= 4], source_root)
    rest = [e for e in todo if not e.get("file")]
    files = _source_files(page, source_root) if rest else []
    for e in rest:
        pat = re.compile(r"""\bid\s*=\s*["']""" + re.escape(str(e["id"])) + r"""["']|getElementById\(\s*["']""" + re.escape(str(e["id"])) + r"""["']\s*\)""")
        for rel, body in files:
            m = pat.search(body)
            if m:
                e["file"], e["line"] = rel, body.count("\n", 0, m.start()) + 1
                break


def clip_to_frames(res, lo, hi):
    """The element map `res` limited to frames [lo, hi): a unit's map must not carry boxes of other units. Elements left without boxes are dropped."""
    keep = []
    for e in res.get("elements", []):
        boxes = [b for b in e.get("boxes", []) if lo <= b["f"] < hi]
        if boxes:
            keep.append(dict(e, boxes=boxes))
    res["elements"] = keep
    fs = [b["f"] for e in keep for b in e["boxes"]]
    if fs and isinstance(res.get("sampled"), list) and len(res["sampled"]) == 3:
        res["sampled"] = [min(fs), max(fs), res["sampled"][2]]
    return res


def _attach_sources(page, items, source_root=None):
    """items: [(element dict, full text)]. Sets file/line when the literal text is in the page's sources; line_ambiguous when it appears more than once."""
    if not items:
        return
    files = _source_files(page, source_root)
    if not files:
        return
    starts = {}
    for el, text in items:
        t = text.strip()
        if len(t) < 2:
            continue
        variants = {t, t.replace("\\", "\\\\").replace("'", "\\'"), t.replace("\\", "\\\\").replace('"', '\\"'),
                    json.dumps(t, ensure_ascii=False)[1:-1], t.replace("`", "\\`")}
        hits = set()
        for fi, (_, body) in enumerate(files):
            for v in variants:
                i = body.find(v)
                while i != -1:
                    hits.add((fi, i))
                    i = body.find(v, i + max(1, len(v)))
        if not hits:
            continue
        fi, idx = min(hits)
        body = files[fi][1]
        el["file"] = files[fi][0]
        el["line"] = body.count("\n", 0, idx) + 1
        if len(hits) > 1:
            el["line_ambiguous"] = True


_FRAME_JS = ("(async function (i, selector, useCanvas) { await (" + _DRAW_JS + ")(i); var got = (" + _COLLECT_JS + ")(selector); "
             "var cv = useCanvas ? (" + _CANVAS_READ_JS + ")() : null; var hook = await (" + _HOOK_JS + ")(i); return {got: got, cv: cv, hook: hook}; })")


def _common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _track_ids(items, prev):
    """Ids for this frame's canvas items: an item takes the id of the previous frame's item of the same kind that overlaps it most (IoU > 0.3,
    same style for shapes, same leading characters for text); otherwise a hash of its identity. Returns the ids in the order of `items`."""
    pairs = []
    for i, it in enumerate(items):
        for j, pv in enumerate(prev):
            if it["kind"] != pv["kind"]:
                continue
            if it["kind"] == "text":
                if _common_prefix(it["label"], pv["label"]) < min(3, len(it["label"]), len(pv["label"])):
                    continue
            elif it["ident"] != pv["ident"]:
                continue
            v = _iou(it["box"], pv["box"])
            if it["kind"] == "text":                       # a word that grows letter by letter: overlap against the smaller box
                v = max(v, _overlap_small(it["box"], pv["box"]) * 0.99 if _overlap_small(it["box"], pv["box"]) >= 0.6 else 0)
            if v > 0.3:
                pairs.append((v, i, j))
    pairs.sort(key=lambda x: -x[0])
    ids, taken = [None] * len(items), set()
    for _, i, j in pairs:
        if ids[i] is None and j not in taken:
            ids[i] = prev[j]["cid"]
            taken.add(j)
    used = {x for x in ids if x}
    for i, it in enumerate(items):
        if ids[i] is None:
            base = "cv-" + hashlib.sha1(f"{it['ident']}#{it['n']}".encode()).hexdigest()[:6]
            cid, k = base, 2
            while cid in used:
                cid, k = f"{base}-{k}", k + 1
            ids[i] = cid
            used.add(cid)
    _track_ids.last = ids
    return ids


def _flush(st, offset):
    if st["pending"] is not None:
        f, b = st["pending"]
        st["boxes"].append({"f": int(f) + offset, "l": b[0], "t": b[1], "r": b[2], "b": b[3]})
        st["pending"] = None


def _short(text, n=60):
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def _slug(s):
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")
    return s[:40] or "el"


MAX_GAP = 12      # frames: a visible element gets a box at least this often (the reader treats an element as visible about one sampling gap around its boxes)


def measure(page, frames, size=None, fps=None, selector=None, params="", browser=None, on_progress=None, offset=0, canvas=True, source_root=None, root=None):
    """Measure `page` at `frames`. Returns the element-map dict (see module doc). `size` = (W, H) of the VIDEO (boxes are scaled to it);
    default: the page's own size (what READY reports), else 1920x1080.
    `canvas` (default on) records text, shapes and images the page draws with the 2D canvas API; `source_root` is an extra folder searched for the literal text (source lines).
    `offset` is added to every written frame number: a page that renders a scene's own frames 0..n writes the video's absolute frames. A `browser` may be passed in to reuse one Chrome across calls."""
    frames = list(frames)
    remote = bool(re.match(r"^https?://", page))
    if not remote:
        if page.startswith("file://"):
            page = urllib.parse.unquote(urllib.parse.urlsplit(page).path)
        local = os.path.abspath(os.path.expanduser(page))
        if not os.path.isfile(local):
            raise ElementsError(f"page not found: {page}")
        root = os.path.abspath(os.path.expanduser(root)) if root else os.path.dirname(local)
    own = browser is None
    t0 = time.time()
    if own:
        browser = Browser(*(size or (1920, 1080)), root=None if remote else root)
    if remote:
        url = page_url(page, params)
    else:
        if not browser.server or not browser.server.contains(local):
            if own:
                browser.close()
            raise ElementsError(f"the page {os.path.basename(local)} is outside the folder this browser may serve")
        url = browser.server.url_for(local, params)
    try:
        vw, vh = size or (1920, 1080)
        browser.call("Emulation.setDeviceMetricsOverride", width=vw, height=vh, deviceScaleFactor=1, mobile=False)
        if canvas and not getattr(browser, "_canvas_hook", False):
            browser.call("Page.addScriptToEvaluateOnNewDocument", source=_CANVAS_JS)       # runs before any page script
            browser._canvas_hook = True
        browser.goto(url)
        if browser.eval("!!window.__kurguCanvas"):
            browser.eval(f"window.__kurguCanvas.on = {'true' if canvas else 'false'}")
        try:
            info = browser.eval(_READY_JS, timeout_ms=120000)
        except ElementsError as e:
            raise ElementsError(f"the page's READY failed: {e}")
        if not info.get("fn"):
            raise ElementsError("the page has no window.renderFrame(i) or window.captureFrame(i): this tool measures frame-function pages")
        if info.get("width") and info.get("height") and (int(info["width"]), int(info["height"])) != (vw, vh):
            vw, vh = int(info["width"]), int(info["height"])      # the page's own picture size is the layout size; boxes are scaled to the video size below
            browser.call("Emulation.setDeviceMetricsOverride", width=vw, height=vh, deviceScaleFactor=1, mobile=False)
        video_w, video_h = size or (vw, vh)
        fps_v = fps or info.get("fps") or None
        warnings = []
        if not fps_v:
            fps_v = 30
            warnings.append("fps unknown (the page's READY gave none, no --fps): 30 assumed")
        states = {}               # key -> dict(id, label, kind, file, line, boxes, last, present)
        order = []
        canvas_frames = 0
        has_hook = False
        text_frames = 0
        gl_seen = False
        approx_labels = set()
        cv_hits = 0
        prev_cv = []
        for n, f in enumerate(frames):
            # one round trip per frame (every evaluate costs ~10 ms of DevTools latency): draw, measure the DOM, read the canvas records, call the hook
            res_f = browser.eval(f"({_FRAME_JS})({int(f)},{json.dumps(selector)},{'true' if canvas else 'false'})", timeout_ms=120000)
            got, cvres, hook = res_f["got"], res_f["cv"], res_f["hook"]
            if cvres:
                gl_seen = gl_seen or bool(cvres.get("gl"))
                approx_labels.update(cvres.get("approx") or [])
                cur_items = [it for it in cvres["items"] if not (it["box"][2] < 0 or it["box"][3] < 0 or it["box"][0] > got["vw"] or it["box"][1] > got["vh"])]
                for it, cid in zip(cur_items, _track_ids(cur_items, prev_cv)):
                    got["elements"].append({"key": "id:" + cid, "kid": cid, "htmlId": "", "label": it["label"], "kind": it["kind"], "box": it["box"],
                                            "file": "", "line": 0, "cv": True})
                    cv_hits += 1
                prev_cv = [dict(it, cid=cid) for it, cid in zip(cur_items, _track_ids.last)]
            else:
                prev_cv = []
            if hook is not None:
                has_hook = True
                by_key = {e["key"]: k for k, e in enumerate(got["elements"])}
                for h in hook:
                    try:
                        box = [float(h["l"]), float(h["t"]), float(h["r"]), float(h["b"])]
                        hid = str(h["id"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    ent = {"key": "id:" + hid, "kid": hid, "htmlId": "", "label": str(h.get("label") or hid), "kind": str(h.get("kind") or "text"),
                           "box": box, "file": str(h.get("file") or ""), "line": int(h.get("line") or 0), "hook": True}
                    if ent["key"] in by_key:
                        got["elements"][by_key[ent["key"]]] = ent
                    else:
                        got["elements"].append(ent)
            if hook:
                got["elements"] = _drop_canvas_duplicates(got["elements"])
            sx = video_w / got["vw"] if got["vw"] else 1.0
            sy = video_h / got["vh"] if got["vh"] else 1.0
            present = set()
            if got["elements"]:
                text_frames += 1
            elif got["canvases"]:
                canvas_frames += 1
            for e in got["elements"]:
                key = e["key"]
                present.add(key)
                st = states.get(key)
                if st is not None and st["cv"] and st["kind"] == "text" and len(e["label"]) > len(st["text"]):
                    st["text"], st["label"] = e["label"], _short(e["label"])      # a letter-by-letter reveal: the label grows to the whole word
                if st is None:
                    st = states[key] = {"key": key, "kid": e["kid"], "html_id": e["htmlId"], "label": _short(e["label"]), "kind": e["kind"],
                                        "file": e["file"], "line": e["line"], "boxes": [], "last": None, "last_f": 0, "pending": None,
                                        "cv": bool(e.get("cv")), "text": e["label"], "nf": 0}
                    order.append(key)
                st["nf"] += 1
                box = [int(round(e["box"][0] * sx)), int(round(e["box"][1] * sy)), int(round(e["box"][2] * sx)), int(round(e["box"][3] * sy))]
                if st["last"] is None or max(abs(a - b) for a, b in zip(box, st["last"])) > 1 or (f - st["last_f"]) + (f - frames[n - 1] if n else 0) > MAX_GAP:
                    st["boxes"].append({"f": int(f) + offset, "l": box[0], "t": box[1], "r": box[2], "b": box[3]})
                    st["last"], st["last_f"], st["pending"] = box, f, None
                else:
                    st["pending"] = (f, box)               # same box as the last written one: written only if it turns out to be the last visible frame
            for key in order:
                st = states[key]
                if key not in present and st["last"] is not None:
                    _flush(st, offset)
                    st["last"] = None
            if on_progress:
                on_progress(n + 1, len(frames))
        for st in states.values():
            _flush(st, offset)                              # the last visible frame of every element gets a box
        # ids: data-kurgu-id, else the html id, else a stable hash of the DOM position; unique across the file
        used = set()
        elements = []
        st_text = []
        if len(frames) >= 3:                                   # sparkle: a shape that lives for fewer than 3 sampled frames is noise
            order = [k for k in order if not (states[k]["cv"] and states[k]["kind"] == "shape" and states[k]["nf"] < 3)]
        for key in order:
            st = states[key]
            base = st["kid"] or st["html_id"] or "e-" + hashlib.sha1(key.encode()).hexdigest()[:6]
            eid, k = base, 2
            while eid in used:
                eid, k = f"{base}-{k}", k + 1
            used.add(eid)
            el = {"id": eid, "label": st["label"], "kind": st["kind"]}
            if st["file"]:
                el["file"] = st["file"]
                if st["line"]:
                    el["line"] = st["line"]
            el["boxes"] = st["boxes"]
            if st["cv"] and st["kind"] == "text":
                st_text.append((el, st["text"]))
            elements.append(el)
        _attach_sources(page, st_text, source_root)
        _attach_dom_sources(page, elements, source_root)
        boxed = {e["label"] for e in elements if e["boxes"]}
        for tx in sorted(approx_labels):
            if _short(tx) not in boxed:                        # text only ever drawn off screen / into a canvas that was not drawn 1:1: no position
                elements.append({"id": "cv-" + hashlib.sha1((tx + "#approx").encode()).hexdigest()[:6], "label": _short(tx), "kind": "text",
                                 "approx": True, "boxes": []})
        if gl_seen:
            warnings.append("a WebGL / 3D canvas is visible: text, shapes and objects drawn with WebGL or three.js cannot be captured generically. "
                            "Report them with window.kurguElements(i) (see \"for engine authors\" in the README) or use the OCR / vision fallback")
        elif not elements and canvas_frames and not has_hook:
            warnings.append("no DOM text or data-kurgu-id elements found, but the page draws into a <canvas> in a way that was not captured (2D text, shapes and images are; "
                            "pixels, WebGL and 3D are not). Let the page report its own layout: define window.kurguElements(i) returning [{id,label,l,t,r,b}] in page px "
                            "(see \"for engine authors\" in the README), or mark DOM elements with data-kurgu-id")
        elif canvas_frames:
            warnings.append(f"in {canvas_frames} of {len(frames)} frames only a <canvas> was visible and nothing was captured from it"
                            + ("" if has_hook else " (a page can report it with window.kurguElements(i))"))
        out = {"fps": fps_v, "size": [video_w, video_h], "measured": "dom", "sampled": [frames[0], frames[-1], len(frames)],
               "elements": elements}
        if warnings:
            out["warnings"] = warnings
        out["seconds"] = round(time.time() - t0, 2)
        return out
    finally:
        if own:
            browser.close()


# ---------------------------------------------------------------- CLI
def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="kurgu elements", description=(
        "Measure where text and marked objects are in a video made by a web page. The page must expose window.renderFrame(i) "
        "(or captureFrame); it is opened headless, drawn at the given frames, and the boxes of visible DOM text and of elements "
        "with data-kurgu-id are written as an element map (*.kurgu.json). Text, shapes and images drawn with the 2D canvas API are captured too (--no-canvas turns it off); "
        "WebGL / three.js output is pixels: report it with window.kurguElements(i), or use `kurgu detect` (OCR) on the rendered video."))
    ap.add_argument("page", help="path or URL of the frame-function HTML page")
    ap.add_argument("--frames", required=True, help="frames to draw: a list (0,24,48) or a range a:b:step (b inclusive)")
    ap.add_argument("-o", "--output", default=None, help="element map path (default: <page name>.kurgu.json next to a local page, else ./elements.kurgu.json)")
    ap.add_argument("--size", default=None, help="video size WxH the boxes are scaled to (default: what the page reports, else 1920x1080)")
    ap.add_argument("--fps", type=float, default=None, help="fps to record (default: the page's READY value)")
    ap.add_argument("--selector", default=None, help="CSS selector of the elements to measure (default: elements with their own text; data-kurgu-id ones are always included)")
    ap.add_argument("--params", default="", help='query string added to the page URL, e.g. "w=1080&h=1920&capture=1"')
    ap.add_argument("--no-canvas", action="store_true", help="do not record text, shapes and images drawn with the 2D canvas API")
    ap.add_argument("--source-root", default=None, help="folder searched (besides the page's own) for the literal text of canvas text, to give it file:line")
    a = ap.parse_args(argv)
    try:
        frames = parse_frames(a.frames)
        size = parse_size(a.size) if a.size else None
        out_path = a.output
        if not out_path:
            if re.match(r"^[a-z]+://", a.page):
                out_path = "elements.kurgu.json"
            else:
                out_path = os.path.splitext(os.path.abspath(os.path.expanduser(a.page)))[0] + ".kurgu.json"
        res = measure(a.page, frames, size=size, fps=a.fps, selector=a.selector, params=a.params, canvas=not a.no_canvas, source_root=a.source_root)
    except ElementsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    write_map(out_path, res)
    n_boxes = sum(len(e["boxes"]) for e in res["elements"])
    print(os.path.abspath(out_path))
    print(f"{len(res['elements'])} elements, {n_boxes} boxes over {len(frames)} frames in {res['seconds']} s "
          f"({res['seconds'] / max(1, len(frames)) * 1000:.0f} ms/frame), video size {res['size'][0]}x{res['size'][1]}", file=sys.stderr)
    for w in res.get("warnings", []):
        print(f"note: {w}", file=sys.stderr)
    return 0


def write_map(path, res):
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = kurgu_paths.tmp_path(path, ".part")
    head = {k: v for k, v in res.items() if k != "elements"}
    with kurgu_paths.excl_open(tmp) as f:
        # one element per line (boxes inline): the file stays diffable and an agent can read it without paging
        f.write(json.dumps(head, ensure_ascii=False)[:-1] + ',\n "elements": [\n')
        f.write(",\n".join("  " + json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in res["elements"]))
        f.write("\n ]}\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
