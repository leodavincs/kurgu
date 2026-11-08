"""agent_notes.py: the "watch + note + send" loop between the human and a coding agent.

The human watches the video in the editor, drops timestamped notes and clicks Send. The editor server (server.App.notes_send)
marks those notes `sent` in project.json, saves a JPEG of each frame under .kurgu/notes/, rewrites NOTLAR.md (readable by both)
and appends ONE JSON line per send to .kurgu/inbox.jsonl. The agent blocks on `kurgu notes wait <dir>` (or the MCP tool
`notes_wait`) and is woken by that line; it fixes the notes, then `kurgu notes done <dir> <id...> --reply "..."`, then waits again. Every inbox
entry has a monotonically increasing `seq`; `.kurgu/inbox.cursor` holds the last seq the agent consumed, so a send made WHILE the agent was
fixing is returned by the next wait instead of being lost.

Note status (derived): done -> "done"; else `asked` timestamp set -> "asked" (the agent asked a question, `question`; waiting for the human, who answers in the
editor: the answer goes into `answer`, `asked` is cleared and the note re-enters the inbox with a new seq); else sent timestamp set -> "sent"; else "open".

CLI:  kurgu notes list <dir> [--open|--sent|--all] [--json]
      kurgu notes wait <dir> [--timeout S] [--since SEQ] [--json]
      kurgu notes done <dir> <id...> [--reply "text"]
      kurgu notes ask <dir> <id> --question "text"
Pure standard library plus schema; no network. The project file is written through the running editor when there is one
(so the editor reloads live), else directly."""
import argparse
import collections
import json
import math
import os
import re
import stat
import sys
import threading
import time
from datetime import datetime

import kurgu_paths
import schema

RECOVER_EVERY = 15.0          # seconds between "sent but never announced" checks while waiting
NOTES_MD = "NOTLAR.md"
# ---------------------------------------------------------------- safe writes inside .kurgu/ (a shared project folder may ship symlinks there)
IS_WINDOWS = os.name == "nt"
SHELL_META = '&|<>^%"!'


class KurguRefusal(OSError):
    """Kurgu refused to do something for safety. `code` + `params` are shown to the user by the editor (i18n key reason.<code>)."""
    def __init__(self, code, message, **params):
        super().__init__(message)
        self.code, self.params = code, params


def _rel(path):
    parts = os.path.normpath(str(path)).replace("\\", "/").split("/")
    return "/".join(parts[parts.index(".kurgu"):]) if ".kurgu" in parts else parts[-1]


def open_nofollow(path, mode="w"):
    """Open `path` for writing ("w" truncate / "a" append) as UTF-8 text, refusing symlinks: the file itself, and the `.kurgu` folder it lives in.
    A project folder is untrusted: `.kurgu/hook.log -> ~/.zshrc` must not turn Send into an append to the user's shell profile."""
    parent = os.path.dirname(path)
    if os.path.basename(parent) == ".kurgu" and _linkish(parent):
        raise KurguRefusal("symlink", f"refusing to write: {parent} is a symbolic link", path=".kurgu")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if mode == "a" else os.O_TRUNC) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)   # (O_BINARY: on Windows the CRT must not add a second \r) a shipped FIFO fails (ENXIO) instead of blocking
    if not hasattr(os, "O_NOFOLLOW") and os.path.islink(path):
        raise KurguRefusal("symlink", f"refusing to write: {path} is a symbolic link", path=_rel(path))
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as e:
        if os.path.islink(path):
            raise KurguRefusal("symlink", f"refusing to write: {path} is a symbolic link", path=_rel(path)) from e
        raise
    import stat
    if not stat.S_ISREG(os.fstat(fd).st_mode):                 # a FIFO with a reader, a device, ...: not a log file
        os.close(fd)
        raise OSError(f"refusing to write: {os.path.basename(path)} is not a regular file")
    return os.fdopen(fd, mode, encoding="utf-8", newline="\n")        # one newline convention in every Kurgu log line, on every OS


WATCHED = ("inbox.jsonl", "inbox.seq", "inbox.cursor", "inbox.cursor.tmp", "inbox.seq.tmp")      # a Send cannot record notes through these
SIDE_FILES = ("hook.log", "listening.json", "listening.lock", "log.jsonl")                        # only reported (the hook / heartbeat / diagnostics skip them)


def _reparse(path):
    """Windows: a junction or any other reparse point (it redirects like a symlink but os.path.islink says False). False elsewhere."""
    if not IS_WINDOWS:
        return False
    try:
        if hasattr(os.path, "isjunction") and os.path.isjunction(path):
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)          # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


def _linkish(path):
    return os.path.islink(path) or _reparse(path)


def hook_agent_exe():
    """The agent program the user's on_send hook ends up running, resolved like a launch would: `kurgu wake claude|codex ...` -> that agent, else the hook's own
    first word. None when no hook is configured."""
    cmd = on_send_command()
    if not cmd:
        return None
    name = cmd[cmd.index("wake") + 1] if "wake" in cmd[:-1] and cmd[cmd.index("wake") + 1] in ("claude", "codex") else cmd[0]
    return resolve_exe(name)


def check_writable(d, side=False):
    """Raise KurguRefusal(symlink) when `.kurgu` or a file Kurgu writes in it is a symbolic link. Called before a Send records anything."""
    k = os.path.join(d, ".kurgu")
    if _linkish(k):
        raise KurguRefusal("symlink", f"refusing to write: {k} is a symbolic link or junction", path=".kurgu")
    ch = os.path.join(d, "changes.md")
    if _linkish(ch):                                   # written (appended) on every project save and send: a shipped symlink would redirect it
        raise KurguRefusal("symlink", f"refusing to write: {ch} is a symbolic link", path="changes.md")
    try:
        st = os.lstat(ch)
    except OSError:
        st = None
    if st is not None and not stat.S_ISREG(st.st_mode):   # a FIFO / device / folder named changes.md: the append would hang or fail AFTER project.json was written
        raise KurguRefusal("special_file", f"refusing to write: {ch} is not a regular file", path="changes.md")
    if _linkish(os.path.join(k, "notes")):
        raise KurguRefusal("symlink", f"refusing to write: {os.path.join(k, 'notes')} is a symbolic link or junction", path=".kurgu/notes")
    for n in WATCHED + (SIDE_FILES if side else ()):
        if os.path.islink(os.path.join(k, n)):
            raise KurguRefusal("symlink", f"refusing to write: {os.path.join(k, n)} is a symbolic link", path=f".kurgu/{n}")


def shim_refusal(exe, path):
    """Windows only: an npm `.cmd` / `.bat` shim runs through cmd.exe, where these characters inside the PROJECT PATH (an argument that came from the project)
    can inject commands (BatBadBut). `exe` itself (the launcher, e.g. a user profile called A&B) is not checked. Returns a KurguRefusal (not raised) or None."""
    if not IS_WINDOWS or not str(exe).lower().endswith((".cmd", ".bat")):
        return None
    bad = sorted({c for c in str(path) if c in SHELL_META})
    if not bad:
        return None
    return KurguRefusal("shell_chars", f"refusing to start {os.path.basename(str(exe))}: the project path contains {' '.join(bad)} (cmd.exe would read it as a command). "
                        f"Move or rename the project folder so its path has none of {SHELL_META}.", chars=" ".join(bad), path=str(path))


INBOX = os.path.join(".kurgu", "inbox.jsonl")
FRAMES = os.path.join(".kurgu", "notes")


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def status_of(n):
    return "done" if n.get("done") else "asked" if n.get("asked") else "sent" if n.get("sent") else "open"


def _open_regular(path, binary=False):
    """A project-side file opened for reading: regular files only, never through a symlink, never blocking (a shipped FIFO or `-> /dev/zero` would hang or never end)."""
    import diag
    return diag.open_regular(path, binary)


AGENT_LABELS = {"claude": "Claude", "codex": "Codex"}


def env_agent_label(env=None):
    """\"Claude\" / \"Codex\" for the agent this process runs inside (env only), else ''. Unlike env_session it ignores KURGU_WAKE: it only names, it records nothing."""
    env = os.environ if env is None else env
    if env.get("CODEX_THREAD_ID") or env.get("CODEX_SESSION_ID"):
        return "Codex"
    return "Claude" if env.get("CLAUDE_CODE_SESSION_ID") else ""


def display_agent_name(d):
    """The name on the Send button, in this order: an explicit name (env / user config / project config), the agent that is LISTENING right now (its heartbeat
    says which), the agent the wake hook starts (`kurgu wake claude|codex`), else '' (the editor then says \"Ajana\")."""
    v = agent_name(d)
    if v:
        return v
    now = time.time()
    live = []
    for e in _read_listeners(d).values():
        try:
            b = _num(e.get("beat")) if isinstance(e, dict) else None
            if b is not None and 0 <= now - b < BEAT_FRESH and _pid_alive(e["pid"]) and isinstance(e.get("agent"), str) and e["agent"].strip():
                live.append((b, e["agent"].strip()[:40]))
        except (KeyError, TypeError, ValueError):
            continue
    if live:
        return max(live)[1]
    cmd = on_send_command()
    if cmd and "wake" in cmd[:-1] and cmd[cmd.index("wake") + 1] in AGENT_LABELS:
        return AGENT_LABELS[cmd[cmd.index("wake") + 1]]
    return ""


def agent_name(d):
    """Display name of the agent the Send button talks to: $KURGU_AGENT_NAME, else `agent_name` in .kurgu/config.json, else ''."""
    v = os.environ.get("KURGU_AGENT_NAME", "").strip()
    if v:
        return v
    try:
        with _open_regular(os.path.join(d, ".kurgu", "config.json")) as f:
            v = json.load(f).get("agent_name")
        return v.strip() if isinstance(v, str) else ""
    except (OSError, ValueError, AttributeError):
        return ""


