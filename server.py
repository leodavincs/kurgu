#!/usr/bin/env python3
"""Kurgu local server (SPEC.md §5). Python stdlib + fontTools (through fonts.py). Binds 127.0.0.1 only.

    python3 server.py <project_dir> [--port 8765] [--no-open]

Decisions where the spec is silent:
- Version = sha1 of the bytes of project.json. A PUT without base_version skips the conflict check.
- GET /api/project returns the project normalised (schema.normalize), so clients always see every default filled.
  PUT writes exactly what the client sent (only `version: 2` is ensured).
- /media is allowed for paths under the project dir, and for any path referenced by a `src` or by `output`
  (+ its `_draft` sibling) in project.json.
- Requests whose Host header is not 127.0.0.1/localhost are refused (DNS-rebinding protection).
- /api/waveform on a file without audio answers {"rate":100,"peaks":[]} instead of an error.
- /api/thumbs answers a bare JSON array of `n` URLs (images: the same thumbnail repeated).
- Proxies are made for videos wider than 1280 px; the media-info call triggers generation (hardware decode/encode when
  available, software fallback). media-info also carries `proxy_progress` (0-1, null when no proxy applies) and
  `proxy_eta` (seconds, null until known). Sources over 1 GB start their proxy on import / first file listing.
- GET /api/state also exists (returns what PUT stored), handy for tests and for Claude.
- main() writes `.kurgu/server.json` ({port, pid, started}) and removes it on clean exit (Ctrl-C, SIGTERM), so tools
  (mcp_server.py) can tell whether an editor is running for a directory. A file whose pid is dead is stale.
"""
import argparse
import array
import hashlib
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import ai
import media_tools
import exports
import fonts
import presets
import kurgu_paths
import schema
import transcribe

ROOT = os.path.dirname(os.path.realpath(__file__))
WEB = os.path.join(kurgu_paths.data_root(), "web")
CHUNK = 256 * 1024
HW_PROXY_CODECS = ("hevc", "vp9", "av1")
BIG_SOURCE = 1 << 30          # videos above this get their proxy on first sight
IMPORT_CHUNK = 1024 * 1024
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
FONT_EXT = {".ttf", ".otf", ".ttc", ".otc"}
REJECT_FONT_EXT = {".woff", ".woff2"}
MIME_EXTRA = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
              ".json": "application/json", ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml",
              ".woff2": "font/woff2", ".mp4": "video/mp4", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
              ".wav": "audio/wav", ".png": "image/png", ".webm": "video/webm", ".ttf": "font/ttf",
              ".otf": "font/otf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class HttpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def sha1(b):
    return hashlib.sha1(b if isinstance(b, bytes) else b.encode()).hexdigest()


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in FONT_EXT:
        return "font"
    return None


