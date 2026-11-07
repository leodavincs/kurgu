"""agent_notes.py: the "watch + note + send" loop between the human and a coding agent.

The human watches the video in the editor, drops timestamped notes and clicks Send. The editor server (server.App.notes_send)
marks those notes `sent` in project.json, saves a JPEG of each frame under .kurgu/notes/, rewrites NOTLAR.md (readable by both)
and appends ONE JSON line per send to .kurgu/inbox.jsonl. The agent blocks on `kurgu notes wait <dir>` (or the MCP tool
`notes_wait`) and is woken by that line; it fixes the notes, then `kurgu notes done <dir> <id...> --reply "..."`, then waits again. Every inbox
entry has a monotonically increasing `seq`; `.kurgu/inbox.cursor` holds the last seq the agent consumed, so a send made WHILE the agent was
fixing is returned by the next wait instead of being lost.

Note status (derived, nothing extra stored): done -> "done"; else sent timestamp set -> "sent"; else "open" (written, not sent yet).

CLI:  kurgu notes list <dir> [--open|--sent|--all] [--json]
      kurgu notes wait <dir> [--timeout S] [--since SEQ] [--json]
      kurgu notes done <dir> <id...> [--reply "text"]
Pure standard library plus schema; no network. The project file is written through the running editor when there is one
(so the editor reloads live), else directly."""
import argparse
import collections
import json
import os
import sys
import threading
import time
from datetime import datetime

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
    if os.path.basename(parent) == ".kurgu" and os.path.islink(parent):
        raise KurguRefusal("symlink", f"refusing to write: {parent} is a symbolic link", path=".kurgu")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if mode == "a" else os.O_TRUNC) | getattr(os, "O_NOFOLLOW", 0)
    if not hasattr(os, "O_NOFOLLOW") and os.path.islink(path):
        raise KurguRefusal("symlink", f"refusing to write: {path} is a symbolic link", path=_rel(path))
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as e:
        if os.path.islink(path):
            raise KurguRefusal("symlink", f"refusing to write: {path} is a symbolic link", path=_rel(path)) from e
        raise
    return os.fdopen(fd, mode, encoding="utf-8")


WATCHED = ("inbox.jsonl", "inbox.seq", "inbox.cursor", "inbox.cursor.tmp", "inbox.seq.tmp")      # a Send cannot record notes through these
SIDE_FILES = ("hook.log", "listening.json", "listening.lock", "log.jsonl")                        # only reported (the hook / heartbeat / diagnostics skip them)


def check_writable(d, side=False):
    """Raise KurguRefusal(symlink) when `.kurgu` or a file Kurgu writes in it is a symbolic link. Called before a Send records anything."""
    k = os.path.join(d, ".kurgu")
    if os.path.islink(k):
        raise KurguRefusal("symlink", f"refusing to write: {k} is a symbolic link", path=".kurgu")
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
    return "done" if n.get("done") else "sent" if n.get("sent") else "open"


def agent_name(d):
    """Display name of the agent the Send button talks to: $KURGU_AGENT_NAME, else `agent_name` in .kurgu/config.json, else ''."""
    v = os.environ.get("KURGU_AGENT_NAME", "").strip()
    if v:
        return v
    try:
        with open(os.path.join(d, ".kurgu", "config.json"), encoding="utf-8") as f:
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
            with open(os.path.join(d, ".kurgu", "config.json"), encoding="utf-8") as f:
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
        with open(inbox_path(d), encoding="utf-8") as f:
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
    tmp = cursor_path(d) + ".tmp"
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


def _box_of(l, W, H):
    """Approximate axis-aligned box (x0, y0, x1, y1) of a visual layer in project pixels, from its static x / y / scale props (keyframes, rotation
    and anchors are ignored). Video / image without a known box cover the frame size; text is estimated from its size and longest line."""
    cx, cy = float(l.get("x", W / 2)), float(l.get("y", H / 2))
    k = float(l.get("scale", 1) or 1)
    sx, sy = k * float(l.get("scale_x", 1) or 1), k * float(l.get("scale_y", 1) or 1)
    if l.get("type") == "text":
        size = float(l.get("size", 48) or 48)
        lines = str(l.get("text", "")).split("\n") or [""]
        w, h = max(len(x) for x in lines) * size * 0.55, len(lines) * size * 1.25
    else:
        w, h = W, H
    w, h = abs(w * sx), abs(h * sy)
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def layers_under_mark(proj, t, pts):
    """Names of the visual layers active at `t` whose box contains one of `pts` (project pixels), front-most first; layers hit by the first point
    (the shape's centre / an arrow's tip) come before the others. Colour and audio layers are left out."""
    W, H = float(proj.get("width", 1920)), float(proj.get("height", 1080))
    hits = []
    for i, l in enumerate(proj.get("layers", [])):
        if l.get("type") not in ("video", "image", "text") or l.get("hidden") or not (l.get("start", 0) <= t < l.get("end", 0)):
            continue
        x0, y0, x1, y1 = _box_of(l, W, H)
        idx = next((j for j, (px, py) in enumerate(pts) if x0 <= px <= x1 and y0 <= py <= y1), None)
        if idx is not None:
            hits.append((0 if idx == 0 else 1, i, l.get("name") or l.get("id")))
    return [n for _f, _i, n in sorted(hits)]