def default_mode(d):
    """Editor mode on a first visit: $KURGU_MODE, else `default_mode` in .kurgu/config.json, else "review" (the calm view for watching and noting;
    "edit" is the full editor)."""
    v = os.environ.get("KURGU_MODE", "").strip().lower()
    if v not in ("review", "edit"):
        try:
            with _open_regular(os.path.join(d, ".kurgu", "config.json")) as f:
                v = str(json.load(f).get("default_mode", "")).strip().lower()
        except (OSError, ValueError, AttributeError):
            v = ""
    return v if v in ("review", "edit") else "review"


def inbox_path(d):
    return os.path.join(d, INBOX)


def frame_rel(nid):
    return FRAMES.replace(os.sep, "/") + f"/{nid}.jpg"


def read_inbox(d):
    """Every inbox entry, each with its `seq` (older lines without one get their line number)."""
    out = []
    try:
        with _open_regular(inbox_path(d)) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    e.setdefault("seq", len(out) + 1)
                    out.append(e)
    except OSError:
        pass
    return out


def cursor_path(d):
    return os.path.join(d, ".kurgu", "inbox.cursor")


def get_cursor(d):
    """The saved cursor. One above the highest seq that exists means the inbox was deleted / truncated by hand: reset to 0, so what is still
    sent (and not done) is delivered again instead of every later send being swallowed."""
    c = _read_int(cursor_path(d))
    return c if c <= inbox_len(d) else 0


def set_cursor(d, seq):
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    tmp = kurgu_paths.tmp_path(cursor_path(d))
    with open_nofollow(tmp, "w") as f:
        f.write(str(int(seq)))
    os.replace(tmp, cursor_path(d))


# ---------------------------------------------------------------- marks (circles / boxes / arrows drawn on the frame to say WHERE)
MARK_TYPES = ("ellipse", "rect", "arrow")
MARK_COLOR = (243, 195, 75)          # cue yellow, same as the editor's --note (the default of a mark without `color`)
MARK_DEFAULT = "#f3c34b"
COLOR_NAMES = {"#f3c34b": ("sarı", "yellow"), "#ff5446": ("kırmızı", "red"), "#ffffff": ("beyaz", "white"), "#2ec4b6": ("turkuaz", "cyan"), "#6cc592": ("yeşil", "green")}


def mark_color(m):
    c = str(m.get("color") or "").lower()
    return c if len(c) == 7 and c[0] == "#" and all(x in "0123456789abcdef" for x in c[1:]) else MARK_DEFAULT


def hex_rgb(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def _r(v, n=0):
    v = round(float(v), n)
    return int(v) if n == 0 else v


def _region(cx, cy):
    """Plain-language place of a point given as 0-1 coordinates: the quarter of the frame, or the centre."""
    if 0.4 <= cx <= 0.6 and 0.4 <= cy <= 0.6:
        return "merkez"
    return f'{"sol" if cx < 0.5 else "sağ"} {"üst" if cy < 0.5 else "alt"} çeyrek'


_size_cache = {}


def native_size(d, l):
    """(w, h) of a video / image layer's source after `crop`, or None when it cannot be read cheaply (cached per file + mtime)."""
    src = l.get("src")
    if not d or not src:
        return None
    path = os.path.realpath(os.path.join(d, os.path.expanduser(str(src))))
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return None
    if key not in _size_cache:
        wh = None
        try:
            if l.get("type") == "image":
                from PIL import Image
                with Image.open(path) as im:
                    wh = im.size
            else:
                import media_tools
                for st in media_tools.probe_json(path, "-show_streams").get("streams", []):
                    if st.get("codec_type") == "video" and st.get("width") and st.get("height"):
                        wh = (int(st["width"]), int(st["height"]))
                        break
        except Exception:
            wh = None
        _size_cache[key] = wh
    wh = _size_cache[key]
    if not wh:
        return None
    c = l.get("crop") if isinstance(l.get("crop"), dict) else {}
    try:
        fw = 1 - float(c.get("left", 0)) - float(c.get("right", 0))
        fh = 1 - float(c.get("top", 0)) - float(c.get("bottom", 0))
    except (TypeError, ValueError):
        fw = fh = 1.0
    return wh[0] * fw, wh[1] * fh


def _at(l, prop, t, dflt):
    """Value of a transform prop at timeline time `t` (keys + modifiers) when `t` is given, else the static value."""
    base = l.get(prop, dflt)
    base = float(base) if isinstance(base, (int, float)) and not isinstance(base, bool) else float(dflt)
    if t is None:
        return base
    try:
        import anim
        return float(anim.prop_value(l, prop, t - float(l.get("start", 0) or 0), base))
    except Exception:
        return base


_font_cache = {}


def _text_size(l, d=None):
    return _text_size_ex(l, d)[:2]


def _text_size_ex(l, d=None):
    """(w, h, measured) of a text layer's block at scale 1: the renderer's font metrics (line widths, letter spacing, line height) when the font loads,
    else the rough size x 0.55 per character guess."""
    size = float(l.get("size", 48) or 48)
    lines = str(l.get("text", "")).split("\n") or [""]
    lh = size * float(l.get("line_height", 1.25) or 1.25)
    try:
        import fonts
        key = (l.get("font") or schema.DEFAULT_FONT, size, d)
        if key not in _font_cache:
            _font_cache[key] = fonts.load_pil_font(key[0], size, d)[0]
        f = _font_cache[key]
        sp = size * float(l.get("letter_spacing", 0) or 0)
        w = max(f.getlength(x) + sp * max(len(x) - 1, 0) for x in lines)
        return max(w, 1.0), len(lines) * lh, True
    except Exception:
        return max(len(x) for x in lines) * size * 0.55, len(lines) * lh, False


def _box_of(l, W, H, t=None, d=None):
    """Axis-aligned box (x0, y0, x1, y1) of a visual layer in project pixels. With the project folder `d`, image / video use their NATIVE size fitted into the
    frame like the renderer does (fit contain / cover / native, crop) times scale; without it they cover the frame. With `t` the keyframed x / y / scale /
    rotation at that timeline time are used and rotation about the anchor is applied. Text width is a guess (size and longest line)."""
    cx, cy = _at(l, "x", t, W / 2), _at(l, "y", t, H / 2)
    k = _at(l, "scale", t, 1)
    sx, sy = k * _at(l, "scale_x", t, 1), k * _at(l, "scale_y", t, 1)
    if l.get("type") == "text":
        w, h = _text_size(l, d)
    else:
        nat = native_size(d, l) if l.get("type") in ("video", "image") else None
        if nat and nat[0] > 0 and nat[1] > 0:
            mode = l.get("fit") or ("cover" if l.get("type") == "video" else "contain")
            kf = 1.0 if mode == "native" else min(W / nat[0], H / nat[1]) if mode == "contain" else max(W / nat[0], H / nat[1])
            w, h = nat[0] * kf, nat[1] * kf
        else:
            w, h = W, H
    ax, ay = (_at(l, "anchor_x", t, .5) - .5) * w, (_at(l, "anchor_y", t, .5) - .5) * h
    rot = math.radians(_at(l, "rotation", t, 0))
    c, s_ = math.cos(rot), math.sin(rot)
    xs, ys = [], []
    for qx, qy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        vx, vy = (qx - ax) * sx, (qy - ay) * sy
        xs.append(cx + vx * c - vy * s_)
        ys.append(cy + vx * s_ + vy * c)
    return min(xs), min(ys), max(xs), max(ys)


BOUND_KEYS = ("left", "top", "right", "bottom", "w", "h")


def estimate_bounds(l, W, H, t=None, d=None):
    """Fallback when the editor did not send bounds (agent-made note, older client): the box from `_box_of` (native size, keys at `t`), flagged approx."""
    x0, y0, x1, y1 = _box_of(l, W, H, t, d)
    what = "text block centre" if l.get("type") == "text" else "layer centre"
    if l.get("type") == "text":
        measured = _text_size_ex(l, d)[2]                                  # the renderer's own font metrics
    elif l.get("type") in ("video", "image"):
        nat = native_size(d, l)
        measured = bool(nat and nat[0] > 0 and nat[1] > 0)                  # the file's real pixel size, fitted like the renderer
    else:
        measured = True                                                     # a colour / shape layer: the frame (or its own size) is exact
    out = {"left": _r(x0, 1), "top": _r(y0, 1), "right": _r(x1, 1), "bottom": _r(y1, 1), "w": _r(x1 - x0, 1), "h": _r(y1 - y0, 1)}
    if measured:                                                           # computed by Kurgu from the same metrics the renderer uses: not a guess, so not flagged
        out["anchor"] = f"x,y is the {what} (measured by Kurgu from the renderer's metrics)"
    else:
        out["anchor"] = f"x,y is the {what} (estimate; the editor sent no measured bounds)"
        out["approx"] = True
    return out


def clean_bounds(b):
    """One stored bounds entry -> only the known fields, numbers rounded; None when it is not usable."""
    if not isinstance(b, dict) or not all(isinstance(b.get(k), (int, float)) and not isinstance(b.get(k), bool) for k in BOUND_KEYS):
        return None
    out = {k: _r(b[k], 1) for k in BOUND_KEYS}
    if isinstance(b.get("anchor"), str) and b["anchor"].strip():
        out["anchor"] = b["anchor"].strip()[:300]
    return out


def bounds_of(proj, n, l, d=None):
    """Bounds of layer dict `l` for note `n`: what the editor measured at send time, else an estimate. None for audio layers."""
    if l.get("type") not in ("video", "image", "text", "color"):
        return None
    b = clean_bounds((n.get("bounds") or {}).get(l.get("id")))
    if b:
        return b
    if l.get("type") == "color":
        return None
    return estimate_bounds(l, float(proj.get("width", 1920)), float(proj.get("height", 1080)), n.get("t"), d)


def fmt_bounds(b):
    return f"left {b['left']:g}, top {b['top']:g}, right {b['right']:g}, bottom {b['bottom']:g} ({b['w']:g}x{b['h']:g})"


def layers_under_mark(proj, t, pts, d=None):
    """Names of the visual layers active at `t` whose box contains one of `pts` (project pixels), front-most first; layers hit by the first point
    (the shape's centre / an arrow's tip) come before the others. Colour and audio layers are left out."""
    W, H = float(proj.get("width", 1920)), float(proj.get("height", 1080))
    hits = []
    for i, l in enumerate(proj.get("layers", [])):
        if l.get("type") not in ("video", "image", "text") or l.get("hidden") or not (l.get("start", 0) <= t < l.get("end", 0)):
            continue
        x0, y0, x1, y1 = _box_of(l, W, H, t, d)
        idx = next((j for j, (px, py) in enumerate(pts) if x0 <= px <= x1 and y0 <= py <= y1), None)
        if idx is not None:
            hits.append((0 if idx == 0 else 1, i, l.get("name") or l.get("id")))
    return [n for _f, _i, n in sorted(hits)]


def mark_view(m, W, H, proj=None, t=0.0, note=None, d=None):
    """One mark for agents: pixel + normalised coordinates (project space W x H), a plain-language hint and the layers under it (topmost first)."""
    typ = m.get("type")
    W, H = max(1.0, float(W)), max(1.0, float(H))
    col = mark_color(m)
    cname_tr, cname_en = COLOR_NAMES.get(col, (col, col))
    out = {"type": typ, "color": col, "color_name": cname_en}
    def pick(nk, pk, size):   # the stored normalised value wins (it survives a resolution change); pixels are then recomputed from the current size
        v = m.get(nk)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float(m.get(pk, 0)) / size
    if typ == "arrow":
        n1, m1, n2, m2 = pick("nx1", "x1", W), pick("ny1", "y1", H), pick("nx2", "x2", W), pick("ny2", "y2", H)
        x1, y1, x2, y2 = n1 * W, m1 * H, n2 * W, m2 * H
        out.update(x1=_r(x1), y1=_r(y1), x2=_r(x2), y2=_r(y2), nx1=_r(n1, 4), ny1=_r(m1, 4), nx2=_r(n2, 4), ny2=_r(m2, 4))
        out["hint"] = f"işaret: {cname_tr} ok, ({_r(x1)}, {_r(y1)}) noktasından ({_r(x2)}, {_r(y2)}) noktasına — ucu {_region(x2 / W, y2 / H)}"
    else:
        nx, ny, nw, nh = pick("nx", "x", W), pick("ny", "y", H), pick("nw", "w", W), pick("nh", "h", H)
        x, y, w, h = nx * W, ny * H, nw * W, nh * H
        cx, cy = x + w / 2, y + h / 2
        out.update(x=_r(x), y=_r(y), w=_r(w), h=_r(h), nx=_r(nx, 4), ny=_r(ny, 4), nw=_r(nw, 4), nh=_r(nh, 4), center=[_r(cx), _r(cy)])
        name = "elips" if typ == "ellipse" else "dikdörtgen"
        out["hint"] = f"işaret: {cname_tr} {name}, merkez ({_r(cx)}, {_r(cy)}), {_r(w)}x{_r(h)} px — {_region(cx / W, cy / H)}"
    lays = [l.get("name") or l.get("id") for l in (m.get("layers") or []) if isinstance(l, dict)]
    if not lays and proj is not None:     # the mark came without the editor's hit test (agent / pipeline / older client): work it out here
        if typ == "arrow":
            pts = [(x2, y2), ((x1 + x2) / 2, (y1 + y2) / 2)]
        else:
            pts = [(cx, cy), (x + w * .25, y + h * .25), (x + w * .75, y + h * .25), (x + w * .25, y + h * .75), (x + w * .75, y + h * .75)]
        lays = layers_under_mark(proj, t, pts, d)
    out["layers"] = lays
    if proj is not None:
        rects = [(x2 - 8, y2 - 8, x2 + 8, y2 + 8), (x1 - 8 + (x2 - x1) / 2, y1 - 8 + (y2 - y1) / 2, x1 + 8 + (x2 - x1) / 2, y1 + 8 + (y2 - y1) / 2)] if typ == "arrow" \
            else [(x, y, x + w, y + h)]
        eu = elements_under_mark(proj, t, rects, d)
        if eu:
            out["elements_under"] = eu
    if lays:
        out["hint"] += " — altındaki katmanlar (üstten alta): " + ", ".join(lays)
        if proj is not None and note is not None:
            lb = []
            for nm in lays:
                l = next((x for x in proj.get("layers", []) if isinstance(x, dict) and nm in (x.get("name"), x.get("id"))), None)
                b = bounds_of(proj, note, l, d) if l else None
                if b:
                    lb.append({"id": l.get("id"), "name": l.get("name") or l.get("id"), **b, **layer_timing(l)})
            if lb:
                out["layer_bounds"] = lb
                out["hint"] += " — sınırları: " + "; ".join(f"{x['name']} [{fmt_bounds(x)}]" for x in lb)
    return out


def burn_marks(im, views):
    """Draw the marks (as `mark_view` dicts) onto a PIL image, no fill: each in its own colour (default cue yellow, 3 px at 1000 px wide) over a thin dark
    outline (~50 % black), so every colour reads on light and dark frames."""
    import math
    from PIL import Image, ImageDraw
    w, h = im.size
    lw = max(3, round(w * 0.004))
    over = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)

    def strokes(v, fill, width):
        if v["type"] == "arrow":
            x1, y1, x2, y2 = v["nx1"] * w, v["ny1"] * h, v["nx2"] * w, v["ny2"] * h
            d.line([(x1, y1), (x2, y2)], fill=fill, width=width)
            a = math.atan2(y2 - y1, x2 - x1)
            L = max(12, lw * 5)
            for da in (2.6, -2.6):
                d.line([(x2, y2), (x2 + L * math.cos(a + da), y2 + L * math.sin(a + da))], fill=fill, width=width)
        else:
            box = [v["nx"] * w, v["ny"] * h, (v["nx"] + v["nw"]) * w, (v["ny"] + v["nh"]) * h]
            (d.ellipse if v["type"] == "ellipse" else d.rectangle)(box, outline=fill, width=width)
    for v in views:
        strokes(v, (0, 0, 0, 128), lw + 2)
    for v in views:
        strokes(v, hex_rgb(mark_color(v)) + (255,), lw)
    im.paste(Image.alpha_composite(im.convert("RGBA"), over).convert("RGB"))
    return im


