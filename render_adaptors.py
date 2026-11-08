"""`kurgu rerender`: regenerate ONE unit of a clip that was made by code (SPEC 1.9), in place and atomically.

SECURITY MODEL. A project.json can only NAME an adaptor (`source.render`); it can never carry a command.
- Built-in adaptors (`html-frames`, `cinecode-shot`) live in this file. Their argv templates are fixed; only validated values are substituted:
  unit ids (`^[A-Za-z0-9_][A-Za-z0-9_.-]*$`), frame integers, a numeric fps, and paths that resolve (symlinks followed) inside `source.root`, which
  itself must sit inside the project folder (or a folder the USER trusts: `render_trusted_roots` in the user config / env KURGU_RENDER_ROOTS).
- User adaptors come ONLY from the user's own configuration, like the `on_send` hook: `render_adaptors` in `~/.config/kurgu/config.json`
  ({"name": ["argv", "..."]}) or env `KURGU_RENDER_<NAME>` (a JSON argv list). Never from the project folder. Placeholders `{root} {unit}
  {start_frame} {end_frame} {fps} {out}` are substituted into argv items; there is no shell.
- The page that html-frames opens runs in headless Chrome with the network blocked and no access to other local files.
- cinecode's own capture.mjs is run only from a folder the user names (`cinecode_dir` in the user config / env KURGU_CINECODE), never from
  the project (a project's own tools/ folder is project-supplied code).
Output goes to `<media>.part<ext>` next to the real file; it is probed, then `os.replace`d over the layer's media path. On any failure the
`.part` file is removed and the old media is untouched. Right before the replace, `versions.snapshot(project_dir, path)` (when that module
exists) keeps the old media in `.kurgu/versions/` for the editor's before/after toggle; a snapshot failure never blocks the render. Timing in project.json is never changed: a different duration is only reported."""
import base64
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import kurgu_paths
import media_tools
import schema

try:                                  # added by the before/after branch; absent here, so optional
    import versions
except ImportError:
    versions = None

UNIT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PARAMS_RE = re.compile(r"^[A-Za-z0-9_=&.\-]{0,200}$")
PLACEHOLDERS = ("{root}", "{unit}", "{start_frame}", "{end_frame}", "{fps}", "{out}")
BUILTIN = ("html-frames", "cinecode-shot")
MAX_FRAMES = 200000
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".m4v")


class RerenderError(Exception):
    pass


# ---------------------------------------------------------------- user configuration (never the project folder)
def _user_config():
    try:
        with open(kurgu_paths.config_file(), encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def _argv_list(v):
    return v if isinstance(v, list) and v and all(isinstance(x, str) for x in v) and v[0] else None


def user_adaptor(name):
    """argv list for a user-defined adaptor, or None. env KURGU_RENDER_<NAME> (JSON list) wins over `render_adaptors` in the user config."""
    env = "KURGU_RENDER_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()
    raw = os.environ.get(env, "").strip()
    if raw:
        try:
            return _argv_list(json.loads(raw))
        except ValueError:
            return None
    table = _user_config().get("render_adaptors")
    return _argv_list(table.get(name)) if isinstance(table, dict) else None


def trusted_roots(project=None):
    """Folders outside a project that `source.root` may point to, from the USER's side only. Entries of `render_trusted_roots` in the user config are
    {"root": folder, "project": project folder} (written by `kurgu import`) and count only for THAT project, so another project cannot borrow a trust the
    user gave elsewhere; a plain string entry and env KURGU_RENDER_ROOTS (path-separated) are the user's own global choice."""
    out = []

    def add(p):
        if isinstance(p, str) and os.path.isabs(os.path.expanduser(p)):
            out.append(os.path.realpath(os.path.expanduser(p)))
    raw = os.environ.get("KURGU_RENDER_ROOTS", "")
    items = [p for p in raw.split(os.pathsep) if p] if raw else (_user_config().get("render_trusted_roots") or [])
    proj = os.path.realpath(project) if project else None
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict):
            pj = it.get("project")
            if proj and isinstance(pj, str) and os.path.realpath(os.path.expanduser(pj)) == proj:
                add(it.get("root"))
        else:
            add(it)
    return out


def trust_root(folder, project):
    """Add {root: realpath(folder), project: realpath(project)} to `render_trusted_roots` in the USER config (created if missing, other keys kept,
    written atomically): the trust holds for that project only. Only ever called for a folder the user named on the command line (`kurgu import`).
    Returns (True, path) or (False, reason)."""
    path = kurgu_paths.config_file()
    try:
        conf = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                conf = json.load(f)
            if not isinstance(conf, dict):
                return False, "config.json is not a JSON object"
        real = os.path.realpath(os.path.expanduser(folder))
        roots = conf.get("render_trusted_roots")
        roots = [r for r in roots if isinstance(r, (str, dict))] if isinstance(roots, list) else []
        entry = {"root": real, "project": os.path.realpath(os.path.expanduser(project))}
        if entry not in roots:
            roots.append(entry)
        conf["render_trusted_roots"] = roots
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = kurgu_paths.tmp_path(path, ".part")
        with kurgu_paths.excl_open(tmp) as f:
            json.dump(conf, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
        return True, path
    except (OSError, ValueError) as e:
        return False, type(e).__name__


def cinecode_dir():
    """Folder holding cinecode's scripts/capture.mjs, from the USER's config only; None when not set / not there."""
    p = os.environ.get("KURGU_CINECODE") or _user_config().get("cinecode_dir")
    if not isinstance(p, str) or not p:
        return None
    p = os.path.realpath(os.path.expanduser(p))
    return p if os.path.isfile(os.path.join(p, "scripts", "capture.mjs")) else None


# ---------------------------------------------------------------- validation helpers
def _inside(path, base):
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(base)]) == os.path.realpath(base)
    except ValueError:
        return False


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _rel_ok(rel):
    return isinstance(rel, str) and rel != "" and not os.path.isabs(rel) and not rel.startswith("~") and not re.match(r"^[A-Za-z]:", rel) and "\x00" not in rel