def mark_view(m, W, H, proj=None, t=0.0):
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
        lays = layers_under_mark(proj, t, pts)
    out["layers"] = lays
    if lays:
        out["hint"] += " — altındaki katmanlar (üstten alta): " + ", ".join(lays)
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


def layers_under(proj, t):
    return [{"id": l.get("id"), "name": l.get("name", ""), "type": l.get("type")} for l in proj.get("layers", [])
            if isinstance(l, dict) and not l.get("hidden") and l.get("start", 0) <= t < l.get("end", 0)]


def tc(t):
    m = int(t // 60)
    return f"{m}:{t - m * 60:04.1f}"


def note_view(d, n, proj):
    out = {"id": n["id"], "t": n["t"], "timecode": tc(n["t"]), "text": n["text"], "status": status_of(n),
           "layers": layers_under(proj, n["t"])}
    if n.get("marks"):
        out["marks"] = [mark_view(m, proj.get("width", 1920), proj.get("height", 1080), proj, n["t"]) for m in n["marks"]]
    for k in ("sent", "reply", "resolved_at", "created"):
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
    return os.path.join(d, FRAMES, nid + ".sig")


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
         "Written by Kurgu. `sent` = the human released it to you, fix these. `open` = still a draft, leave it. `done` = fixed.",
         "After fixing: `kurgu notes done <project_dir> <id...> --reply \"what you changed\"` (or MCP `notes_done`).", "",
         f"{len(todo)} to do · {sum(v['status'] == 'open' for v in views)} draft · {sum(v['status'] == 'done' for v in views)} done", ""]
    for v in views:
        L.append(f"## {v['timecode']} · {v['status']} · `{v['id']}`")
        L.append("")
        L.append(v["text"])
        L.append("")
        L.append(f"- time: {v['t']} s")
        L.append("- layers then: " + (", ".join(f"{l['name'] or l['id']} ({l['type']})" for l in v["layers"]) or "none"))
        if v.get("frame"):
            L.append(f"- frame: ![{v['timecode']}]({v['frame']})" + (" (the marks are drawn on it in yellow)" if v.get("marks") else ""))
        for mk in v.get("marks", []):
            L.append(f"- {mk['hint']}")
            L.append("  - coordinates: " + ", ".join(f"{k}={mk[k]}" for k in mk if k not in ("type", "hint", "layers", "center")) + " (px in the project frame; n* = 0-1 of its width/height)")
        if v.get("sent"):
            L.append(f"- sent: {v['sent']}")
        if v.get("reply"):
            L.append(f"- reply: {v['reply']}")
        L.append("")
    path = os.path.join(d, NOTES_MD)
    tmp = path + ".tmp"
    with open_nofollow(tmp, "w") as f:
        f.write("\n".join(L))
    os.replace(tmp, path)
    return path


def seq_path(d):
    return os.path.join(d, ".kurgu", "inbox.seq")