MAX_KEYS = 20


def layer_timing(l, note=None):
    """Compact timing of a layer for note payloads: start / end and, when it is animated, `keys` = [{t, prop: value, ...}] in PROJECT seconds
    (layer-local key times + start), only animated props, rounded, at most MAX_KEYS entries (`keys_note` says when cut)."""
    out = {}
    st, en = l.get("start"), l.get("end")
    if isinstance(st, (int, float)) and isinstance(en, (int, float)):
        out["start"], out["end"] = _r(st, 3), _r(en, 3)
    kf = l.get("keys")
    if isinstance(kf, dict) and isinstance(st, (int, float)):
        by_t = {}
        for prop in sorted(kf):
            for k in kf[prop] if isinstance(kf[prop], list) else []:
                if isinstance(k, dict) and isinstance(k.get("t"), (int, float)) and isinstance(k.get("v"), (int, float)) and not isinstance(k.get("v"), bool):
                    by_t.setdefault(_r(st + k["t"], 3), {})[prop] = _r(k["v"], 3)
        rows = [{"t": t, **by_t[t]} for t in sorted(by_t)]
        if rows:
            out["keys"] = rows[:MAX_KEYS]
            if len(rows) > MAX_KEYS:
                out["keys_note"] = f"showing the first {MAX_KEYS} of {len(rows)} key times; read project.json for the rest"
    return out


def fmt_keys(l):
    """One compact line: `scale 0.6@0.3s→1@0.65s, x 100@0.3s→300@1s`; '' when the layer has no keys."""
    per = {}
    for row in l.get("keys") or []:
        for p, v in row.items():
            if p != "t":
                per.setdefault(p, []).append(f"{v:g}@{row['t']:g}s")
    s = ", ".join(f"{p} " + "→".join(vs) for p, vs in per.items())
    return s + (f" ({l['keys_note']})" if l.get("keys_note") and s else "")


def source_view(l, t, proj=None, d=None):
    """Payload block for a layer made with code (`source`): tool, unit, file, render adaptor name, the source `frame` on screen at project time `t`
    and `time_in_unit` (seconds into the unit). {} when the layer has no usable `source`."""
    s = l.get("source")
    if not isinstance(s, dict):
        return {}
    import source_map
    out = {k: s[k] for k in ("kind", "unit", "file", "render", "root", "elements") if isinstance(s.get(k), str) and s[k]}
    f = source_map.source_frame(l, t)
    if f is not None:
        out["frame"] = int(round(f))
        fps = float(s["fps"])
        out["time_in_unit"] = _r(f / fps - float(s.get("unit_start_frame") or 0) / fps, 3)
        out["fps"] = s["fps"]
    if s.get("elements"):
        er = source_map.elements_at(l, t, (proj or {}).get("width", 1920), (proj or {}).get("height", 1080), d)
        if er.get("error"):
            out["elements_error"] = er["error"]
    return out


def elements_under_mark(proj, t, rects, d=None, lays=None):
    """Elements of the code-made layers active at `t` whose project-px box intersects one of `rects` (x0, y0, x1, y1), smallest first.
    Each: id, label, kind, box [l, t, r, b], file, line, layer (id). `lays` (names / ids) limits it to the layers under the mark when known."""
    import source_map
    W, H = proj.get("width", 1920), proj.get("height", 1080)
    hits = []
    for l in proj.get("layers", []):
        if not isinstance(l, dict) or not isinstance(l.get("source"), dict) or not l["source"].get("elements") or l.get("hidden"):
            continue
        if not (l.get("start", 0) <= t < l.get("end", 0)) or (lays and not ({l.get("name"), l.get("id")} & set(lays))):
            continue
        for e in source_map.elements_at(l, t, W, H, d).get("elements", []):
            if source_map.intersects(e["box"], rects):
                hits.append({**e, "layer": l.get("id")})
    hits.sort(key=lambda e: (e["box"][2] - e["box"][0]) * (e["box"][3] - e["box"][1]))
    return hits