def source_root(d, src):
    """Absolute root folder of the unit: source.root relative to the project folder; it must stay inside the project (or a user-trusted folder)."""
    rel = src.get("root") or "."
    if not _rel_ok(rel):
        raise RerenderError("source.root must be a path relative to the project folder")
    root = os.path.realpath(os.path.join(d, rel))
    if _inside(root, d) or any(_inside(root, t) for t in trusted_roots(d)):
        return root
    raise RerenderError(f"source.root '{rel}' points outside the project folder. If that folder is yours, list it under "
                        "\"render_trusted_roots\" in ~/.config/kurgu/config.json (or env KURGU_RENDER_ROOTS); a project file cannot grant that itself")


def confine(root, rel, what):
    """rel (a source.* path) resolved inside `root`; refuses '..' escapes, absolute paths and symlinks that leave the root."""
    if not _rel_ok(rel):
        raise RerenderError(f"source.{what} must be a path relative to source.root")
    p = os.path.realpath(os.path.join(root, rel))
    if not _inside(p, root):
        raise RerenderError(f"source.{what} '{rel}' leaves source.root")
    return p


def media_path(d, root, layer):
    """The layer's media file (realpath). Must sit inside the project folder or source.root: a project cannot make us overwrite elsewhere."""
    src = layer.get("src")
    if not isinstance(src, str) or not src or "\x00" in src:
        raise RerenderError(f"layer {layer.get('id')} has no src")
    p = os.path.expanduser(src)
    p = os.path.realpath(p if os.path.isabs(p) else os.path.join(d, p))
    if not (_inside(p, d) or _inside(p, root)):
        raise RerenderError(f"the media of layer {layer.get('id')} is outside the project folder; refusing to overwrite it")
    if os.path.splitext(p)[1].lower() not in VIDEO_EXTS:
        raise RerenderError(f"media extension {os.path.splitext(p)[1] or '(none)'} is not supported (use one of {', '.join(VIDEO_EXTS)})")
    return p


def part_path(path):
    base, ext = os.path.splitext(path)
    return kurgu_paths.tmp_path(base, ".part" + ext)            # unpredictable: nobody can plant a symlink there in advance