def _read_int(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_int(path, v):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open_nofollow(tmp, "w") as f:
        f.write(str(int(v)))
    os.replace(tmp, path)


def append_inbox(d, proj, notes, sent_at, recovered=False):
    """One JSON line per send; the line is the wake-up signal for `notes wait`. `seq` only ever grows: the high-water mark lives in
    .kurgu/inbox.seq, so deleting or truncating inbox.jsonl cannot make it restart below the agent's cursor."""
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    seq = max([e["seq"] for e in read_inbox(d)] + [_read_int(seq_path(d))]) + 1
    entry = {"seq": seq, "sent": sent_at, "project": proj.get("name") or os.path.basename(d), "dir": d,
             "notes": [note_view(d, n, proj) for n in notes]}
    if recovered:
        entry["recovered"] = True
    lead = ""
    try:                                              # a crash mid-write can leave a last line without its newline: never glue onto it
        with open(inbox_path(d), "rb") as f:
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
        if n.get("sent") and not n.get("done") and n["id"] not in seen and n.get("author") != "agent":
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
    now = {n["id"]: n for n in notes if n.get("sent") and not n.get("done")}
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
        with open(os.path.join(d, LISTENING), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_listeners(d, entries):
    path = os.path.join(d, LISTENING)
    try:
        if not entries:
            os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
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


def _beat(d, since, agent):
    pid = os.getpid()
    now = time.time()

    def go(e):
        for k in [k for k, v in e.items() if not isinstance(v, dict) or not _pid_alive(v.get("pid", -1)) or now - float(v.get("beat", 0) or 0) > 10 * BEAT_FRESH]:
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
            if 0 <= now - float(e["beat"]) < BEAT_FRESH and _pid_alive(e["pid"]):
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def wake_configured():
    """A user-level on_send hook exists (the same read run_on_send does: env / user config only)."""
    return on_send_command() is not None


def wait_for_send(d, since=None, timeout=None, poll=0.4, cancel=None, commit=True):
    """Blocking wait (see _wait_loop). While it blocks (timeout != 0) the process advertises itself in .kurgu/listening.json."""
    if timeout == 0:
        return _wait_loop(d, since, timeout, poll, cancel, commit, None)
    key, t0 = (d, os.getpid()), time.time()
    with _listen_lock:
        _listen_count[key] = _listen_count.get(key, 0) + 1
    try:
        return _wait_loop(d, since, timeout, poll, cancel, commit, lambda: _beat(d, t0, agent_name(d) or None))
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
            if reply:
                n["reply"] = reply.strip()
        return found

    done = writer(d, mutate, lambda ns: f"{len(ns)} note(s) done" + (f": {reply.strip()}" if reply else "") + " (" + ", ".join(n["id"] for n in ns) + ")")
    write_notes_md(d)
    return done


def render_text(views):
    out = []
    for v in views:
        lay = ", ".join(l["name"] or l["id"] for l in v.get("layers", [])) or "-"
        out.append(f"[{v['id']}] {v['timecode']} ({v['t']} s) {v['status']}: {v['text']}\n    layers: {lay}"
                   + "".join(f"\n    {mk['hint']}" for mk in v.get("marks", []))
                   + (f"\n    frame: {v['frame']}" if v.get("frame") else "") + (f"\n    reply: {v['reply']}" if v.get("reply") else ""))
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
        with open(os.path.join(kurgu_paths.config_dir(), "config.json"), encoding="utf-8") as f:
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


class _Runner:
    def __init__(self, d):
        self.d, self.lock, self.running, self.pending = d, threading.Lock(), False, None

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
        log.write(f"[{datetime.now().replace(microsecond=0).isoformat()}] run (send #{seq}, {count} notes): {' '.join(argv)}".replace("\n", " ") + "\n")
        log.flush()
        kw = {"creationflags": 0x00000200} if os.name == "nt" else {"start_new_session": True}      # its own process group: stoppable as a whole
        try:
            p = subprocess.Popen(argv, cwd=self.d, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
        except OSError as e:
            log.write(f"  could not start: {e}\n")
            log.close()
            raise
        p.tail = collections.deque(maxlen=5)
        import diag
        diag.event(self.d, "wake", "hook run started", ctx={"seq": seq, "notes": count, "exe": os.path.basename(argv[0])})
        p.pump = threading.Thread(target=_pump, args=(p.stdout, log, p.tail), daemon=True)      # the agent's output goes in line by line, each prefixed "| ": it can never forge a log line
        p.pump.start()
        threading.Thread(target=self._wait, args=(p, log, cmd), daemon=True).start()

    def _wait(self, p, log, cmd):
        import subprocess
        limit = HOOK_TIMEOUT
        try:
            v = user_config().get("on_send_timeout")
            limit = float(v) if v else HOOK_TIMEOUT
        except (TypeError, ValueError):
            pass
        try:
            rc = p.wait(limit)
            p.pump.join(5)
            log.write(f"  finished, exit code {rc}\n")
            import diag
            diag.event(self.d, "wake", "hook run finished", "info" if rc == 0 else "warn", ctx={"exit": rc, "tail": " / ".join(p.tail)})
        except subprocess.TimeoutExpired:
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
            diag.event(self.d, "wake", "hook run stopped (timeout)", "warn", ctx={"limit_s": limit, "tail": " / ".join(p.tail)})
        finally:
            log.close()
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


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="kurgu notes", description="Notes the human sent from the editor (watch + note mode).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", help="list notes (default: sent = the ones to fix now)")
    pl.add_argument("project_dir")
    g = pl.add_mutually_exclusive_group()
    g.add_argument("--open", action="store_true", help="drafts: written but not sent yet")
    g.add_argument("--sent", action="store_true", help="sent and not done (default)")
    g.add_argument("--all", action="store_true")
    pl.add_argument("--json", action="store_true")
    pw = sub.add_parser("wait", help="block until the human clicks Send, print the new notes, exit 0")
    pw.add_argument("project_dir")
    pw.add_argument("--timeout", type=float, default=None, help="seconds; omitted = wait forever, 0 = look once without waiting; exit code 3 on timeout")
    pw.add_argument("--since", type=int, default=None, help="return sends with seq above this (the saved cursor is then neither read nor moved)")
    pw.add_argument("--json", action="store_true")
    pd = sub.add_parser("done", help="mark notes done, optionally with a one-line reply the human sees")
    pd.add_argument("project_dir")
    pd.add_argument("ids", nargs="+")
    pd.add_argument("--reply", default="")
    a = ap.parse_args(argv)
    d = _project_dir(a.project_dir)
    if a.cmd == "list":
        st = "all" if a.all else "open" if a.open else "sent"
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
    try:
        done = mark_done(d, a.ids, a.reply, mcp_server._write_notes)
    except (ValueError, mcp_server.ToolError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{len(done)} note(s) marked done: " + ", ".join(n["id"] for n in done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