def fmt_element(e):
    loc = e.get("file") or ""
    if loc and e.get("line"):
        loc += f":{e['line']}"
    return f'"{e["label"] or e["id"]}" {e["kind"] or "element"} [{_r(e["box"][0])},{_r(e["box"][1])}→{_r(e["box"][2])},{_r(e["box"][3])}]' + (f" {loc}" if loc else "")


def fmt_source(s):
    loc = f" ({s['file']})" if s.get("file") else ""
    fr = f" frame {s['frame']}" if s.get("frame") is not None else ""
    return f"{s.get('kind', '?')} {s.get('unit', '')}{fr}{loc}".replace("  ", " ") + (f", render: {s['render']}" if s.get("render") else "") + (f" — {s['elements_error']}" if s.get("elements_error") else "")


def layers_under(proj, t, note=None, d=None):
    out = []
    for l in proj.get("layers", []):
        if isinstance(l, dict) and not l.get("hidden") and l.get("start", 0) <= t < l.get("end", 0):
            e = {"id": l.get("id"), "name": l.get("name", ""), "type": l.get("type"), **layer_timing(l)}
            sv = source_view(l, t, proj, d)
            if sv:
                e["source"] = sv
            b = bounds_of(proj, note, l, d) if note is not None else None
            if b:
                e["bounds"] = {k: v for k, v in b.items() if k != "anchor"}
                e["anchor"] = b.get("anchor", "")
            out.append(e)
    return out


def tc(t):
    m = int(t // 60)
    return f"{m}:{t - m * 60:04.1f}"


def _nid(nid):
    if not (isinstance(nid, str) and schema.NOTE_ID_RE.match(nid)):
        raise ValueError(f"unsafe note id: {str(nid)[:40]!r}")
    return nid


safe_nid = _nid                                    # the name the hotfix branch uses


def notes_dir(d):
    """.kurgu/notes, refused when it (or .kurgu) is a symbolic link / junction: the note pictures, signatures and audio blocks are written there."""
    k = os.path.join(d, ".kurgu")
    p = os.path.join(d, FRAMES)
    for x in (k, p):
        if _linkish(x):
            raise KurguRefusal("symlink", f"refusing to write: {x} is a symbolic link", path=_rel(x))
    return p


def audio_path(d, nid):
    return os.path.join(notes_dir(d), _nid(nid) + ".audio.json")


def read_audio(d, n):
    """The audio block saved when the note was sent (see `save_audio`), or [] when none / the note moved since."""
    try:
        with _open_regular(audio_path(d, n["id"])) as f:
            a = json.load(f)
        return a["audio"] if a.get("t") == n.get("t") and isinstance(a.get("audio"), list) else []
    except (OSError, ValueError, KeyError, AttributeError):
        return []


def save_audio(d, proj, n):
    """Measure the sound around note `n` (audio_facts) and keep it next to its frame JPEG. Never raises: audio facts are a bonus."""
    try:
        import audio_facts
        au = audio_facts.audio_block(d, proj, n["t"])
        os.makedirs(os.path.join(d, FRAMES), exist_ok=True)
        path = audio_path(d, n["id"])
        if au:
            tmp = kurgu_paths.tmp_path(path)
            with open_nofollow(tmp, "w") as f:
                json.dump({"t": n["t"], "audio": au}, f, ensure_ascii=False)
            os.replace(tmp, path)
        elif os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


FRAME_PROVIDERS = {}          # project dir -> callable(t) -> PNG bytes, set by the editor server so a Send never calls back into its own HTTP port (deadlock on write_lock)


def pixel_things_under(d, n, m):
    """Fallback for `elements_under` when the mark lies over footage with no element map: OCR text lines and detected shapes under the mark
    (ocr_boxes.things_under_mark, a clean frame at the note time). Each: id, label, kind, box [l, t, r, b] project px, approx True, source "ocr" | "pixels".
    Never raises; [] when no OCR engine / frame is available."""
    try:
        import ocr_boxes
        fp = FRAME_PROVIDERS.get(os.path.realpath(d))
        png = fp(float(n.get("t", 0))) if fp else None
        items = ocr_boxes.things_under_mark(d, n, m, frame_png=png)
    except Exception:
        return []
    out = []
    for k, it in enumerate(items[:8], 1):
        text = it.get("text")
        row = {"id": f"{'ocr' if text else 'px'}-{k}", "label": text if text else str(it.get("kind") or "object"), "kind": "text" if text else str(it.get("kind") or "object"),
               "box": it["box"], "approx": True, "source": "ocr" if text else "pixels"}
        if it.get("color"):
            row["color"] = it["color"]
        out.append(row)
    return out


def note_view(d, n, proj):
    out = {"id": n["id"], "t": n["t"], "timecode": tc(n["t"]), "text": n["text"], "status": status_of(n),
           "layers": layers_under(proj, n["t"], n, d)}
    au = read_audio(d, n)
    if au:
        out["audio"] = au
    if n.get("marks"):
        out["marks"] = [mark_view(m, proj.get("width", 1920), proj.get("height", 1080), proj, n["t"], n, d) for m in n["marks"]]
        if out["status"] == "sent":                                   # what the agent is about to act on: measured elements first, pixels as the fallback
            for m, mv in zip(n["marks"], out["marks"]):
                if not mv.get("elements_under"):
                    ou = pixel_things_under(d, n, m)
                    if ou:
                        mv["elements_under"] = ou
    for k in ("sent", "reply", "resolved_at", "created", "question", "asked", "answer"):
        if n.get(k):
            out[k] = n[k]
    if os.path.exists(os.path.join(d, FRAMES, n["id"] + ".jpg")):
        out["frame"] = frame_rel(n["id"])
    return out


def note_sig(n, proj):
    """What the note's frame JPEG depends on: its time, its marks and the frame size (the project's visuals are not tracked)."""
    import hashlib
    return hashlib.sha1(json.dumps([n.get("t"), n.get("marks") or [], proj.get("width"), proj.get("height")], sort_keys=True).encode()).hexdigest()[:16]


def sig_path(d, nid):
    return os.path.join(notes_dir(d), _nid(nid) + ".sig")


def load_notes(d):
    """(normalised project, notes sorted by time, each note normalised)."""
    proj = schema.normalize(json.load(open(schema.project_path(d), encoding="utf-8")))
    return proj, sorted(proj["notes"], key=lambda n: n["t"])


def write_notes_md(d, proj=None):
    """(Re)write NOTLAR.md from project.json: every note with timecode, text, layers active then, frame image and status."""
    if proj is None:
        proj = schema.normalize(json.load(open(schema.project_path(d), encoding="utf-8")))
    views = [note_view(d, n, proj) for n in sorted(proj["notes"], key=lambda n: n["t"])]
    name = proj.get("name") or os.path.basename(d)
    todo = [v for v in views if v["status"] == "sent"]
    L = [f"# Notlar / Notes: {name}", "",
         "Written by Kurgu. `sent` = the human released it to you, fix these. `asked` = you asked a question, wait for the answer (it comes back as a new send). `open` = still a draft, leave it. `done` = fixed.",
         "Bounds are the rendered box of each layer in project px; x/y of a layer are its anchor (see the line under each bounds). When you move or place something, "
         "align its edges with nearby content, then run `kurgu frame <project_dir> <seconds>` (or MCP `render_frame`) and look before closing.",
         "After fixing: `kurgu notes done <project_dir> <id...> --reply \"what you changed\"` (or MCP `notes_done`).", "",
         f"{len(todo)} to do · {sum(v['status'] == 'open' for v in views)} draft · {sum(v['status'] == 'asked' for v in views)} asked · {sum(v['status'] == 'done' for v in views)} done", ""]
    for v in views:
        L.append(f"## {v['timecode']} · {v['status']} · `{v['id']}`")
        L.append("")
        L.append(v["text"])
        L.append("")
        L.append(f"- time: {v['t']} s")
        L.append("- layers then: " + (", ".join(f"{l['name'] or l['id']} ({l['type']})" for l in v["layers"]) or "none"))
        for l in v["layers"]:
            if l.get("bounds"):
                L.append(f"  - {l['name'] or l['id']} bounds: {fmt_bounds(l['bounds'])} px" + (" (estimate)" if l["bounds"].get("approx") else "") + (f" — {l['anchor']}" if l.get("anchor") else ""))
            if l.get("keys"):
                L.append(f"  - {l['name'] or l['id']} keys: {fmt_keys(l)}")
        for l in v["layers"]:
            if l.get("source"):
                L.append(f"- source: {fmt_source(l['source'])}" + (f" — layer {l['name'] or l['id']}" if len(v["layers"]) > 1 else ""))
        for au in v.get("audio", []):
            import audio_facts          # (small, no ffmpeg: only formats what was measured at send time)
            L.append("- audio: " + audio_facts.summary_line(au))
        if v.get("frame"):
            L.append(f"- frame: ![{v['timecode']}]({v['frame']})" + (" (the marks are drawn on it in yellow)" if v.get("marks") else ""))
        for mk in v.get("marks", []):
            L.append(f"- {mk['hint']}")
            if mk.get("elements_under"):
                L.append("  - under mark: " + "; ".join(fmt_element(e) for e in mk["elements_under"][:8]) + (" (approximate: read from the pixels, not from a source)" if any(e.get("approx") for e in mk["elements_under"]) else " (measured from the source's element map)"))
            L.append("  - coordinates: " + ", ".join(f"{k}={mk[k]}" for k in mk if k not in ("type", "hint", "layers", "center", "layer_bounds", "elements_under")) + " (px in the project frame; n* = 0-1 of its width/height)")
        if v.get("sent"):
            L.append(f"- sent: {v['sent']}")
        if v.get("question"):
            L.append(f"- question: {v['question']}" + (" (waiting for the human's answer)" if v["status"] == "asked" else ""))
        if v.get("answer"):
            L.append(f"- answer: {v['answer']}")
        if v.get("reply"):
            L.append(f"- reply: {v['reply']}")
        L.append("")
    path = os.path.join(d, NOTES_MD)
    tmp = kurgu_paths.tmp_path(path)
    with open_nofollow(tmp, "w") as f:
        f.write("\n".join(L))
    os.replace(tmp, path)
    return path


def seq_path(d):
    return os.path.join(d, ".kurgu", "inbox.seq")


def _read_int(path):
    try:
        with _open_regular(path) as f:
            return int(f.read(64).strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_int(path, v):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = kurgu_paths.tmp_path(path)
    with open_nofollow(tmp, "w") as f:
        f.write(str(int(v)))
    os.replace(tmp, path)


def append_inbox(d, proj, notes, sent_at, recovered=False):
    """One JSON line per send; the line is the wake-up signal for `notes wait`. `seq` only ever grows: the high-water mark lives in
    .kurgu/inbox.seq, so deleting or truncating inbox.jsonl cannot make it restart below the agent's cursor."""
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    seq = max([e["seq"] for e in read_inbox(d)] + [_read_int(seq_path(d))]) + 1
    for n in notes:
        save_audio(d, proj, n)
    entry = {"seq": seq, "sent": sent_at, "project": proj.get("name") or os.path.basename(d), "dir": d,
             "notes": [note_view(d, n, proj) for n in notes]}
    if recovered:
        entry["recovered"] = True
    lead = ""
    try:                                              # a crash mid-write can leave a last line without its newline: never glue onto it
        with _open_regular(inbox_path(d), binary=True) as f:
            f.seek(0, os.SEEK_END)
            if f.tell():
                f.seek(-1, os.SEEK_END)
                lead = "" if f.read(1) == b"\n" else "\n"
    except OSError:
        pass
    with open_nofollow(inbox_path(d), "a") as f:
        f.write(lead + json.dumps(entry, ensure_ascii=False) + "\n")
    _write_int(seq_path(d), seq)
    return entry


def inbox_len(d):
    return max([e["seq"] for e in read_inbox(d)] + [_read_int(seq_path(d))])


def recover_undelivered(d, grace=10.0):
    """A send that stamped `sent` in project.json but crashed before its inbox line was written would never wake the agent: give such notes
    (sent, not done, in no inbox entry, stamped more than `grace` seconds ago) an inbox entry of their own."""
    try:
        proj, notes = load_notes(d)
    except (OSError, ValueError):
        return
    seen = {n["id"] for e in read_inbox(d) for n in e.get("notes", [])}
    now = datetime.now()
    lost = []
    for n in notes:
        if n.get("sent") and not n.get("done") and not n.get("asked") and n["id"] not in seen and n.get("author") != "agent":
            try:
                if (now - datetime.fromisoformat(n["sent"])).total_seconds() < grace:
                    continue
            except ValueError:
                pass
            lost.append(n)
    if lost:
        append_inbox(d, proj, lost, max(n["sent"] for n in lost), recovered=True)


def _live(d, entries):
    """Entries reduced to the notes that still need the agent (sent, not done, still in the project), each note rebuilt from the CURRENT project
    (text / time / marks as they are now; seq and sent time stay the entry's); empty entries are dropped."""
    try:
        proj, notes = load_notes(d)
    except (OSError, ValueError):
        return entries
    now = {n["id"]: n for n in notes if n.get("sent") and not n.get("done") and not n.get("asked")}
    out = []
    for e in entries:
        keep = [note_view(d, now[n["id"]], proj) for n in e.get("notes", []) if n["id"] in now]
        if keep:
            out.append(dict(e, notes=keep))
    return out


# ---------------------------------------------------------------- listener heartbeat: is an agent blocked in `notes wait` right now?
LISTENING = os.path.join(".kurgu", "listening.json")
BEAT_EVERY = 5.0      # a waiting process refreshes its entry this often
BEAT_FRESH = 15.0     # a listener counts as alive while its last beat is younger than this AND its pid runs
_listen_lock = threading.Lock()
_listen_count = {}    # (dir, pid) -> waits of this process in flight (MCP can serve several at once)


def _read_listeners(d):
    try:
        import diag
        with diag.open_regular(os.path.join(d, LISTENING)) as f:     # a FIFO here would block notes wait forever
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError, RecursionError):      # a project may ship any file here (deep nesting, junk)
        return {}


def _write_listeners(d, entries):
    path = os.path.join(d, LISTENING)
    try:
        if not entries:
            os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = kurgu_paths.tmp_path(path)
        with open_nofollow(tmp, "w") as f:
            json.dump(entries, f)
        os.replace(tmp, path)
    except OSError:
        pass                                           # best effort: a heartbeat must never break a wait


def _pid_alive(pid):
    try:
        m = sys.modules.get("__main__")
        if not hasattr(m, "pid_alive"):
            import server as m
        return bool(m.pid_alive(int(pid)))
    except Exception:
        return False


class _FileLock:
    """Cross-process lock on a sidecar file (.kurgu/listening.lock, opened without following symlinks): flock on POSIX, msvcrt.locking on Windows."""
    def __init__(self, d):
        self.path = os.path.join(d, ".kurgu", "listening.lock")
        self.f = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.f = open_nofollow(self.path, "a")
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX)
        except (OSError, ImportError):
            pass                                          # no lock available: carry on unlocked rather than lose the heartbeat
        return self

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        self.f.close()