def probe(path):
    """{w, h, dur, audio} of a media file, never cached; None when unreadable."""
    try:
        j = media_tools.probe_json(path, "-show_streams")
    except Exception:
        return None
    w = h = 0
    audio = False
    vdur = 0.0
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and not w:
            w, h = int(s.get("width") or 0), int(s.get("height") or 0)
            try:
                vdur = float(s.get("duration") or 0)
            except (TypeError, ValueError):
                vdur = 0.0
        if s.get("codec_type") == "audio":
            audio = True
    try:
        dur = float((j.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {"w": w, "h": h, "dur": vdur or dur, "audio": audio}      # the PICTURE's length: the kept audio may run longer


# ---------------------------------------------------------------- finding the work
def agent_roots(d):
    """Source folders OUTSIDE the project that a woken agent may edit: every layer's `source.root` that source_root() accepts, i.e. that the USER trusts
    (`render_trusted_roots` / env, which `kurgu import` fills for the folder the user named). A project file alone never adds one. Real paths, sorted."""
    d = os.path.realpath(d)
    try:
        with open(schema.project_path(d), encoding="utf-8") as f:
            layers = json.load(f).get("layers") or []
    except (OSError, ValueError, AttributeError):
        return []
    out = set()
    for l in layers:
        src = l.get("source") if isinstance(l, dict) else None
        if not isinstance(src, dict):
            continue
        try:
            root = source_root(d, src)
        except RerenderError:
            continue
        if not _inside(root, d):
            out.add(root)
    return sorted(out)


def find_layers(project, ident):
    """Layers whose id or source.unit equals `ident` and that have a source."""
    out = []
    for l in project.get("layers") or []:
        if not isinstance(l, dict) or not isinstance(l.get("source"), dict):
            continue
        if l.get("id") == ident or l["source"].get("unit") == ident:
            out.append(l)
    return out


def _spec_for(d, layer, shared=False):
    """Validate everything a run needs and return a plan dict (no side effects). Raises RerenderError."""
    src = layer["source"]
    name = src.get("render")
    if not isinstance(name, str) or not name:
        raise RerenderError(f"layer {layer.get('id')} has no source.render (an adaptor name); nothing to run")
    if not NAME_RE.match(name):
        raise RerenderError("source.render must be an adaptor name, never a command")
    user_argv = user_adaptor(name)
    if name not in BUILTIN and user_argv is None:
        raise RerenderError(f"unknown render adaptor '{name}'. Built in: {', '.join(BUILTIN)}. "
                            f"Your own: add it to \"render_adaptors\" in ~/.config/kurgu/config.json or set KURGU_RENDER_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()} "
                            "(a JSON argv list). A project file cannot define one")
    unit = src.get("unit")
    if not isinstance(unit, str) or not UNIT_RE.match(unit):
        raise RerenderError("source.unit must match [A-Za-z0-9_][A-Za-z0-9_.-]*")
    fps = _num(src.get("fps"))
    if not fps or fps <= 0 or fps > 240:
        raise RerenderError("source.fps must be a number between 0 and 240")
    start = _num(src.get("unit_start_frame"))
    if start is None or start < 0 or start != int(start):
        raise RerenderError("source.unit_start_frame must be a whole number >= 0")
    start = int(start)
    root = source_root(d, src)
    media = media_path(d, root, layer)
    old = probe(media) if os.path.isfile(media) else None
    frames = src.get("frames")
    if frames is not None:
        if not isinstance(frames, int) or isinstance(frames, bool) or not 1 <= frames <= MAX_FRAMES:
            raise RerenderError(f"source.frames must be a whole number between 1 and {MAX_FRAMES}")
    listed = unit_from_list(root, src)                            # the scene list (shots.json / scenes.json) is the authority on where the unit is and how long
    if listed:
        start, frames = listed
        if start < 0 or not 1 <= frames <= MAX_FRAMES:                  # the list is project-side text: a huge or negative range is no deadline-free render
            raise RerenderError(f"the scene list gives unit {unit} the frames [{start}, {start + frames}): a start below 0 or a length outside 1..{MAX_FRAMES} is refused")
    elif frames is None and shared:
        raise RerenderError(f"unit {unit} shares its media with other units and has no frame count: set source.frames (and source.unit_start_frame) in project.json, "
                            "or give it source.list (the scene list file inside source.root)")
    elif frames is None and old and old["dur"] > 0:
        frames = max(1, int(round(old["dur"] * fps)))             # a unit that owns its media: the media is the unit
    elif frames is None:
        raise RerenderError("cannot tell how many frames the unit has: the media is missing or unreadable; set source.frames")
    plan = {"project_dir": d, "layer": layer["id"], "name": name, "user_argv": user_argv, "unit": unit, "fps": fps, "start": start, "end": start + frames,
            "frames": frames, "root": root, "media": media, "part": part_path(media), "old": old, "prev": old, "src": src, "retarget": None, "audio_from": None, "listed": bool(listed)}
    own = os.path.splitext(os.path.basename(media))[0].endswith("." + unit)        # the media already IS this unit's own file
    plan["own"] = own
    if (shared or listed) and not own:                            # a scene of a film: it gets its OWN file, even when it is the film's last user: the shared film is never rewritten
        unit_file = os.path.splitext(media)[0] + "." + unit + os.path.splitext(media)[1]
        plan.update(media=unit_file, part=part_path(unit_file), prev=probe(unit_file) if os.path.isfile(unit_file) else None, retarget=unit_file,
                    audio_from=media if old and old["audio"] else None, audio_ss=start / fps, audio_t=frames / fps)
    return plan


def unit_from_list(root, src):
    """(start frame, frame count) of the unit as the scene list in source.root says NOW, or None without `source.list` / when the unit is not in it.
    The list is read from inside source.root only (never a path outside it)."""
    rel = src.get("list")
    if rel is None:
        return None
    import scene_import
    try:
        p = confine(root, rel, "list")
        spec = scene_import.read_cinecode(root, p) if os.path.basename(p).lower() == "shots.json" else scene_import.read_scenes(root, p)
    except (RerenderError, scene_import.SceneImportError, OSError, ValueError, KeyError, TypeError):
        raise RerenderError(f"source.list '{rel}' cannot be read as a scene list inside source.root")
    for u in spec["units"]:
        if u["id"] == src.get("unit") and u["end"] > u["start"]:
            return int(u["start"]), int(u["end"] - u["start"])
    raise RerenderError(f"unit {src.get('unit')} is not in {rel} any more")


def _fmt_fps(fps):
    return str(int(fps)) if fps == int(fps) else f"{fps:g}"


# ---------------------------------------------------------------- adaptor: html-frames
def page_of(plan, default_names=("film.html",)):
    """The frame-function page: source.page, else source.file when it is an .html, else a default name; always inside the root."""
    src = plan["src"]
    rel = src.get("page")
    if rel is None and isinstance(src.get("file"), str) and src["file"].lower().endswith((".html", ".htm")):
        rel = src["file"]
    if rel is None:
        for n in default_names:
            if os.path.isfile(os.path.join(plan["root"], n)):
                rel = n
                break
    if rel is None:
        raise RerenderError("no page to render: set source.page (an .html file inside source.root that exposes window.renderFrame(i))")
    p = confine(plan["root"], rel, "page")
    if not p.lower().endswith((".html", ".htm")) or not os.path.isfile(p):
        raise RerenderError(f"source.page '{rel}' is not an existing .html file inside source.root")
    return p


def _params(plan):
    p = plan["src"].get("params")
    if p is None:
        return ""
    if not isinstance(p, str) or not PARAMS_RE.match(p):
        raise RerenderError("source.params may only contain letters, digits and _ = & . -")
    return p


def page_url(plan, server=None):
    """The page's URL. With `server` (elements._RootServer over source.root) it is served from there; without one (dry run) the text only says where it comes from."""
    q = urllib.parse.urlencode([("capture", "1")] + urllib.parse.parse_qsl(_params(plan)))
    if server:
        return server.url_for(page_of(plan), q)
    rel = os.path.relpath(page_of(plan), plan["root"]).replace(os.sep, "/")
    return f"http://127.0.0.1:<port>/{urllib.parse.quote(rel)}?{q}   (source.root served over loopback; no file:// access)"


def ffmpeg_argv(plan, size, with_audio):
    """Fixed template: PNG frames on stdin -> H.264 at source.fps; the old file's audio stream is copied when it has one."""
    w, h = size
    a = [media_tools.find("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-framerate", _fmt_fps(plan["fps"]), "-c:v", "png", "-i", "-"]
    if with_audio:
        a += (["-ss", f"{plan['audio_ss']:.6f}", "-t", f"{plan['audio_t']:.6f}", "-i", plan["audio_from"]] if plan.get("audio_from") else ["-i", plan["media"]])
    a += ["-map", "0:v"] + (["-map", "1:a:0", "-c:a", "copy"] if with_audio else ["-an"])
    a += ["-vf", f"scale={w}:{h}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "medium", "-r", _fmt_fps(plan["fps"]),
          "-movflags", "+faststart", plan["part"]]
    return a


def chrome_flags(profile, w, h, proxy_port=None):
    f = ["--headless=new", "--remote-debugging-port=0", "--remote-allow-origins=*", "--user-data-dir=" + profile, "--no-first-run",
         "--no-default-browser-check", "--disable-extensions", "--mute-audio", "--hide-scrollbars", "--force-color-profile=srgb",
         f"--window-size={w},{h}", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
         # every request, loopback included, goes to the root server, which answers only for its own folder and refuses everything else
         f"--proxy-server=http://127.0.0.1:{proxy_port or 1}", "--proxy-bypass-list=<-loopback>"]
    f += kurgu_paths.WEBRTC_FLAGS + kurgu_paths.no_sandbox_flags()
    return f + ["about:blank"]


def find_chrome():
    for var in ("KURGU_CHROME", "CHROME_PATH", "CHROME_BIN"):
        c = os.environ.get(var, "").strip()
        if c and os.path.exists(c):
            return c
    cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium",
             "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"]
    if sys.platform.startswith("win"):
        cands += [os.path.join(os.environ.get(v, ""), "Google", "Chrome", "Application", "chrome.exe") for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if shutil.which(n):
            return shutil.which(n)
    return None


class _WS:
    """Minimal RFC 6455 client (text frames only) so no third-party websocket package is needed."""

    def __init__(self, port, path, timeout=120):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = self.s.recv(4096)
            if not c:
                raise RerenderError("Chrome closed the DevTools connection")
            buf += c
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RerenderError("Chrome refused the DevTools connection")

    def _read(self, n):
        while len(self.buf) < n:
            c = self.s.recv(1 << 20)
            if not c:
                raise RerenderError("Chrome closed the DevTools connection")
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        n = len(data)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        mask = os.urandom(4)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        msg = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            op = b0 & 0x0F
            if op == 8:
                raise RerenderError("Chrome closed the page")
            if op in (9, 10):
                continue
            msg += payload
            if b0 & 0x80:
                return msg.decode("utf-8", "replace")

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class _Browser:
    def __init__(self, w, h, root=None):
        why = kurgu_paths.chrome_blocked_reason()
        if why:
            raise RerenderError(why)
        exe = find_chrome()
        if not exe:
            raise RerenderError("no Chrome / Chromium found for html-frames (set KURGU_CHROME)")
        import elements
        self.server = elements._RootServer(root) if root else None
        self.profile = tempfile.mkdtemp(prefix="kurgu-rerender-")
        self.proc = subprocess.Popen([exe] + chrome_flags(self.profile, w, h, self.server.port if self.server else None), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import atexit
        atexit.register(lambda p=self.proc: p.poll() is None and kurgu_paths.stop_chrome(p, None, None, 3))
        self.ws = None
        self.n = 0
        try:
            port_file = os.path.join(self.profile, "DevToolsActivePort")
            end = time.time() + 30
            while not os.path.exists(port_file) and time.time() < end and self.proc.poll() is None:
                time.sleep(0.1)
            if not os.path.exists(port_file):
                raise RerenderError("Chrome did not start")
            time.sleep(0.2)
            port = int(open(port_file).read().split()[0])
            tabs = []
            while time.time() < end and not tabs:
                try:
                    tabs = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2)) if t.get("type") == "page"]
                except OSError:
                    time.sleep(0.1)
            if not tabs:
                raise RerenderError("Chrome has no page")
            self.ws = _WS(port, urllib.parse.urlparse(tabs[0]["webSocketDebuggerUrl"]).path)
            self.call("Page.enable")
            self.call("Page.addScriptToEvaluateOnNewDocument", source=kurgu_paths.WEBRTC_JS)
            self.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
        except BaseException:
            self.close()
            raise

    def call(self, method, **params):
        self.n += 1
        try:
            self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
            return self._reply(method)
        except (socket.timeout, TimeoutError):
            raise RerenderError(f"Chrome did not answer {method} in time (the page is stuck or too heavy); nothing was replaced")
        except OSError as e:
            raise RerenderError(f"lost the connection to Chrome during {method}: {type(e).__name__}")

    def _reply(self, method):
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RerenderError(f"{method}: {m['error'].get('message')}")
                return m.get("result", {})

    def eval(self, js):
        r = self.call("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            ex = r["exceptionDetails"]
            raise RerenderError("page error: " + str((ex.get("exception") or {}).get("description") or ex.get("text"))[:300])
        return r["result"].get("value")

    def close(self):
        def graceful():
            if self.ws:
                self.ws.send(json.dumps({"id": 10 ** 6, "method": "Browser.close"}))
        kurgu_paths.stop_chrome(self.proc, graceful, None)
        if self.ws:
            self.ws.close()
        kurgu_paths.stop_chrome(self.proc, None, self.profile)
        if self.server:
            self.server.close()


_GRAB = """(async (i) => {
  if (typeof window.captureFrame === 'function') { const u = await window.captureFrame(i, 'image/png'); if (typeof u === 'string' && u.startsWith('data:image')) return u; }
  await window.renderFrame(i);
  await new Promise(r => { const t = setTimeout(r, 300); requestAnimationFrame(() => requestAnimationFrame(() => { clearTimeout(t); r(); })); });
  return null;
})"""


def run_html_frames(plan, log):
    old = plan["old"]
    w = h = 0
    if old and old["w"] and old["h"]:
        w, h = old["w"], old["h"]
    if not w:
        raise RerenderError("cannot tell the frame size: the old media is missing or unreadable")
    w, h = w - w % 2, h - h % 2
    ff = ffmpeg_argv(plan, (w, h), bool(old and old["audio"]))
    br = _Browser(w, h, plan["root"])
    url = page_url(plan, br.server)
    deadline = time.time() + max(300, 3 * plan["frames"])           # an overall limit: a page that crawls must not run forever
    enc = None
    try:
        br.call("Page.navigate", url=url)
        end = time.time() + 60
        while time.time() < end:
            if br.eval("document.readyState") == "complete" and br.eval("typeof window.renderFrame === 'function' || typeof window.captureFrame === 'function'"):
                break
            time.sleep(0.1)
        else:
            raise RerenderError("the page never exposed window.renderFrame(i) / window.captureFrame(i)")
        info = br.eval("(async()=>{ const r = window.READY ? await window.READY : null; return r && typeof r === 'object' ? {frames: r.frames, fps: r.fps} : null; })()")
        if info and isinstance(info.get("frames"), (int, float)) and plan["end"] > info["frames"]:
            raise RerenderError(f"the page has {int(info['frames'])} frames but the unit needs frames {plan['start']} to {plan['end'] - 1}")
        enc = subprocess.Popen(ff, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for k, i in enumerate(range(plan["start"], plan["end"])):
            if time.time() > deadline:
                raise RerenderError(f"rendering took longer than the limit ({max(300, 3 * plan['frames'])} s) and was stopped at frame {i}; nothing was replaced")
            u = br.eval(f"({_GRAB})({int(i)})")
            if u:
                png = base64.b64decode(u.split(",", 1)[1])
            else:
                png = base64.b64decode(br.call("Page.captureScreenshot", format="png", fromSurface=True)["data"])
            try:
                enc.stdin.write(png)
            except BrokenPipeError:
                break
            if log and (k + 1) % 24 == 0:
                log(f"  {k + 1}/{plan['frames']} frames")
        try:
            enc.stdin.close()
        except OSError:
            pass
        err = enc.stderr.read().decode("utf-8", "replace")
        if enc.wait() != 0:
            raise RerenderError("ffmpeg failed: " + (err.strip().splitlines() or ["unknown error"])[-1])
    finally:
        if enc and enc.poll() is None:
            enc.kill()
        br.close()


# ---------------------------------------------------------------- adaptor: cinecode-shot
def cinecode_argv(plan):
    """cinecode's own per-shot entry: `node capture.mjs --url <page> --video <out> --start a --end b` (end is inclusive there). Needs the
    user-configured cinecode folder (the project's own tools/ is project-supplied code and is never executed). None when not configured."""
    cd = cinecode_dir()
    node = shutil.which("node")
    if not cd or not node:
        return None
    page = page_of(plan, ("film.html", "engine/index.html", "index.html"))
    a = [node, os.path.join(cd, "scripts", "capture.mjs"), "--url", page, "--video", plan["part"], "--start", str(plan["start"]), "--end", str(plan["end"] - 1)]
    if _params(plan):
        a += ["--params", _params(plan)]
    return a


# ---------------------------------------------------------------- user adaptors
def expand(argv, plan):
    rep = {"{root}": plan["root"], "{unit}": plan["unit"], "{start_frame}": str(plan["start"]), "{end_frame}": str(plan["end"]),
           "{fps}": _fmt_fps(plan["fps"]), "{out}": plan["part"]}
    out = []
    for a in argv:
        for k, v in rep.items():
            a = a.replace(k, v)
        out.append(a)
    return out


# ---------------------------------------------------------------- driver
def describe(plan):
    """Lines that say exactly what would run (for --dry-run and the log)."""
    lines = [f"layer {plan['layer']}  unit {plan['unit']}  adaptor {plan['name']}  frames [{plan['start']}, {plan['end']}) at {_fmt_fps(plan['fps'])} fps",
             f"media   {plan['media']}", f"writes  {plan['part']}  then os.replace over the media"]
    if plan["name"] == "html-frames" or (plan["name"] == "cinecode-shot" and not cinecode_argv(plan)):
        old = plan["old"] or {}
        if plan["name"] == "cinecode-shot":
            lines.append("cinecode-shot: no cinecode folder configured (cinecode_dir / KURGU_CINECODE); falling back to html-frames over the shot's frame range")
        lines += [f"root    {plan['root']}  (the only folder Chrome can load from)", f"page    {page_url(plan)}", "chrome  headless, network blocked, --mute-audio: window.captureFrame(i) or window.renderFrame(i) + screenshot per frame",
                  "ffmpeg  " + " ".join(ffmpeg_argv(plan, (old.get("w", 0), old.get("h", 0)), bool(old.get("audio"))))]
        if old.get("audio"):
            lines.append("audio   copied from the old media (not re-rendered)")
    elif plan["name"] == "cinecode-shot":
        lines.append("run     " + " ".join(cinecode_argv(plan)))
        lines.append("audio   the shot render has no audio; the old media's audio stream is re-muxed after" if (plan["old"] or {}).get("audio") else "audio   none")
    else:
        lines.append("run     " + " ".join(expand(plan["user_argv"], plan)) + "   (user adaptor)")
    if plan["src"].get("elements"):
        lines.append("sidecar not regenerated by this adaptor (source.elements may be stale)")
    return lines


def _remux_audio(plan):
    """cinecode-shot / user adaptors leave a video-only .part: put the old media's audio stream back (copy, no re-encode)."""
    old = plan["old"]
    if not (old and old["audio"]) or not os.path.exists(plan["part"]):
        return
    pp = probe(plan["part"])
    if pp is None or pp["audio"]:
        return
    tmp = plan["part"] + ".mux" + os.path.splitext(plan["part"])[1]
    audio_in = (["-ss", f"{plan['audio_ss']:.6f}", "-t", f"{plan['audio_t']:.6f}", "-i", plan["audio_from"]] if plan.get("audio_from") else ["-i", plan["media"]])
    r = subprocess.run([media_tools.find("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-i", plan["part"], *audio_in, "-map", "0:v", "-map", "1:a:0",
                        "-c", "copy", tmp], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RerenderError("could not keep the old audio: " + (r.stderr.strip().splitlines() or ["ffmpeg failed"])[-1])
    os.replace(tmp, plan["part"])


def _run_argv(argv, cwd, timeout=3600):
    try:
        r = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise RerenderError(f"command not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        raise RerenderError(f"{os.path.basename(argv[0])} ran longer than {timeout} s and was stopped")
    if r.returncode != 0:
        tail = ((r.stderr or "").strip().splitlines() or (r.stdout or "").strip().splitlines() or ["no output"])[-1]
        raise RerenderError(f"{os.path.basename(argv[0])} exited with {r.returncode}: {tail}")


def run_plan(plan, log=None):
    """Run one plan; returns the result dict. The old media stays untouched until the new one is probed OK; the .part is always cleaned up."""
    t0 = time.time()
    part = plan["part"]
    if os.path.lexists(part):
        os.remove(part)
    os.makedirs(os.path.dirname(plan["media"]), exist_ok=True)
    try:
        if plan["name"] == "html-frames" or (plan["name"] == "cinecode-shot" and not cinecode_argv(plan)):
            run_html_frames(plan, log)
        elif plan["name"] == "cinecode-shot":
            _run_argv(cinecode_argv(plan), plan["root"])
            _remux_audio(plan)
        else:
            _run_argv(expand(plan["user_argv"], plan), plan["root"])
            _remux_audio(plan)
        if not os.path.isfile(part) or os.path.getsize(part) == 0:
            raise RerenderError("the adaptor finished but did not write the output file")
        new = probe(part)
        if not new or new["dur"] <= 0 or not new["w"]:
            raise RerenderError("the new media cannot be read (ffprobe); the old media is untouched")
        if versions is not None and os.path.isfile(plan["media"]):
            try:
                versions.snapshot(plan["project_dir"], plan["media"], sync=True)
            except Exception as e:                       # keeping a copy is a convenience; never lose the new render over it
                if log:
                    log(f"  (could not keep the old version: {type(e).__name__})")
        os.replace(part, plan["media"])
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
    old = plan["prev"]                                           # what was at the output path before (None the first time a unit gets its own file)
    res = {"layer": plan["layer"], "unit": plan["unit"], "adaptor": plan["name"], "path": plan["media"], "duration": round(new["dur"], 3),
           "fps": plan["fps"], "elapsed": round(time.time() - t0, 2), "old_duration": round(old["dur"], 3) if old else None,
           "duration_changed": False, "sidecar": "not regenerated" if plan["src"].get("elements") else None}
    if old and old["dur"] > 0 and abs(new["dur"] - old["dur"]) > 0.5 / plan["fps"]:
        res["duration_changed"] = True
    return res


def _write_project(d, project):
    """project.json rewritten atomically (the editor polls the file and reloads)."""
    path = schema.project_path(d)
    tmp = kurgu_paths.tmp_path(path)
    with kurgu_paths.excl_open(tmp) as f:
        f.write(json.dumps(project, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def apply_layer_updates(d, plan, res, warnings):
    """After a successful run: make the unit's layers follow what was rendered. A unit that got its own file points at it (src, src_in 0); the unit's
    frame range from the scene list is stored (unit_start_frame, frames); a layer whose length no longer matches the clip gets the new end. Layers that
    come later on the timeline are NOT moved: the warning says by how much."""
    path = schema.project_path(d)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    ids = {l["id"] for l in plan["layers"]}
    want = plan["frames"] / plan["fps"]
    changed, shift = [], 0.0
    rel = os.path.relpath(plan["media"], d).replace(os.sep, "/") if plan["retarget"] else None
    for l in raw.get("layers") or []:
        if l.get("id") not in ids:
            continue
        src = l["source"]
        before = (l.get("src"), l.get("src_in"), l.get("end"), src.get("unit_start_frame"), src.get("frames"))
        if rel:
            l["src"], l["src_in"] = rel, 0
        if plan.get("listed") or plan["retarget"]:
            src["unit_start_frame"], src["frames"] = plan["start"], plan["frames"]
        if rel or plan.get("own"):
            src["media_start_frame"] = plan["start"]                  # the unit's own file starts at the unit's first source frame (source_map reads it)
        if abs((l.get("end", 0) - l.get("start", 0)) - want) > 0.5 / plan["fps"]:
            shift = want - (l.get("end", 0) - l.get("start", 0))                      # + longer, - shorter
            l["end"] = round(l.get("start", 0) + want, 6)
        if before != (l.get("src"), l.get("src_in"), l.get("end"), src.get("unit_start_frame"), src.get("frames")):
            changed.append(l["id"])
    if not changed:
        return
    ends = [l.get("end", 0) for l in raw["layers"] if l.get("id") in ids]
    if abs(shift) > 1e-6:
        later = [l["id"] for l in raw["layers"] if l.get("id") not in ids and l.get("type") in ("video", "image") and l.get("start", 0) >= min(ends) - abs(shift) - 1e-6
                 and isinstance(l.get("source"), dict)]
        warnings.append(f"unit {plan['unit']} is now {plan['frames']} frames ({want:g} s): layer(s) {', '.join(sorted(ids))} end was updated; the scene layers after it "
                        f"({', '.join(later) or 'none'}) were NOT moved: shift their start/end by {shift:+g} s if the cut should stay gapless.")
    _write_project(d, raw)


# ---------------------------------------------------------------- element map refresh after a rerender
def _sidecar_step(head, default=6):
    sm = head.get("sampled")
    try:
        a, b, n = sm
        return max(1, int(round((b - a) / (n - 1)))) if n and n > 1 and b > a else default
    except (TypeError, ValueError):
        return default


def _mark_stale(path, old, why):
    try:
        old = dict(old)
        old["stale"] = True
        old["stale_reason"] = why[:200]
        import elements as el
        el.write_map(path, old)
    except Exception:
        pass


def refresh_sidecar(plan, log=None):
    """After a built-in rerender: re-measure this unit's frames with the same page, params and step as the sidecar was made with, replace only that
    frame range of the sidecar (atomically). On failure the old sidecar stays and is marked "stale": true. Returns "updated" | "stale" | None."""
    src = plan["src"]
    if not src.get("elements"):
        return None
    s, e = plan["start"], plan["end"]
    import source_map
    path = source_map._resolve(plan["project_dir"], src, "elements")
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        if not isinstance(old, dict) or not isinstance(old.get("elements"), list):
            raise ValueError("no elements list")
    except (OSError, ValueError, TypeError) as e:
        if log:
            log(f"  (element map not refreshed: {type(e).__name__})")
        return None
    try:
        import elements as el
        size = old.get("size") if isinstance(old.get("size"), list) and len(old["size"]) == 2 else None
        size = (int(size[0]), int(size[1])) if size else None
        step = _sidecar_step(old)
        fr = sorted(set(list(range(s, e, step)) + [e - 1]))
        extra = ["w=%d" % size[0], "h=%d" % size[1]] if size else []
        params = "&".join(p for p in ([_params(plan)] if _params(plan) else []) + ["capture=1"] + extra)
        res = el.measure(page_of(plan), fr, size=size, fps=old.get("fps") or plan["fps"], params=params, root=plan["root"])
    except Exception as ex:
        _mark_stale(path, old, f"re-measure after rerender failed: {ex}")
        if log:
            log(f"  (element map marked stale: {ex})")
        return "stale"
    el.clip_to_frames(res, s, e)
    new = {x["id"]: x for x in res["elements"]}
    merged = []
    for oe in old["elements"]:
        if not isinstance(oe, dict):
            continue
        keep = []                                                       # a unit's map holds the unit's frames only: boxes of its OLD range (it moved) go too
        n = new.pop(oe.get("id"), None)
        boxes = sorted(keep + (n["boxes"] if n else []), key=lambda b: b["f"])
        if boxes:
            row = dict(oe)
            if n:
                row.update({k: n[k] for k in ("label", "kind", "file", "line") if k in n})
            row["boxes"] = boxes
            merged.append(row)
    merged += list(new.values())
    head = {k: v for k, v in old.items() if k not in ("elements", "stale", "stale_reason")}
    head["measured"] = res.get("measured", head.get("measured"))
    fs = [b["f"] for e in merged for b in e["boxes"]]
    if fs:
        head["sampled"] = [min(fs), max(fs), head.get("sampled", [0, 0, len(fr)])[2] if isinstance(head.get("sampled"), list) and len(head["sampled"]) == 3 else len(fr)]
    head["elements"] = merged
    el.write_map(path, head)
    return "updated"


def _log_failure(d, ident, e):
    try:
        import diag
        diag.event(d, "rerender", "rerender failed", "warn", ctx={"target": str(ident)[:60], "detail": str(e)})
    except Exception:
        pass


def rerender(d, ident, dry_run=False, log=None):
    try:
        return _rerender(d, ident, dry_run, log)
    except RerenderError as e:
        if not dry_run and os.path.isfile(os.path.join(os.path.realpath(d), "project.json")):
            _log_failure(os.path.realpath(d), ident, e)
        raise


def _rerender(d, ident, dry_run=False, log=None):
    """-> {"plans": [...lines], "results": [...], "warnings": [...]}. Raises RerenderError."""
    try:
        d = kurgu_paths.require_project(d)
    except kurgu_paths.ProjectError as e:
        raise RerenderError(str(e))
    path = schema.project_path(d)
    try:
        with open(path, encoding="utf-8") as f:
            project = schema.normalize(json.load(f))
    except (OSError, ValueError) as e:
        raise RerenderError(f"project.json unreadable: {type(e).__name__}")
    if not isinstance(ident, str) or not ident:
        raise RerenderError("give a layer id or a unit")
    layers = find_layers(project, ident)
    if not layers:
        raise RerenderError(f"no layer with a source has id or unit '{ident}'")
    plans, seen = [], set()
    by_media = {}
    for x in project.get("layers") or []:
        if isinstance(x, dict) and isinstance(x.get("source"), dict) and isinstance(x.get("src"), str):
            try:
                by_media.setdefault(os.path.realpath(os.path.join(d, os.path.expanduser(x["src"]))), set()).add(x["source"].get("unit"))
            except (OSError, ValueError):
                pass
    for l in layers:
        shared = len(by_media.get(os.path.realpath(os.path.join(d, os.path.expanduser(l.get("src") or ""))), set()) - {None}) > 1
        p = _spec_for(d, l, shared)
        if p["media"] in seen:                       # several layers showing the same unit's file: render once
            continue
        seen.add(p["media"])
        p["layers"] = [x for x in layers if x["source"].get("unit") == l["source"].get("unit") and x.get("src") == l.get("src")]
        plans.append(p)
    out = {"plans": [describe(p) for p in plans], "results": [], "warnings": []}
    if dry_run:
        return out
    for p in plans:
        r = run_plan(p, log)
        apply_layer_updates(d, p, r, out["warnings"])
        if p["src"].get("list"):
            try:
                import scene_import
                scene_import.refresh_scenes_md(d, p["root"], confine(p["root"], p["src"]["list"], "list"))
            except Exception:
                pass                                              # the table is a convenience; the render and the layers are what matter
        if r["sidecar"] and (p["name"] == "html-frames" or (p["name"] == "cinecode-shot" and not cinecode_argv(p))):
            state = refresh_sidecar(p, log)
            r["sidecar"] = "refreshed" if state == "updated" else ("stale" if state == "stale" else r["sidecar"])
        want = p["frames"] / p["fps"]
        if abs(r["duration"] - want) > 0.5 / p["fps"]:                # what apply_layer_updates did is reported there; this is about the CLIP not being the unit's length
            ids = ", ".join(f"{x['id']} ({x.get('start', 0):g}-{x.get('end', 0):g} s)" for x in p["layers"])
            out["warnings"].append(f"CLIP LENGTH DIFFERS from unit {p['unit']}: the clip is {r['duration']:g} s but the unit is {p['frames']} frames = {want:g} s. "
                                   f"Layer(s) {ids} follow the UNIT's length, so the clip now ends {'early' if r['duration'] < want else 'later than the layer'}: "
                                   "fix the adaptor or the unit's frame range, or adjust the layers by hand (or tell the human).")
        if r["sidecar"] == "stale":
            out["warnings"].append(f"unit {p['unit']}: the element map could not be re-measured and is marked stale; do not trust its boxes (see elements_error).")
        elif r["sidecar"] == "refreshed":
            pass
        elif r["sidecar"]:
            out["warnings"].append(f"unit {p['unit']}: element map (source.elements) was not regenerated by {p['name']}; its boxes may be stale.")
        out["results"].append(r)
    return out


def format_result(res):
    lines = []
    for r in res["results"]:
        lines.append(f"rerendered {r['unit']} (layer {r['layer']}, {r['adaptor']})")
        lines.append(f"  path     {r['path']}")
        lines.append(f"  duration {r['duration']:g} s at {_fmt_fps(r['fps'])} fps, took {r['elapsed']:g} s")
    lines += ["WARNING: " + w for w in res["warnings"]]
    lines.append("Next: look with `kurgu frame` / `kurgu strip` before closing the note; the editor reloads the file by itself.")
    return "\n".join(lines)


def rerender_parser():
    import argparse
    ap = argparse.ArgumentParser(prog="kurgu rerender", description="Regenerate ONLY one code-made unit's media (layer.source) and replace it atomically at the same path. "
                                 "The adaptor named in source.render runs; project.json never supplies a command.")
    ap.add_argument("project_dir", help="project folder")
    ap.add_argument("target", help="layer id or source.unit")
    ap.add_argument("--dry-run", action="store_true", help="only print the argv and paths; run nothing")
    return ap


def main(argv):
    a = rerender_parser().parse_args(argv)
    try:                                                              # a SIGTERM must run the `finally` blocks that remove the temp file and stop Chrome / ffmpeg
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    except (ValueError, OSError, AttributeError):
        pass
    try:
        res = rerender(os.path.expanduser(a.project_dir), a.target, a.dry_run, log=lambda s: print(s, file=sys.stderr))
    except RerenderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.dry_run:
        print("\n\n".join("\n".join(p) for p in res["plans"]))
        print("(dry run: nothing was run)")
        return 0
    print(format_result(res))
    return 0