def is_under(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def write_atomic(path, data):
    tmp = f"{path}.tmp{os.getpid()}{threading.get_ident()}"
    with open(tmp, "wb") as f:
        f.write(data)
    for attempt in range(20):  # Windows refuses to replace a file another handle is reading: retry briefly
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                os.remove(tmp)
                raise
            time.sleep(0.025)


def safe_name(name):
    """Sanitise an uploaded file name; returns (stem, ext) with ext lower-case."""
    name = unicodedata.normalize("NFC", (name or "").replace("\\", "/").split("/")[-1])
    stem, ext = os.path.splitext(name)
    stem = re.sub(r"[^\w .()+-]", "_", stem, flags=re.UNICODE).strip(" .") or "file"
    return stem[:120], ext.lower()


def kill_tree(p):
    """Stop a render.py process and every ffmpeg child: process group on POSIX, taskkill /T on Windows."""
    if p.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        return
    for sig, wait in ((signal.SIGTERM, 4), (signal.SIGKILL, 2)):
        try:
            os.killpg(p.pid, sig)          # render.py leads its own session (start_new_session), so pgid == pid
        except (ProcessLookupError, PermissionError):
            return
        try:
            p.wait(wait)
            return
        except subprocess.TimeoutExpired:
            continue


def open_with_os(path):
    """Open a file with the OS default app / show it in the file manager. Best effort."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)                     # noqa: only exists on Windows
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def reveal_in_os(path):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif os.name == "nt":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        pass


class App:
    """Per-server state (kept off module globals so several servers can run in one process, e.g. tests)."""

    def __init__(self, project_dir):
        self.dir = os.path.realpath(project_dir)
        self.project_path = os.path.join(self.dir, schema.PROJECT_FILE)
        self.kurgu = os.path.join(self.dir, ".kurgu")
        self.write_lock = threading.Lock()
        self.info_cache, self.info_lock = {}, threading.Lock()
        self.proxy_state, self.proxy_lock = {}, threading.Lock()
        self.proxy_prog, self.prewarmed = {}, set()
        self.proxy_sem = threading.Semaphore(2)
        self.wave_lock = threading.Lock()
        self.thumb_lock = threading.Lock()
        self.frame_locks = {}
        self.render = {"status": "idle", "progress": 0, "output": None, "output_path": None, "name": None, "error": None,
                       "warnings": []}
        self.render_lock = threading.Lock()
        self.ai = ai.Ai(self)
        self.stt = transcribe.JobRunner()
        self.render_proc, self.render_thread, self.render_cancel_flag = None, None, False
        self.render_t0, self.render_end, self.render_part = None, None, "mp4"

    # ------------------------------------------------------------ paths
    def prepare(self):
        os.makedirs(self.kurgu, exist_ok=True)
        if not os.path.exists(self.project_path):
            if os.path.exists(schema.legacy_path(self.dir)):
                schema.migrate_dir(self.dir)
                print("migrated proje.json (v1) -> project.json (v2); proje.json left untouched", flush=True)
            else:
                tpl = schema.default_project(os.path.basename(self.dir) or "Untitled")
                write_atomic(self.project_path, schema.dumps(tpl).encode())
                print("no project.json found, wrote an empty template", flush=True)

    def sub(self, name):
        d = os.path.join(self.kurgu, name)
        os.makedirs(d, exist_ok=True)
        return d

    def resolve(self, p):
        p = os.path.expanduser(p)
        if not os.path.isabs(p):
            p = os.path.join(self.dir, p)
        return os.path.realpath(p)

    def project_bytes(self):
        with open(self.project_path, "rb") as f:
            return f.read()

    def version(self):
        return sha1(self.project_bytes())

    def allowed_paths(self):
        found = set()
        try:
            project = json.loads(self.project_bytes())
        except Exception:
            return found

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "src" and isinstance(v, str) and v:
                        found.add(self.resolve(v))
                    else:
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(project)
        out = project.get("output") if isinstance(project, dict) else None
        if isinstance(out, str) and out:
            found.add(self.resolve(out))
            stem, ext = os.path.splitext(out)
            found.add(self.resolve(stem + "_draft" + ext))
        return found

    def media_path(self, raw):
        if not raw:
            raise HttpError(400, "path is required")
        real = self.resolve(raw)
        if not (is_under(real, self.dir) or real in self.allowed_paths()):
            raise HttpError(403, "path not allowed")
        if not os.path.isfile(real):
            raise HttpError(404, f"file not found: {raw}")
        return real

    def key_of(self, path):
        return sha1(f"{path}|{os.stat(path).st_mtime_ns}")

    # ------------------------------------------------------------ project
    def write_changes(self, summary):
        if not summary:
            return
        path = os.path.join(self.dir, "changes.md")
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        text, last = "", None
        if not os.path.exists(path):
            text = "# Changes (Kurgu)\n"
        else:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("## "):
                        last = line[3:].strip()
        if last != today:
            text += f"\n## {today}\n\n"
        stamp = now.strftime("%H:%M:%S")
        text += "".join(f"- {stamp} {str(s).strip()}\n" for s in summary)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)

    def backup(self, old_bytes):
        d = self.sub("backups")
        name = datetime.now().strftime("project-%Y%m%d-%H%M%S-%f.json")
        with open(os.path.join(d, name), "wb") as f:
            f.write(old_bytes)
        files = sorted(x for x in os.listdir(d) if x.startswith("project-") and x.endswith(".json"))
        for x in files[:-50]:
            try:
                os.remove(os.path.join(d, x))
            except OSError:
                pass

    def get_brief(self):
        return {"text": ai.read_brief(self.dir), "max": ai.BRIEF_MAX}

    def save_brief(self, text, summary="brief edited"):
        """Replace brief.md (previous one backed up to .kurgu/backups/brief-*.md). Empty text deletes it."""
        text = str(text or "").strip()
        if len(text) > ai.BRIEF_MAX:
            raise HttpError(400, f"brief is too long ({len(text)} > {ai.BRIEF_MAX} characters)")
        path = os.path.join(self.dir, "brief.md")
        with self.write_lock:
            old = ai.read_brief(self.dir)
            if old == text:
                return {"text": text, "max": ai.BRIEF_MAX}
            if old:
                d = self.sub("backups")
                with open(os.path.join(d, datetime.now().strftime("brief-%Y%m%d-%H%M%S-%f.md")), "w", encoding="utf-8") as f:
                    f.write(old + "\n")
                for x in sorted(x for x in os.listdir(d) if x.startswith("brief-") and x.endswith(".md"))[:-20]:
                    try:
                        os.remove(os.path.join(d, x))
                    except OSError:
                        pass
            if text:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text + "\n")
            elif os.path.exists(path):
                os.remove(path)
            self.write_changes([summary])
        return {"text": text, "max": ai.BRIEF_MAX}

    def get_project(self):
        raw = self.project_bytes()
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise HttpError(500, f"project.json is not valid JSON: {e}")
        return {"project": schema.normalize(data), "version": sha1(raw)}

    def save_project(self, body):
        project, summary, base = body.get("project"), body.get("summary") or [], body.get("base_version")
        if not isinstance(project, dict) or not isinstance(project.get("layers", []), list):
            raise HttpError(400, "project must be an object with a layers list")
        with self.write_lock:
            old = self.project_bytes()
            current = sha1(old)
            if base is not None and base != current:
                try:
                    cur = schema.normalize(json.loads(old))
                except Exception:
                    cur = None
                return 409, {"error": "conflict: project.json changed on disk", "project": cur, "version": current}
            return 200, {"version": self._write_project(project, summary, old)}

    def _write_project(self, project, summary, old):
        """Back up `old`, stamp `requires` (motion features actually used, like schema.normalize) and write project.json. Caller holds write_lock."""
        self.backup(old)
        project = schema.stamp_requires(dict(project, version=schema.VERSION))
        new = schema.dumps(project).encode()
        write_atomic(self.project_path, new)
        self.write_changes(summary if isinstance(summary, list) else [summary])
        return sha1(new)

    def apply_preset(self, body):
        """POST /api/presets/apply {layer_id, preset, at?, dur?, params?}: bake one motion preset into the layer's keys/mods and save."""
        lid, pid = body.get("layer_id"), body.get("preset")
        if pid not in presets.PRESETS:
            raise HttpError(400, f"unknown preset {json.dumps(pid)} (have: {', '.join(presets.PRESETS)})")
        with self.write_lock:
            old = self.project_bytes()
            try:
                proj = schema.normalize(json.loads(old))
            except ValueError as e:
                raise HttpError(500, f"project.json is not valid JSON: {e}")
            idx = next((i for i, l in enumerate(proj["layers"]) if l.get("id") == lid), None)
            if idx is None:
                raise HttpError(404, f"no layer {json.dumps(lid)}")
            layer = proj["layers"][idx]
            if layer.get("locked"):
                raise HttpError(400, "layer is locked")
            entry = {"preset": pid, "at": body["at"] if "at" in body else ("out" if presets.PRESETS[pid].get("default_at") == "out" else "in")}
            if isinstance(body.get("params"), dict):
                entry["params"] = body["params"]
            if "dur" in body:
                entry["dur"] = body["dur"]
            layer["use"] = [entry]
            probs = presets.validate_use(layer, "layer")
            if probs:
                raise HttpError(400, "; ".join(probs))
            proj["layers"][idx] = presets.bake_layer(layer, proj["width"], proj["height"])
            self._write_project(proj, [f'"{layer.get("name") or lid}": preset {pid} applied'], old)
        return self.get_project()

    def save_state(self, body):
        state = {"time": body.get("time", 0), "selected": body.get("selected") or [],
                 "playing": bool(body.get("playing", False)), "language": body.get("language") or "",
                 "updated": datetime.now().replace(microsecond=0).isoformat()}
        write_atomic(os.path.join(self.sub(""), "state.json"), json.dumps(state, ensure_ascii=False, indent=2).encode())
        return {"ok": True}

    def get_state(self):
        try:
            with open(os.path.join(self.kurgu, "state.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"time": 0, "selected": [], "playing": False, "language": "", "updated": None}

    # ------------------------------------------------------------ media
    @staticmethod
    def ffprobe(path):
        try:
            return media_tools.probe_json(path, "-show_streams")
        except media_tools.UnsafeSourceError as e:
            raise HttpError(422, str(e))
        except media_tools.MediaToolError as e:
            raise HttpError(422, str(e))

    def proxy_path(self, path):
        return os.path.join(self.kurgu, "proxies", self.key_of(path) + ".mp4")

    def proxy_cmd(self, path, tmp, hw):
        """ffmpeg command for a 1280-wide, keyframe-every-15 preview proxy; hw = hardware attempt (decode + encode)."""
        cmd = [media_tools.ffmpeg(), "-y", "-v", "error", "-nostdin", "-progress", "pipe:1", "-nostats"]
        venc = media_tools.hw_encoders()["h264"] if hw else None
        if venc:
            cmd += media_tools.hw_decode_args()
        cmd += media_tools.input_args(path) + ["-vf", "scale=1280:-2"]
        if venc:
            cmd += media_tools.hw_video_args(venc, crf=23, gop=15)
        else:
            cmd += ["-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-g", "15"]
        return cmd + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", tmp], bool(venc)

    def run_proxy(self, path, tmp, key, duration, hw):
        cmd, used_hw = self.proxy_cmd(path, tmp, hw)
        if hw and not used_hw:
            return None
        errlog = tempfile.TemporaryFile()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errlog, stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        t0 = time.time()
        for line in p.stdout:
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):     # (both are microseconds in practice)
                try:
                    sec = int(line.split("=", 1)[1]) / 1e6
                except ValueError:
                    continue
                if duration > 0:
                    frac = min(0.99, max(0.0, sec / duration))
                    el = time.time() - t0
                    with self.proxy_lock:
                        self.proxy_prog[key] = {"p": frac, "eta": round(el * (1 - frac) / frac, 1) if frac > 0.005 else None}
        rc = p.wait()
        errlog.close()
        return rc == 0

    def make_proxy(self, path, target, key, duration=0.0, codec=""):
        with self.proxy_sem:
            tmp = target + ".part.mp4"
            state = "error"
            try:
                # Hardware decode + encode only pays for codecs that are slow to decode in software (measured: H.264 is
                # faster on the CPU, HEVC/VP9/AV1 on the GPU); a failed hardware attempt falls back to software.
                ok = self.run_proxy(path, tmp, key, duration, True) if codec in HW_PROXY_CODECS else None
                if not ok:
                    ok = self.run_proxy(path, tmp, key, duration, False)
                if ok:
                    os.replace(tmp, target)
                    state = "ready"
            except Exception:
                pass
            if state == "error" and os.path.exists(tmp):
                os.remove(tmp)
            with self.proxy_lock:
                self.proxy_state[key] = state
                self.proxy_prog.pop(key, None)

    def start_proxy(self, path, target, duration=0.0, codec=""):
        key = os.path.basename(target)
        with self.proxy_lock:
            if os.path.exists(target):
                self.proxy_state[key] = "ready"
                return
            if self.proxy_state.get(key) in ("running", "error"):
                return
            self.proxy_state[key] = "running"
            self.proxy_prog[key] = {"p": 0.0, "eta": None}
        self.sub("proxies")
        threading.Thread(target=self.make_proxy, args=(path, target, key, duration, codec), daemon=True).start()

    def prewarm(self, path):
        """Huge sources (> 1 GB) get their proxy the moment they are seen (import / first file listing), not on first use."""
        try:
            if kind_of(path) != "video" or os.path.getsize(path) < BIG_SOURCE:
                return
        except OSError:
            return
        key = self.key_of(path)
        with self.proxy_lock:
            if key in self.prewarmed:
                return
            self.prewarmed.add(key)

        def go():
            try:
                self.media_info(path)
            except Exception:
                pass
        threading.Thread(target=go, daemon=True).start()

    def media_info(self, path):
        key = self.key_of(path)
        with self.info_lock:
            base = self.info_cache.get(key)
        if base is None:
            data = self.ffprobe(path)
            streams = data.get("streams", [])
            v = next((s for s in streams if s.get("codec_type") == "video"), None)
            try:
                duration = float(data.get("format", {}).get("duration") or 0)
            except ValueError:
                duration = 0.0
            base = {"duration": duration, "width": int(v["width"]) if v else 0, "height": int(v["height"]) if v else 0,
                    "has_audio": any(s.get("codec_type") == "audio" for s in streams),
                    "codec": (v or {}).get("codec_name", "")}
            with self.info_lock:
                self.info_cache[key] = base
        info = dict(base, proxy=None, proxy_ready=False, proxy_progress=None, proxy_eta=None)
        if kind_of(path) == "video" and base["width"] > 1280:
            target = self.proxy_path(path)
            self.start_proxy(path, target, base["duration"], base.get("codec", ""))
            info["proxy_ready"] = os.path.exists(target)
            info["proxy"] = "/media?path=" + quote(target)
            with self.proxy_lock:
                pr = self.proxy_prog.get(os.path.basename(target))
                failed = self.proxy_state.get(os.path.basename(target)) == "error"
            if info["proxy_ready"]:
                info["proxy_progress"] = 1.0
            elif not failed:
                info["proxy_progress"] = round(pr["p"], 3) if pr else 0.0
                info["proxy_eta"] = pr["eta"] if pr else None
        return info

    def waveform(self, path):
        target = os.path.join(self.sub("waveforms"), self.key_of(path) + ".json")
        with self.wave_lock:
            if os.path.exists(target):
                with open(target, "rb") as f:
                    return json.load(f)
            if not self.media_info(path)["has_audio"]:
                return {"rate": 100, "peaks": []}
            p = subprocess.Popen([media_tools.ffmpeg(), "-v", "error", *media_tools.input_args(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            peaks, window, rest = [], 80, b""
            while True:
                b = p.stdout.read(CHUNK)
                if not b:
                    break
                rest += b
                n = len(rest) // (window * 2) * (window * 2)
                if n:
                    arr = array.array("h")
                    arr.frombytes(rest[:n])
                    rest = rest[n:]
                    for i in range(0, len(arr), window):
                        peaks.append(round(max(abs(x) for x in arr[i:i + window]) / 32768, 4))
            if rest:
                arr = array.array("h")
                arr.frombytes(rest[:len(rest) // 2 * 2])
                if len(arr):
                    peaks.append(round(max(abs(x) for x in arr) / 32768, 4))
            p.wait()
            if p.returncode != 0 and not peaks:
                raise HttpError(422, "could not decode audio")
            result = {"rate": 100, "peaks": peaks}
            write_atomic(target, json.dumps(result).encode())
            return result

    def thumbs(self, path, n):
        kind = kind_of(path)
        if kind not in ("video", "image"):
            raise HttpError(400, "thumbnails are only available for video and image files")
        n = max(1, min(40, n))
        folder = os.path.join(self.sub("thumbs"), f"{self.key_of(path)}-{n}")

        def urls(names):
            return ["/media?path=" + quote(os.path.join(folder, x)) for x in names]

        with self.thumb_lock:
            names = [f"{i:03d}.jpg" for i in range(n)]
            if all(os.path.exists(os.path.join(folder, x)) for x in names):
                return urls(names)
            os.makedirs(folder, exist_ok=True)
            if kind == "image":
                one = os.path.join(folder, names[0])
                cmd = [media_tools.ffmpeg(), "-y", "-v", "error", *media_tools.input_args(path), "-vf", "scale=-2:72", "-frames:v", "1", "-q:v", "5", one]
                if subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True).returncode != 0:
                    raise HttpError(422, "could not read image")
                for x in names[1:]:
                    with open(one, "rb") as src, open(os.path.join(folder, x), "wb") as dst:
                        dst.write(src.read())
                return urls(names)
            dur = self.media_info(path)["duration"] or 1.0
            cmd = [media_tools.ffmpeg(), "-y", "-v", "error", "-ss", f"{dur / (2 * n):.4f}", *media_tools.input_args(path), "-an",
                   "-vf", f"fps={n}/{dur:.6f},scale=-2:72", "-frames:v", str(n), "-q:v", "5",
                   os.path.join(folder, "%03d.jpg.tmp.jpg")]
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace")
            made = sorted(x for x in os.listdir(folder) if x.endswith(".tmp.jpg"))
            if r.returncode != 0 and not made:
                raise HttpError(422, (r.stderr.strip().splitlines() or ["thumbnail extraction failed"])[-1])
            # ffmpeg counts from 1; pad with the last frame if it produced fewer than n
            for i, x in enumerate(names):
                src = os.path.join(folder, made[min(i, len(made) - 1)])
                with open(src, "rb") as s, open(os.path.join(folder, x), "wb") as d:
                    d.write(s.read())
            for x in made:
                os.remove(os.path.join(folder, x))
            return urls(names)

    def list_files(self):
        out = []
        for root, dirs, names in os.walk(self.dir):
            dirs[:] = sorted(d for d in dirs if d != ".kurgu" and not d.startswith("."))
            for name in sorted(names):
                k = kind_of(name)
                if k and not name.startswith("."):
                    full = os.path.join(root, name)
                    out.append({"path": os.path.relpath(full, self.dir).replace(os.sep, "/"), "kind": k,
                                "size": os.path.getsize(full), "mtime": int(os.path.getmtime(full))})
                    if k == "video" and out[-1]["size"] >= BIG_SOURCE:
                        self.prewarm(full)
                    if len(out) >= 500:
                        return out
        return out

    # ------------------------------------------------------------ fonts
    def font_list(self):
        out = []
        for f in fonts.list_faces(self.dir, block=False):   # never wait for the first system-font scan
            out.append({k: f[k] for k in ("id", "family", "style", "weight", "italic", "source")}
                       | {"file": f["file"], "index": f["index"], "url": "/font-file?id=" + quote(f["id"])})
        return out

    def font_file(self, font_id):
        face = fonts.get_face(font_id, self.dir)
        if face is None:
            raise HttpError(404, f"font not found: {font_id}")
        path = fonts.face_file(face)
        return path, fonts.mime_for(path)

    # ------------------------------------------------------------ import
    def reserve(self, directory, stem, ext):
        os.makedirs(directory, exist_ok=True)
        i = 1
        while True:
            name = f"{stem}{ext}" if i == 1 else f"{stem}-{i}{ext}"
            try:
                fd = os.open(os.path.join(directory, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                return fd, os.path.join(directory, name)
            except FileExistsError:
                i += 1

    # ------------------------------------------------------------ render
    def render_plan(self, body):
        """Validate a render request body -> (extra render.py args, absolute output or None, options or None, exists)."""
        if not isinstance(body, dict):
            body = {}
        if "preset" not in body and "options" not in body:       # legacy {"draft": bool}: project.output / _draft
            return (["--draft"] if body.get("draft") else []), None, None, False
        project = self.get_project()["project"]
        try:
            opts = exports.resolve_options(body, project)
            output, existed = exports.plan_output(opts, project, self.dir)
        except exports.OptionError as e:
            raise HttpError(400, str(e))
        return exports.cli_args(opts, project, output), output, opts, existed

    def render_tmp_dir(self):
        return os.path.join(self.kurgu, "render_tmp")

    def render_run(self, args, opts):
        cmd = [sys.executable, os.path.join(ROOT, "render.py"), self.dir] + args
        error, output, tail, warnings = None, None, [], []
        kw = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt" else {"start_new_session": True}
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, **kw)
            with self.render_lock:
                self.render_proc = p
                cancelled = self.render_cancel_flag
            if cancelled:
                kill_tree(p)
            for line in p.stdout:
                line = line.strip()
                if line.startswith("PROGRESS"):
                    try:
                        with self.render_lock:
                            self.render["progress"] = max(0.0, min(1.0, float(line.split()[1])))
                    except (IndexError, ValueError):
                        pass
                elif line.startswith("DONE"):
                    output = line[4:].strip()
                elif line.startswith("ERROR"):
                    error = line[5:].strip() or "unknown error"
                elif line.startswith("WARN"):
                    warnings.append(line[4:].strip())
                    with self.render_lock:
                        self.render["warnings"] = list(warnings)
                elif line:
                    tail = (tail + [line])[-5:]
            p.wait()
            if p.returncode != 0 and not error:
                error = "render.py exited with code %d: %s" % (p.returncode, " | ".join(tail))
            elif error is None and output is None:
                error = "render.py did not report an output path"
        except Exception as e:
            error = f"could not start render: {e}"
        with self.render_lock:
            cancelled = self.render_cancel_flag and not output
            self.render_proc = None
        if cancelled:                                    # nothing partial may stay behind
            shutil.rmtree(self.render_tmp_dir(), ignore_errors=True)
        with self.render_lock:
            self.render_end = time.time()
            if cancelled:
                self.render.update(status="cancelled", error=None, output=None, output_path=None)
            elif error:
                self.render.update(status="error", error=error)
            else:
                self.render.update(status="done", progress=1, error=None,
                                   output="/media?path=" + quote(self.resolve(output)),
                                   output_path=os.path.relpath(self.resolve(output), self.dir).replace(os.sep, "/"))
        if not error and not cancelled and opts and opts.get("open_when_done"):
            open_with_os(self.resolve(output))

    def render_start(self, body):
        """`body`: request dict (or a bool for the legacy draft flag). False when a render is already running."""
        if isinstance(body, bool):
            body = {"draft": body}
        args, output, opts, _ = self.render_plan(body)
        with self.render_lock:
            if self.render["status"] == "running":
                return False
            self.render.update(status="running", progress=0, output=None, output_path=None, error=None, warnings=[],
                               name=os.path.basename(output) if output else None)
            self.render_t0, self.render_end, self.render_cancel_flag = time.time(), None, False
            self.render_part = opts["format"] if opts else "mp4"
            self.render_thread = threading.Thread(target=self.render_run, args=(args, opts), daemon=True)
        self.render_thread.start()
        return True

    def render_cancel(self):
        """Kill the render process tree, drop the partial output. True if a render was running."""
        with self.render_lock:
            if self.render["status"] != "running":
                return False
            self.render_cancel_flag = True
            p, th = self.render_proc, self.render_thread
        if p is not None:
            kill_tree(p)
        if th is not None:
            th.join(15)
        return True

    def render_snapshot(self):
        with self.render_lock:
            d = dict(self.render)
            t0, end, ext = self.render_t0, self.render_end, self.render_part
        d["elapsed"] = None if t0 is None else round((end or time.time()) - t0, 1)
        d["eta"] = None
        if d["status"] == "running" and d["elapsed"] and d["progress"] >= 0.03:
            d["eta"] = round(d["elapsed"] * (1 - d["progress"]) / d["progress"], 1)
        size = None
        try:
            if d["status"] == "done" and d.get("output_path"):
                size = os.path.getsize(os.path.join(self.dir, d["output_path"]))
            elif d["status"] == "running":
                tmp = self.render_tmp_dir()
                sizes = [os.path.getsize(os.path.join(tmp, n)) for n in ("out." + ext, "gif_src.mkv") if os.path.exists(os.path.join(tmp, n))]
                size = max(sizes) if sizes else None
        except OSError:
            pass
        d["output_size"] = size
        return d

    # ------------------------------------------------------------ transcription (optional local speech-to-text)
    def transcribe_start(self, body):
        """-> job id. body: {path | layer_id, model?, language?}. Result words are timeline time when a layer was given."""
        if not isinstance(body, dict):
            raise HttpError(400, "body must be an object")
        av = transcribe.availability()
        if not av["available"]:
            raise HttpError(501, av["message"])
        try:
            tgt = transcribe.resolve_target(self.get_project()["project"], self.dir, body.get("path"), body.get("layer_id"),
                                            allow_outside=False)
            model = transcribe.canonical_model(body.get("model"))
        except transcribe.TranscribeError as e:
            raise HttpError(400, str(e))
        lang, layer = body.get("language"), tgt["layer"]

        def work(progress, cancel):
            tr = transcribe.transcribe_file(self.dir, tgt["path"], model, lang, tgt["src_in"], tgt["duration"], progress, cancel)
            if layer is not None:
                tr = transcribe.to_timeline(tr, float(layer["start"]), tgt["src_in"], float(layer["end"]))
                tr["layer_id"] = layer["id"]
            return tr
        try:
            return self.stt.start(work, {"model": model, "backend": av["backend"], "layer_id": layer["id"] if layer else None,
                                         "note": "The first run downloads the model (one time)."})
        except transcribe.Busy:
            raise HttpError(409, "a transcription is already running")

    def list_exports(self):
        root = os.path.join(self.dir, "exports")
        res = []
        for dp, dn, fn in os.walk(root):
            for n in fn:
                if os.path.splitext(n)[1].lower() not in (VIDEO_EXT | AUDIO_EXT | {".gif"}) or n.startswith("."):
                    continue
                full = os.path.join(dp, n)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                res.append({"path": os.path.relpath(full, self.dir).replace(os.sep, "/"), "name": n, "size": st.st_size,
                            "mtime": int(st.st_mtime), "url": "/media?path=" + quote(full)})
        res.sort(key=lambda r: -r["mtime"])
        return res[:200]

    def reveal(self, rel):
        full = os.path.realpath(os.path.join(self.dir, str(rel)))
        if not is_under(full, self.dir) or not os.path.exists(full):
            raise HttpError(404, "file not found")
        reveal_in_os(full)

    def frame(self, t):
        t = round(t, 3)
        stamp = "|".join(f"{f['id']}:{os.path.getmtime(f['file'])}" for f in fonts.project_faces(self.dir))
        key = sha1(f"{self.version()}|{t:.3f}|{stamp}")
        png = os.path.join(self.sub("frames"), key + ".png")
        if os.path.exists(png):
            return png
        with self.write_lock:
            lock = self.frame_locks.setdefault(key, threading.Lock())
        with lock:
            if os.path.exists(png):
                return png
            tmp = png + ".part.png"
            r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), self.dir, "--frame", f"{t:.3f}",
                                "--output", tmp, "--draft"], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            if r.returncode != 0 or not os.path.exists(tmp):
                msg = next((s[5:].strip() for s in r.stdout.splitlines() if s.startswith("ERROR")), None)
                raise HttpError(500, msg or (r.stderr.strip().splitlines() or ["could not render frame"])[-1])
            os.replace(tmp, png)
        return png


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Kurgu"

    @property
    def app(self):
        return self.server.app

    def log_message(self, *a):
        pass

    def log_request(self, code="-", size="-"):
        if self.path.startswith(("/api/version", "/api/state")):
            return
        print(f"{time.strftime('%H:%M:%S')} {self.command} {unquote(self.path)[:120]} -> {code}", flush=True)

    # ------------------------------------------------------------ responses
    def reply(self, code, data, ctype="application/json; charset=utf-8", extra=None, close=False):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def fail(self, code, message, close=False):
        self.reply(code, {"error": message}, close=close)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def read_json(self):
        return json.loads(self._body or b"{}")

    # ------------------------------------------------------------ routing
    def dispatch(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        raw_host = self.headers.get("Host") or ""
        host = raw_host.split("]")[0] + "]" if raw_host.startswith("[") else raw_host.rsplit(":", 1)[0]
        if raw_host == "":
            host = "127.0.0.1"
        if host not in ALLOWED_HOSTS:
            return self.fail(403, "forbidden host", close=True)
        # cross-site gate (CSRF / drive-by reads): any browser-supplied Origin must be this server's own, and a browser
        # that says the request is cross-site or same-site (another localhost port!) is refused. No headers = curl/MCP/tests.
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        if origin is not None and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return self.fail(403, "forbidden origin", close=True)
        if (self.headers.get("Sec-Fetch-Site") or "").lower() in ("cross-site", "same-site"):
            return self.fail(403, "forbidden cross-site request", close=True)
        streaming = method == "POST" and path == "/api/import"
        if not streaming:
            n = int(self.headers.get("Content-Length") or 0)
            self._body = self.rfile.read(n) if n else b""       # always drain so keep-alive survives
        try:
            if path.startswith("/api/"):
                return self.api(method, path, q)
            if method in ("GET", "HEAD"):
                if path == "/media":
                    return self.serve_media(q.get("path", ""))
                if path == "/font-file":
                    fp, ctype = self.app.font_file(q.get("id", ""))
                    with open(fp, "rb") as f:
                        return self.reply(200, f.read(), ctype, {"Cache-Control": "no-cache"})
                if path == "/":
                    return self.serve_file(os.path.join(WEB, "index.html"))
                if path.startswith("/web/"):
                    full = os.path.realpath(os.path.join(WEB, unquote(path[5:])))
                    if is_under(full, os.path.realpath(WEB)) and os.path.isfile(full):
                        return self.serve_file(full)
            self.fail(404, "not found")
        except HttpError as e:
            self.fail(e.code, e.message)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except json.JSONDecodeError as e:
            self.fail(400, f"invalid JSON: {e}")
        except (subprocess.SubprocessError, RuntimeError) as e:
            self.fail(500, str(e))
        except FileNotFoundError as e:
            self.fail(404, str(e))
        except Exception as e:
            self.fail(500, f"{type(e).__name__}: {e}")

    do_GET = lambda self: self.dispatch("GET")
    do_HEAD = lambda self: self.dispatch("HEAD")
    do_PUT = lambda self: self.dispatch("PUT")
    do_POST = lambda self: self.dispatch("POST")

    def api(self, method, path, q):
        app = self.app
        if path == "/api/project":
            if method == "GET":
                return self.reply(200, app.get_project())
            if method == "PUT":
                code, data = app.save_project(self.read_json())
                return self.reply(code, data)
        elif path == "/api/version" and method == "GET":
            return self.reply(200, {"version": app.version()})
        elif path == "/api/state":
            if method == "PUT":
                return self.reply(200, app.save_state(self.read_json()))
            if method == "GET":
                return self.reply(200, app.get_state())
        elif path == "/api/media-info" and method == "GET":
            return self.reply(200, app.media_info(app.media_path(q.get("path", ""))))
        elif path == "/api/waveform" and method == "GET":
            return self.reply(200, app.waveform(app.media_path(q.get("path", ""))))
        elif path == "/api/thumbs" and method == "GET":
            try:
                n = int(q.get("n", "10"))
            except ValueError:
                raise HttpError(400, "n must be an integer")
            return self.reply(200, app.thumbs(app.media_path(q.get("path", "")), n))
        elif path == "/api/files" and method == "GET":
            return self.reply(200, app.list_files())
        elif path == "/api/fonts" and method == "GET":
            lst = app.font_list()
            return self.reply(200, lst, extra={"X-Kurgu-Fonts-Scanning": "1"} if fonts.scanning() else None)
        elif path == "/api/presets" and method == "GET":
            return self.reply(200, presets.describe())
        elif path == "/api/presets/apply" and method == "POST":
            return self.reply(200, app.apply_preset(self.read_json()))
        elif path == "/api/import" and method == "POST":
            return self.do_import(q)
        elif path == "/api/render":
            if method == "GET":
                return self.reply(200, app.render_snapshot())
            if method == "POST":
                if not app.render_start(self.read_json()):
                    raise HttpError(409, "a render is already running")
                return self.reply(202, {"status": "running"})
        elif path == "/api/brief" and method in ("GET", "PUT"):
            if method == "GET":
                return self.reply(200, app.get_brief())
            body = self.read_json()
            if not isinstance(body.get("text"), str):
                raise HttpError(400, "text (string) is required")
            return self.reply(200, app.save_brief(body["text"], str(body.get("summary") or "brief edited")[:200]))
        elif path == "/api/brief/generate":
            if method == "GET":
                return self.reply(200, app.ai.brief_snapshot())
            if method == "POST":
                return self.ai_api(lambda: (202, app.ai.brief_start(self.read_json().get("lang"))))
        elif path == "/api/brief/generate/cancel" and method == "POST":
            return self.reply(200, app.ai.brief_cancel())
        elif path == "/api/ai" and method == "POST":
            return self.ai_api(lambda: (202, app.ai.start(self.read_json())))
        elif path == "/api/ai/threads" and method in ("GET", "POST"):
            if method == "POST":
                return self.ai_api(lambda: (200, app.ai.thread_new(self.read_json())))
            try:
                sel = json.loads(q.get("sel") or "[]")
                t = float(q["t"]) if q.get("t") not in (None, "") else None
                assert isinstance(sel, list)
            except (ValueError, AssertionError):
                raise HttpError(400, "sel must be a JSON array, t a number")
            return self.ai_api(lambda: (200, app.ai.threads_for(sel, t)))
        elif path.startswith("/api/ai/threads/"):
            parts = path[len("/api/ai/threads/"):].split("/")
            if len(parts) == 1 and method == "GET":
                return self.ai_api(lambda: (200, app.ai.thread_get(parts[0])))
            if len(parts) == 2 and parts[1] == "mark" and method == "POST":
                return self.ai_api(lambda: (200, app.ai.thread_mark(parts[0], self.read_json())))
        elif path.startswith("/api/ai/"):
            parts = path[len("/api/ai/"):].split("/")
            if len(parts) == 1 and method == "GET":
                try:
                    since = int(q.get("since", "0"))
                except ValueError:
                    since = 0
                return self.ai_api(lambda: (200, app.ai.poll(parts[0], since)))
            if len(parts) == 2 and parts[1] == "cancel" and method == "POST":
                return self.ai_api(lambda: (200, app.ai.cancel(parts[0])))
        elif path == "/api/transcribe" and method in ("GET", "POST"):
            if method == "GET":
                return self.reply(200, transcribe.availability())
            return self.reply(202, app.stt.snapshot(app.transcribe_start(self.read_json())))
        elif path.startswith("/api/transcribe/"):
            parts = path[len("/api/transcribe/"):].split("/")
            snap = app.stt.snapshot(parts[0])
            if snap is None:
                raise HttpError(404, "no such transcription job")
            if len(parts) == 1 and method == "GET":
                return self.reply(200, snap)
            if len(parts) == 2 and parts[1] == "cancel" and method == "POST":
                if not app.stt.cancel(parts[0]):
                    raise HttpError(409, "that transcription is not running")
                return self.reply(200, app.stt.snapshot(parts[0]))
        elif path == "/api/render/cancel" and method == "POST":
            if not app.render_cancel():
                raise HttpError(409, "no render is running")
            return self.reply(200, app.render_snapshot())
        elif path == "/api/render/plan" and method == "POST":
            args, output, opts, existed = app.render_plan(self.read_json())
            rel = os.path.relpath(output, app.dir).replace(os.sep, "/") if output else None
            sz = exports.output_size(opts, app.get_project()["project"]) if opts else None
            return self.reply(200, {"output": rel, "exists": existed, "size": sz, "options": opts})
        elif path == "/api/export-presets" and method == "GET":
            return self.reply(200, exports.presets_payload())
        elif path == "/api/exports" and method == "GET":
            return self.reply(200, app.list_exports())
        elif path == "/api/exports/reveal" and method == "POST":
            app.reveal(self.read_json().get("path", ""))
            return self.reply(200, {"ok": True})
        elif path == "/api/frame" and method == "GET":
            try:
                t = float(q.get("t", ""))
            except ValueError:
                raise HttpError(400, "t must be a number")
            with open(app.frame(t), "rb") as f:
                return self.reply(200, f.read(), "image/png")
        else:
            raise HttpError(404, "not found")
        raise HttpError(405, "method not allowed")

    def ai_api(self, fn):
        try:
            code, data = fn()
        except ai.AiError as e:
            raise HttpError(e.code, e.message)
        return self.reply(code, data)

    # ------------------------------------------------------------ import
    def do_import(self, q):
        app = self.app
        stem, ext = safe_name(q.get("name", ""))
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            return self.fail(411, "Content-Length is required", close=True)
        length = int(length)
        if ext in REJECT_FONT_EXT:
            return self.fail(415, "woff/woff2 fonts are not supported; use .ttf, .otf or .ttc", close=True)
        kind = kind_of("x" + ext)
        if kind is None:
            return self.fail(415, f"unsupported file type: {ext or '(no extension)'}", close=True)
        directory = os.path.join(app.dir, "fonts" if kind == "font" else "media")
        fd, dest = app.reserve(directory, stem, ext)
        try:
            with os.fdopen(fd, "wb") as f:
                left = length
                while left > 0:
                    b = self.rfile.read(min(IMPORT_CHUNK, left))
                    if not b:
                        raise ConnectionResetError("client closed during upload")
                    f.write(b)
                    left -= len(b)
        except BaseException:
            try:
                os.remove(dest)
            except OSError:
                pass
            raise
        result = {"path": os.path.relpath(dest, app.dir).replace(os.sep, "/"), "kind": kind}
        if kind == "video":
            app.prewarm(dest)
        if kind == "font":
            fonts.refresh()
            faces = fonts.scan_font_file(dest, "project")
            if not faces:
                os.remove(dest)
                return self.fail(400, "not a readable font file")
            result["font_ids"] = [f["id"] for f in faces]
        self.reply(200, result)

    # ------------------------------------------------------------ files
    def serve_file(self, path):
        with open(path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(path)[1].lower()
        self.reply(200, data, MIME_EXTRA.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream")

    def serve_media(self, raw):
        real = self.app.media_path(raw)
        size = os.path.getsize(real)
        ext = os.path.splitext(real)[1].lower()
        ctype = MIME_EXTRA.get(ext) or mimetypes.guess_type(real)[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if not m or (m.group(1) == "" and m.group(2) == ""):
                return self.reply(416, {"error": "invalid Range"}, extra={"Content-Range": f"bytes */{size}"})
            if m.group(1) == "":
                start, end = max(0, size - int(m.group(2))), size - 1
            else:
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            if start >= size or start > end:
                return self.reply(416, {"error": "range not satisfiable"}, extra={"Content-Range": f"bytes */{size}"})
            code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-cache")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(real, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                b = f.read(min(CHUNK, left))
                if not b:
                    break
                self.wfile.write(b)
                left -= len(b)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"     # on Windows SO_REUSEADDR lets a second server bind a port that is in use

    def server_bind(self):
        if os.name == "nt":                   # ...so ask for exclusive use: a busy port must fail and make_server go to the next one
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def server_close(self):
        if getattr(self, "app", None):
            self.app.render_cancel()               # never leave a render running behind a stopped server
        super().server_close()

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def make_server(project_dir, port=8765, tries=50):
    """Prepare the project dir and bind the first free port in [port, port+tries). Port 0 = any free port."""
    app = App(project_dir)
    if not os.path.isdir(app.dir):
        raise SystemExit(f"folder does not exist: {app.dir}")
    app.prepare()
    last = None
    for p in ([0] if port == 0 else range(port, port + tries)):
        try:
            srv = Server(("127.0.0.1", p), Handler)
            srv.app = app
            return srv
        except OSError as e:
            last = e
    raise SystemExit(f"no free port found ({last})")


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page; paths may hold emoji / Turkish letters
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Kurgu local editor server")
    ap.add_argument("project_dir", nargs="?", default=".")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    a = ap.parse_args(argv)
    srv = make_server(a.project_dir, a.port)
    fonts.start_system_scan()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"Kurgu: {url}  (project: {srv.app.dir})", flush=True)
    if not a.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    info_path = os.path.join(srv.app.kurgu, "server.json")
    info = {"port": srv.server_address[1], "pid": os.getpid(), "started": datetime.now().replace(microsecond=0).isoformat()}
    write_atomic(info_path, json.dumps(info).encode())

    def on_term(*_):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, on_term)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        srv.app.ai.shutdown()
        srv.server_close()
        try:
            with open(info_path, encoding="utf-8") as f:
                mine = json.load(f).get("pid") == os.getpid()
            if mine:
                os.remove(info_path)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    main()