def _locked_update(d, fn):
    """Read-modify-write of listening.json under the thread lock AND the cross-process lock. Best effort."""
    try:
        with _listen_lock, _FileLock(d):
            e = _read_listeners(d)
            fn(e)
            _write_listeners(d, e)
    except OSError:
        pass


def _num(v):
    """A finite number from a (possibly project-shipped) JSON value, else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        f = float(v)
    except (ValueError, OverflowError):               # junk, or an integer too large for a float
        return None
    return f if math.isfinite(f) else None


def _beat(d, since, agent):
    pid = os.getpid()
    now = time.time()

    def go(e):
        for k in [k for k, v in e.items() if not isinstance(v, dict) or not _pid_alive(v.get("pid", -1)) or (_num(v.get("beat")) is None or now - _num(v.get("beat")) > 10 * BEAT_FRESH)]:
            if k != str(pid):
                del e[k]                                   # dead process or long-stale entry: pruned
        e[str(pid)] = {"pid": pid, "since": since, "beat": now, **({"agent": agent} if agent else {})}
    _locked_update(d, go)


def _unbeat(d):
    _locked_update(d, lambda e: e.pop(str(os.getpid()), None))


def is_listening(d, now=None):
    """True when some process is blocked in `notes wait` / notes_wait for this project: fresh beat AND a running pid."""
    now = time.time() if now is None else now
    for e in _read_listeners(d).values():
        try:
            b = _num(e.get("beat")) if isinstance(e, dict) else None
            if b is not None and 0 <= now - b < BEAT_FRESH and _pid_alive(e["pid"]):
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def wake_configured():
    """A user-level on_send hook exists (the same read run_on_send does: env / user config only)."""
    return on_send_command() is not None


# ---------------------------------------------------------------- which agent session works on this project (.kurgu/agents.json)
# The session id comes from the environment ONLY, and an entry is recorded only when the agent is LISTENING (`kurgu notes wait`, MCP `notes_wait`), never from
# other commands (T2: `kurgu frame` inside a session must not make a wake fork the user's own conversation) and never inside a woken run (KURGU_WAKE).
# Each entry carries an HMAC made with a key in the user's config folder: a project folder cannot ship a forged `.kurgu/agents.json`. The wake hook
# then resumes that exact session instead of "the last one in the folder".
AGENTS = os.path.join(".kurgu", "agents.json")
AGENTS_KEEP = 5
AGENT_FRESH = 24 * 3600.0      # a recorded session older than this is not resumed
_UUID = __import__("re").compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_last_recorded = {}


def env_session(env=None):
    """{agent, session_id, pid, entrypoint} of the agent this process runs inside (Claude Code / Codex), or None. Env only; the id must be a uuid."""
    env = os.environ if env is None else env
    if env.get("KURGU_WAKE"):
        return None                                    # a woken run must not become "the user's session"
    if env.get("CODEX_THREAD_ID") or env.get("CODEX_SESSION_ID"):      # innermost agent wins: Codex started from Claude Code carries both sets of variables
        agent, sid, ep, pid = "codex", env.get("CODEX_THREAD_ID") or env["CODEX_SESSION_ID"], "codex", ""
    elif env.get("CLAUDE_CODE_SESSION_ID"):
        agent, sid, ep, pid = "claude", env["CLAUDE_CODE_SESSION_ID"], env.get("CLAUDE_CODE_ENTRYPOINT", ""), env.get("CLAUDE_PID", "")
    else:
        return None
    if not _UUID.match(sid):
        return None
    return {"agent": agent, "session_id": sid.lower(), "pid": int(pid) if pid.isdigit() else os.getppid(), "entrypoint": ep[:40]}


def read_agents(d):
    try:
        with _open_regular(os.path.join(d, AGENTS)) as f:
            v = json.load(f)
        return [e for e in v if isinstance(e, dict)] if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def _agent_mac(e):
    """HMAC (key in the USER's config folder, 0600) over what makes an entry: a project folder that ships its own `.kurgu/agents.json` cannot forge an entry,
    so a downloaded project cannot choose which session a wake resumes."""
    import hashlib
    import hmac
    key_path = os.path.join(kurgu_paths.config_dir(), "agents.key")
    try:
        with open(key_path, "rb") as f:
            key = f.read()
    except OSError:
        import secrets
        key = secrets.token_bytes(32)
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        except FileExistsError:
            with open(key_path, "rb") as f:
                key = f.read()
    msg = f"{e.get('agent')}|{e.get('session_id')}|{e.get('last_seen')}|{e.get('pid')}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _agent_mac_ok(e):
    import hmac
    try:
        return isinstance(e.get("mac"), str) and hmac.compare_digest(e["mac"], _agent_mac(e))
    except OSError:
        return False


def record_agent(d, env=None):
    """Remember the agent session this process belongs to (atomic, last AGENTS_KEEP). Best effort; at most one write per 30 s per project."""
    cur = env_session(env)
    if not cur or not os.path.isdir(os.path.join(d, ".kurgu")) and not os.path.exists(schema.project_path(d)):
        return
    now = time.time()
    if now - _last_recorded.get((d, cur["session_id"]), 0) < 30:
        return
    _last_recorded[(d, cur["session_id"])] = now
    try:
        with _listen_lock:
            items = [e for e in read_agents(d) if not (e.get("agent") == cur["agent"] and e.get("session_id") == cur["session_id"])]
            ent = dict(cur, last_seen=now)
            ent["mac"] = _agent_mac(ent)
            items.append(ent)
            items = sorted(items, key=lambda e: e.get("last_seen", 0))[-AGENTS_KEEP:]
            os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
            path = os.path.join(d, AGENTS)
            tmp = kurgu_paths.tmp_path(path)
            with open_nofollow(tmp, "w") as f:
                json.dump(items, f)
            os.replace(tmp, path)
    except OSError:
        pass


def latest_session(d, agent, now=None):
    """The most recently seen recorded session of `agent` ("claude" | "codex") within AGENT_FRESH, or None."""
    now = time.time() if now is None else now
    got = [e for e in read_agents(d) if e.get("agent") == agent and isinstance(e.get("session_id"), str) and _UUID.fullmatch(e["session_id"])
           and isinstance(e.get("last_seen"), (int, float)) and -60 < now - e["last_seen"] < AGENT_FRESH       # a last_seen in the future is not a fresh session
           and _agent_mac_ok(e)]
    return max(got, key=lambda e: e["last_seen"]) if got else None


def wake_route(d):
    """What a Send will do when the user's on_send hook runs: {} (no hook) | {"route": "custom"} | {"route": "session"|"new", "agent", "session_id"?}."""
    cmd = on_send_command()
    if not cmd:
        return {}
    if "wake" in cmd[:-1] and cmd[cmd.index("wake") + 1] in ("claude", "codex"):
        agent = cmd[cmd.index("wake") + 1]
        e = latest_session(d, agent)
        return {"route": "session", "agent": agent, "session_id": e["session_id"]} if e else {"route": "new", "agent": agent}
    return {"route": "custom"}


def wait_for_send(d, since=None, timeout=None, poll=0.4, cancel=None, commit=True):
    """Blocking wait (see _wait_loop). While it blocks (timeout != 0) the process advertises itself in .kurgu/listening.json."""
    if timeout == 0:
        return _wait_loop(d, since, timeout, poll, cancel, commit, None)
    key, t0 = (d, os.getpid()), time.time()
    with _listen_lock:
        _listen_count[key] = _listen_count.get(key, 0) + 1
    try:
        return _wait_loop(d, since, timeout, poll, cancel, commit, lambda: _beat(d, t0, agent_name(d) or env_agent_label() or None))
    finally:
        with _listen_lock:
            _listen_count[key] -= 1
            last = _listen_count[key] <= 0
            if last:
                del _listen_count[key]
        if last:
            _unbeat(d)


def _wait_loop(d, since, timeout, poll, cancel, commit, beat):
    """Return the inbox entries the agent has not consumed yet (seq above `since`, default: the saved cursor), blocking until there is one.
    Only notes that are still `sent` and not `done` are returned, rebuilt from the current project. Without `since` the cursor moves to what was
    returned, so a send made while the agent was fixing the previous batch is delivered by the next call. `since` given: the cursor is neither
    read nor moved. timeout None = wait forever, 0 = look once, N = N seconds; None comes back on timeout or when `cancel` (a threading.Event) is
    set. `commit=False` leaves the cursor alone and returns (entries, commit_fn): the caller moves it only once the answer is really delivered."""
    use_cursor = since is None
    base = get_cursor(d) if use_cursor else int(since)
    end = None if timeout is None else time.monotonic() + timeout
    next_recover, last_size, next_beat = 0.0, -1, 0.0
    while True:
        if beat is not None and time.monotonic() >= next_beat:
            beat()
            next_beat = time.monotonic() + BEAT_EVERY
        if time.monotonic() >= next_recover:           # at the start and then every 15 s: a send that never reached the inbox is found while waiting too
            recover_undelivered(d)
            next_recover, last_size = time.monotonic() + RECOVER_EVERY, -1
        try:
            size = os.path.getsize(inbox_path(d))
        except OSError:
            size = 0
        if size != last_size:                          # only re-parse the file when it changed
            last_size = size
            pending = [e for e in read_inbox(d) if e["seq"] > base]
            if pending:
                top = max(e["seq"] for e in pending)
                base = top
                live = _live(d, pending)
                if live:
                    if not use_cursor:
                        return (live, lambda: None) if not commit else live
                    if commit:
                        set_cursor(d, top)
                        return live
                    return live, (lambda top=top: set_cursor(d, top))
                if use_cursor:
                    set_cursor(d, top)                 # nothing live in it (all done meanwhile): consumed
        if end is not None and time.monotonic() >= end:
            return None
        if cancel is not None:
            if cancel.wait(poll):
                return None
        else:
            time.sleep(poll)


def list_views(d, status="sent"):
    proj, notes = load_notes(d)
    return [note_view(d, n, proj) for n in notes if status == "all" or status_of(n) == status]


def mark_done(d, ids, reply, writer):
    """Mark notes done (+ optional reply) through `writer(d, mutate, summary)` (mcp_server._write_notes). Returns the touched notes."""
    ids = list(dict.fromkeys(ids))

    def mutate(notes, proj):
        found = []
        for nid in ids:
            n = next((x for x in notes if x["id"] == nid), None)
            if n is None:
                raise ValueError(f'No note with id "{nid}". Run `kurgu notes list <dir> --all` to see the ids.')
            found.append(n)
        for n in found:
            if not n["done"]:
                n["resolved_at"] = now_iso()
            n["done"] = True
            n.pop("asked", None)
            if reply:
                n["reply"] = reply.strip()
        return found

    done = writer(d, mutate, lambda ns: f"{len(ns)} note(s) done" + (f": {reply.strip()}" if reply else "") + " (" + ", ".join(n["id"] for n in ns) + ")")
    write_notes_md(d)
    return done


def mark_asked(d, ids, question, writer):
    """The agent asks the human instead of guessing: the notes get `question` + `asked` (neither done nor sent) and leave the agent's queue until the
    human answers in the editor (server.App.notes_answer)."""
    ids = list(dict.fromkeys(ids))
    question = (question or "").strip()
    if not question:
        raise ValueError("a question is required (--question \"...\")")

    def mutate(notes, proj):
        found = []
        for nid in ids:
            n = next((x for x in notes if x["id"] == nid), None)
            if n is None:
                raise ValueError(f'No note with id "{nid}". Run `kurgu notes list <dir> --all` to see the ids.')
            if n["done"]:
                raise ValueError(f'Note "{nid}" is already done; add a new note instead of asking on it.')
            found.append(n)
        for n in found:
            n["question"], n["asked"] = question, now_iso()
            n["answer"] = ""
        return found

    got = writer(d, mutate, lambda ns: f"{len(ns)} note(s) asked: {question} (" + ", ".join(n["id"] for n in ns) + ")")
    write_notes_md(d)
    return got


def render_text(views):
    out = []
    for v in views:
        lay = ", ".join(l["name"] or l["id"] for l in v.get("layers", [])) or "-"
        bl = "".join(f"\n    bounds {l['name'] or l['id']}: {fmt_bounds(l['bounds'])} — {l.get('anchor', '')}" for l in v.get("layers", []) if l.get("bounds"))
        bl += "".join(f"\n    keys {l['name'] or l['id']}: {fmt_keys(l)}" for l in v.get("layers", []) if l.get("keys"))
        bl += "".join(f"\n    source {l['name'] or l['id']}: {fmt_source(l['source'])}" for l in v.get("layers", []) if l.get("source"))
        out.append(f"[{v['id']}] {v['timecode']} ({v['t']} s) {v['status']}: {v['text']}\n    layers: {lay}" + bl
                   + "".join(f"\n    {mk['hint']}" + "".join(f"\n      under mark: {fmt_element(e)}" for e in mk.get("elements_under", [])[:8]) for mk in v.get("marks", []))
                   + (f"\n    frame: {v['frame']}" if v.get("frame") else "")
                   + (f"\n    your earlier question: {v['question']}" if v.get("question") else "") + (f"\n    the human's answer: {v['answer']}" if v.get("answer") else "")
                   + (f"\n    reply: {v['reply']}" if v.get("reply") else ""))
    return "\n".join(out)


def render_entries(entries):
    out = []
    for e in entries:
        out.append(f"Send #{e['seq']} · {e['sent']} from \"{e['project']}\" ({len(e['notes'])} notes):\n" + render_text(e["notes"]))
    return "\n\n".join(out) + (f"\n\n(inbox position {entries[-1]['seq']}; after fixing: kurgu notes done <dir> <id...>, then kurgu notes wait <dir> again)" if entries else "")


# ---------------------------------------------------------------- on_send hook: wake the agent when the human presses Send
# Read ONLY from the user's own configuration (env KURGU_ON_SEND, or `on_send` in the user config file); never from the project's .kurgu/config.json,
# because a shared project folder must not be able to run commands. The command is an argv list (no shell); {dir} {seq} {count} {file} are replaced.
HOOK_LOG = os.path.join(".kurgu", "hook.log")
HOOK_TIMEOUT = 3600.0          # a hook run that lasts longer than this is stopped (user config `on_send_timeout` overrides)
_runners = {}
_runners_lock = threading.Lock()


def user_config():
    import kurgu_paths
    try:
        with open(kurgu_paths.config_file(), encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def on_send_command():
    """The configured wake-up command as an argv list, or None. env KURGU_ON_SEND (a JSON array) wins over the user config's `on_send`."""
    raw = os.environ.get("KURGU_ON_SEND", "").strip()
    cmd = None
    if raw:
        try:
            cmd = json.loads(raw)
        except ValueError:
            cmd = None
    else:
        cmd = user_config().get("on_send")
    if isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd) and cmd[0]:
        return cmd
    return None


def expand_command(cmd, d, seq, count, file):
    rep = {"{dir}": d, "{seq}": str(seq), "{count}": str(count), "{file}": file}
    out = []
    for a in cmd:
        for k, v in rep.items():
            a = a.replace(k, v)
        out.append(a)
    return out


def resolve_exe(name):
    """A bare command name as a launchable path. On Windows `codex` / `claude` are npm `.cmd` shims that CreateProcess cannot find by their bare name;
    shutil.which honours PATHEXT. A name that cannot be found is returned unchanged (the start then fails with the system's own message)."""
    import shutil
    return shutil.which(name) or name


def _log(d, text):
    try:
        os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
        with open_nofollow(os.path.join(d, HOOK_LOG), "a") as f:
            f.write(f"[{datetime.now().replace(microsecond=0).isoformat()}] {str(text).replace(chr(10), ' ')}\n")
    except OSError as e:
        print(f"kurgu: could not write {HOOK_LOG}: {e}", file=sys.stderr)
        if isinstance(e, KurguRefusal):
            import diag
            diag.refusal(d, e)


def _pump(stream, log, tail=None):
    try:
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").replace("\r", "").rstrip("\n").replace("\n", " ")
            log.write("| " + line + "\n")
            log.flush()
            if tail is not None:
                tail.append(line)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------- wake status: .kurgu/wake.json (what the editor shows next to the sent notes)
WAKE_JSON = os.path.join(".kurgu", "wake.json")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def _unprefix(line):
    """The hook log marks every line the AGENT printed with "| " (see _pump); strip it where the text is shown or analysed."""
    return line[2:] if line.startswith("| ") else line


def _clean_line(s):
    s = _ANSI.sub("", s)
    s = "".join(ch for ch in s if ch == "\t" or ch >= " ").strip()
    return s if len(s) <= 200 else s[:199] + "…"


def log_tail(d, offset=0, lines=4):
    """The last few meaningful lines the hook run wrote to .kurgu/hook.log after byte `offset`, sanitized (no control codes, short).
    A JSON line with a `result` (claude -p --output-format json) is shown as that text."""
    path = os.path.join(d, HOOK_LOG)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))          # never through a symlink
        with os.fdopen(fd, "rb") as f:
            f.seek(max(0, offset))
            raw = f.read(65536)[-8192:].decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for ln in raw.splitlines():
        ln = _unprefix(ln).strip()
        if not ln or ln.startswith("finished, exit code") or ln.startswith("stopped:"):
            continue
        if ln.startswith("{"):
            try:
                o = json.loads(ln)
                if isinstance(o, dict) and isinstance(o.get("result"), str):
                    ln = o["result"]
            except ValueError:
                pass
        out.extend(x for x in (_clean_line(p) for p in ln.splitlines()) if x)
    return out[-lines:]


CODEX_CAUSE = {"codex_no_auth_file": "Codex keeps its login in the system keyring, so Kurgu's restricted Codex cannot use it",
               "codex_auth_replaced": "Codex replaced the linked login file with its own copy; Kurgu will not overwrite it",
               "codex_prepare_failed": "Kurgu could not prepare its restricted Codex folder",
               "codex_policy": "Kurgu could not apply its command rules to Codex"}


def wake_reason(rc, tail):
    """A short cause key for the editor's hint: "login" (agent not signed in), "missing" (command not found), or ""."""
    txt = " ".join(tail).lower()
    m = re.search(r"codex not started \((\w+)\)", txt)
    if m or rc == 78:                                  # `kurgu wake codex` could not set up its command-rules home (codex_policy.PolicyProblem)
        return "codex_" + (m.group(1) if m else "policy")
    if "not logged in" in txt or "/login" in txt or "please log in" in txt or "unauthorized" in txt:
        return "login"
    if rc == 127 or "command not found" in txt or "no such file" in txt or "cannot find" in txt or "not found" in txt:
        return "missing"
    return ""


def write_wake(d, state):
    """Record the wake run's status as .kurgu/wake.json. Never follows a symlink (the file or the folder); failures are ignored."""
    kdir = os.path.join(d, ".kurgu")
    try:
        os.makedirs(kdir, exist_ok=True)
        if os.path.islink(kdir):
            return
        path = os.path.join(d, WAKE_JSON)
        tmp = kurgu_paths.tmp_path(path, ".part")
        with kurgu_paths.excl_open(tmp) as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)                           # replaces a planted symlink itself, never writes through it
    except OSError:
        pass


def read_wake(d):
    path = os.path.join(d, WAKE_JSON)
    try:
        import diag
        with diag.open_regular(path) as f:                     # a regular file only: a shipped FIFO / device / symlink is refused, never waited on
            o = json.loads(f.read(20000))
        return o if isinstance(o, dict) else None
    except (OSError, ValueError, RecursionError, UnicodeError):     # junk, deep nesting or a symlink: no status
        return None


def wake_status(d):
    """The status the editor polls: {} (no wake run on record) or {state: running | finished | stopped, seq, count, started, ended, exit_code, reason,
    summary, tail}. A `running` record with no live runner (the server restarted meanwhile) is reported as nothing."""
    o = read_wake(d)
    if not o:
        return {}
    st = o.get("state")
    if st == "running":
        with _runners_lock:
            r = _runners.get(d)
        if not (r and r.running):
            return {}
    if st not in ("running", "finished", "stopped"):
        return {}
    out = {"state": st}
    for k in ("seq", "count"):
        out[k] = o[k] if isinstance(o.get(k), int) else 0
    for k in ("started", "ended", "reason", "summary"):
        out[k] = str(o.get(k) or "")[:200]
    out["exit_code"] = o["exit_code"] if isinstance(o.get("exit_code"), int) else None
    out["tail"] = [_clean_line(str(x)) for x in (o.get("tail") or [])[:4]] if isinstance(o.get("tail"), list) else []
    return out


def hook_log_text(d, limit=16000):
    path = os.path.join(d, HOOK_LOG)
    try:
        with _open_regular(path, binary=True) as f:                  # regular file only: a shipped FIFO must not hang the endpoint
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit))
            return "\n".join(_unprefix(l) for l in f.read().decode("utf-8", "replace").splitlines()) + "\n"
    except OSError:
        return ""


class _Runner:
    def __init__(self, d):
        self.d, self.lock, self.running, self.pending = d, threading.Lock(), False, None

    @staticmethod
    def _shown(cmd, argv):
        """The command as written to hook.log: the default `kurgu wake <agent> <dir> <seq>` in full (no secrets in it), a custom on_send only as exe + argument count."""
        if "wake" in cmd[:-1] and cmd[cmd.index("wake") + 1] in ("claude", "codex"):
            return " ".join(argv)
        return f"{os.path.basename(argv[0])} (custom on_send, {len(argv) - 1} arguments)"

    def _spawn(self, cmd, seq, count):
        import subprocess
        file = os.path.join(self.d, NOTES_MD)
        argv = expand_command(cmd, self.d, seq, count, file)
        argv[0] = resolve_exe(argv[0])
        env = dict(os.environ, KURGU_PROJECT_DIR=self.d, KURGU_SEQ=str(seq), KURGU_NOTES_COUNT=str(count), KURGU_NOTES_FILE=file)
        why = shim_refusal(argv[0], self.d)
        if why:
            _log(self.d, str(why))
            print(f"kurgu: {why}", file=sys.stderr)
            import diag
            diag.refusal(self.d, why)
            raise why
        os.makedirs(os.path.join(self.d, ".kurgu"), exist_ok=True)
        try:
            log = open_nofollow(os.path.join(self.d, HOOK_LOG), "a")
        except OSError as e:
            print(f"kurgu: {e}; the on_send hook was not started", file=sys.stderr)
            if isinstance(e, KurguRefusal):
                import diag
                diag.refusal(self.d, e)
            raise
        log.write(f"[{datetime.now().replace(microsecond=0).isoformat()}] run (send #{seq}, {count} notes): {self._shown(cmd, argv)}".replace("\n", " ") + "\n")
        log.flush()
        meta = {"seq": seq, "count": count, "started": datetime.now().replace(microsecond=0).isoformat(), "offset": log.tell()}
        write_wake(self.d, dict(meta, state="running"))
        kw = {"creationflags": 0x00000200} if os.name == "nt" else {"start_new_session": True}      # its own process group: stoppable as a whole
        try:
            p = subprocess.Popen(argv, cwd=self.d, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
        except OSError as e:
            log.write(f"  could not start: {e}\n")
            log.close()
            write_wake(self.d, dict(meta, state="finished", exit_code=127, ended=datetime.now().replace(microsecond=0).isoformat(),
                                    reason="missing", summary=_clean_line(str(e)), tail=[_clean_line(str(e))]))
            raise
        p.tail = collections.deque(maxlen=5)
        import diag
        diag.event(self.d, "wake", "hook run started", ctx={"seq": seq, "notes": count, "exe": os.path.basename(argv[0])})
        p.pump = threading.Thread(target=_pump, args=(p.stdout, log, p.tail), daemon=True)      # the agent's output goes in line by line, each prefixed "| ": it can never forge a log line
        p.pump.start()
        threading.Thread(target=self._wait, args=(p, log, cmd, meta), daemon=True).start()

    def _wait(self, p, log, cmd, meta=None):
        import subprocess
        limit = HOOK_TIMEOUT
        try:
            v = user_config().get("on_send_timeout")
            limit = float(v) if v else HOOK_TIMEOUT
        except (TypeError, ValueError):
            pass
        state = {"state": "finished", "exit_code": None}
        try:
            rc = p.wait(limit)
            p.pump.join(5)
            log.write(f"  finished, exit code {rc}\n")
            state["exit_code"] = rc
            import diag
            diag.event(self.d, "wake", "hook run finished", "info" if rc == 0 else "warn", ctx={"exit": rc, "cause": diag.cause_from(p.tail)})
        except subprocess.TimeoutExpired:
            state["state"] = "stopped"
            try:
                if os.name == "nt":
                    p.kill()
                else:
                    import signal
                    os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                pass
            p.pump.join(2)
            log.write(f"  stopped: still running after {limit:g} s\n")
            import diag
            diag.event(self.d, "wake", "hook run stopped (timeout)", "warn", ctx={"limit_s": limit, "cause": diag.cause_from(p.tail)})
        finally:
            log.close()
        if meta:
            tail = log_tail(self.d, meta.get("offset", 0))
            rc = state["exit_code"]
            bad = state["state"] == "stopped" or (rc not in (None, 0))
            summ = next((x for x in reversed(tail) if re.search(r"error|not logged|login|failed|not found|no such|denied", x, re.I)), tail[-1] if tail else "")
            why = wake_reason(rc, tail) if bad else ""
            summ = CODEX_CAUSE.get(why, summ)
            write_wake(self.d, dict({k: v for k, v in meta.items() if k != "offset"}, ended=datetime.now().replace(microsecond=0).isoformat(),
                                    reason=wake_reason(rc, tail) if bad else "", summary=summ if bad else "", tail=tail if bad else [], **state))
        with self.lock:
            nxt, self.pending = self.pending, None
            if nxt is None:
                self.running = False
                return
        try:                                                  # sends that arrived meanwhile: ONE follow-up run, with the latest numbers
            self._spawn(cmd, *nxt)
        except OSError:
            with self.lock:
                self.running = False
            return
        # (stays `running` until that run ends)

    def trigger(self, cmd, seq, count):
        with self.lock:
            if self.running:
                self.pending = (seq, count)
                return {"hook": "queued"}
            self.running = True
        try:
            self._spawn(cmd, seq, count)
        except OSError as e:
            with self.lock:
                self.running = False
            res = {"hook": "error", "error": str(e)}
            if isinstance(e, KurguRefusal):
                res["reason"] = {"code": e.code, "params": e.params}
            return res
        return {"hook": "started"}


def run_on_send(d, seq, count):
    """Fire the user's on_send hook after a send: detached, never blocks. Returns {} (no hook configured) or {"hook": "started" | "queued" | "error", ...}."""
    cmd = on_send_command()
    if not cmd:
        return {}
    with _runners_lock:
        r = _runners.setdefault(d, _Runner(d))
    res = r.trigger(cmd, seq, count)
    if res.get("hook") == "error":
        _log(d, f"hook failed to start: {res['error']}")
    return res


# ---------------------------------------------------------------- CLI
def _project_dir(p):
    d = os.path.realpath(os.path.expanduser(p))
    if not os.path.exists(schema.project_path(d)):
        raise SystemExit(f"No project.json in {d}")
    return d


def add_user_note(d, t, text, rect, writer):
    """A note as the human's editor would make it (author user, layers under the time, an optional rectangle mark with normalised coordinates)."""
    import uuid
    if not math.isfinite(t) or t < 0 or not text.strip():
        raise ValueError("--t must be a finite number >= 0 and --text must not be empty")
    mark = None
    if rect:
        try:
            x, y, w, h = [float(v) for v in rect.split(",")]
        except ValueError:
            raise ValueError("--rect is x,y,w,h in project px (four numbers)")
        if not all(math.isfinite(v) for v in (x, y, w, h)):
            raise ValueError("--rect needs finite numbers")
        if w <= 0 or h <= 0:
            raise ValueError("--rect needs a positive width and height")
        mark = {"type": "rect", "x": x, "y": y, "w": w, "h": h}

    def mutate(notes, proj):
        W, H = proj.get("width", 1920), proj.get("height", 1080)
        ids = {x["id"] for x in notes}
        nid = "n_" + uuid.uuid4().hex[:4]
        while nid in ids:
            nid = "n_" + uuid.uuid4().hex[:6]
        n = {"id": nid, "t": round(float(t), 2), "text": text.strip(), "created": now_iso(), "author": "user",
             "layer_ids": [l["id"] for l in proj.get("layers", []) if l.get("start", 0) <= t < l.get("end", 0)]}
        if mark:
            n["marks"] = [dict(mark, nx=mark["x"] / W, ny=mark["y"] / H, nw=mark["w"] / W, nh=mark["h"] / H)]
        n = schema.normalize_note(n)
        notes.append(n)
        return n
    return writer(d, mutate, lambda n: f'note added @ {n["t"]:g}s: {n["text"]}')


def build_parsers():
    """(parser, {name: subparser}) for `kurgu notes ...`. mcp_server derives the notes_* tool schemas from these, so a flag added here reaches MCP
    (tests/test_guidance_sync.py fails until the tool maps or skips it)."""
    ap = argparse.ArgumentParser(prog="kurgu notes", description="Notes the human sent from the editor (watch + note mode).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", help="list notes (default: sent = the ones to fix now)")
    pl.add_argument("project_dir", help="project folder")
    g = pl.add_mutually_exclusive_group()
    g.add_argument("--open", action="store_true", help="drafts: written but not sent yet")
    g.add_argument("--sent", action="store_true", help="sent and not done (default)")
    g.add_argument("--asked", action="store_true", help="waiting for the human's answer to your question")
    g.add_argument("--done", action="store_true", help="already fixed")
    g.add_argument("--all", action="store_true", help="every note")
    pl.add_argument("--json", action="store_true")
    pw = sub.add_parser("wait", help="block until the human clicks Send, print the new notes, exit 0")
    pw.add_argument("project_dir")
    pw.add_argument("--timeout", type=float, default=None, help="seconds; omitted = wait forever, 0 = look once without waiting; exit code 3 on timeout")
    pw.add_argument("--since", type=int, default=None, help="return sends with seq above this (the saved cursor is then neither read nor moved)")
    pw.add_argument("--json", action="store_true")
    pd = sub.add_parser("done", help="mark notes done, optionally with a one-line reply the human sees")
    pd.add_argument("project_dir")
    pd.add_argument("ids", nargs="+", help="note ids (from notes list / notes wait)")
    pd.add_argument("--reply", default="", help="one-line reply the human sees under the note")
    pa = sub.add_parser("ask", help="ask the human a question instead of guessing: the note stays open (state: asked) until he answers in the editor")
    pa.add_argument("project_dir")
    pa.add_argument("ids", nargs="+", help="note ids (from notes list / notes wait)")
    pa.add_argument("--question", required=True, help="one short concrete question, ideally with options")
    pn = sub.add_parser("add", help="add a note as the human would (for scripts and tests); it stays a draft until it is sent from the editor or POST /api/notes/send")
    pn.add_argument("project_dir")
    pn.add_argument("--t", type=float, required=True, help="timeline time in seconds")
    pn.add_argument("--text", required=True, help="the note")
    pn.add_argument("--rect", default=None, help="a rectangle mark in project px: x,y,w,h (left, top, width, height)")
    return ap, {"list": pl, "wait": pw, "done": pd, "ask": pa, "add": pn}


def main(argv=None):
    try:
        return _main(argv)
    except KurguRefusal as e:                       # a symlink under .kurgu etc.: say so, no traceback
        print(f"error: {e}", file=sys.stderr)
        try:
            import diag
            diag.refusal(_project_dir_quiet(argv), e)
        except Exception:
            pass
        return 1
    except OSError as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


def _project_dir_quiet(argv):
    for x in (argv if argv is not None else sys.argv[1:]):
        if os.path.isdir(x):
            return os.path.realpath(x)
    return os.getcwd()


def _main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap, _subs = build_parsers()
    a = ap.parse_args(argv)
    d = _project_dir(a.project_dir)
    if a.cmd == "wait" and a.timeout != 0:       # only an agent that is really LISTENING is the session to wake: not one that merely ran `kurgu frame` or `notes list`
        record_agent(d)
    if a.cmd == "list":
        st = "all" if a.all else "open" if a.open else "asked" if a.asked else "done" if a.done else "sent"
        views = list_views(d, st)
        if a.json:
            print(json.dumps({"status": st, "count": len(views), "notes": views}, ensure_ascii=False, indent=2))
        else:
            print(render_text(views) if views else f"No {st} notes.")
        return 0
    if a.cmd == "wait":
        entries = wait_for_send(d, since=a.since, timeout=a.timeout)
        if entries is None:
            print(f"timeout: nothing new was sent (inbox position {inbox_len(d)})", file=sys.stderr)
            return 3
        if a.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
        else:
            print(render_entries(entries))
        return 0
    import mcp_server
    if a.cmd == "add":
        try:
            n = add_user_note(d, a.t, a.text, a.rect, mcp_server._write_notes)
        except (ValueError, mcp_server.ToolError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"note {n['id']} added at {n['t']:g} s" + (" with a rectangle mark" if n.get("marks") else "") + " (a draft: send it from the editor)")
        return 0
    if a.cmd == "ask":
        try:
            got = mark_asked(d, a.ids, a.question, mcp_server._write_notes)
        except (ValueError, mcp_server.ToolError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"{len(got)} note(s) marked asked (waiting for the human): " + ", ".join(n["id"] for n in got))
        return 0
    try:
        done = mark_done(d, a.ids, a.reply, mcp_server._write_notes)
    except (ValueError, mcp_server.ToolError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{len(done)} note(s) marked done: " + ", ".join(n["id"] for n in done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
