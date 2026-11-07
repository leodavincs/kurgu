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
import json
import os
import sys
import time
from datetime import datetime

import schema

RECOVER_EVERY = 15.0          # seconds between "sent but never announced" checks while waiting
NOTES_MD = "NOTLAR.md"
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
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(int(seq)))
    os.replace(tmp, cursor_path(d))


# ---------------------------------------------------------------- marks (circles / boxes / arrows drawn on the frame to say WHERE)
MARK_TYPES = ("ellipse", "rect", "arrow")
MARK_COLOR = (243, 195, 75)          # cue yellow, same as the editor's --note


def _r(v, n=0):
    v = round(float(v), n)
    return int(v) if n == 0 else v


def _region(cx, cy):
    """Plain-language place of a point given as 0-1 coordinates: the quarter of the frame, or the centre."""
    if 0.4 <= cx <= 0.6 and 0.4 <= cy <= 0.6:
        return "merkez"
    return f'{"sol" if cx < 0.5 else "sağ"} {"üst" if cy < 0.5 else "alt"} çeyrek'


def mark_view(m, W, H):
    """One mark for agents: pixel + normalised coordinates (project space W x H), a plain-language hint and the layers under it (topmost first)."""
    typ = m.get("type")
    W, H = max(1.0, float(W)), max(1.0, float(H))
    out = {"type": typ}
    def pick(nk, pk, size):   # the stored normalised value wins (it survives a resolution change); pixels are then recomputed from the current size
        v = m.get(nk)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float(m.get(pk, 0)) / size
    if typ == "arrow":
        n1, m1, n2, m2 = pick("nx1", "x1", W), pick("ny1", "y1", H), pick("nx2", "x2", W), pick("ny2", "y2", H)
        x1, y1, x2, y2 = n1 * W, m1 * H, n2 * W, m2 * H
        out.update(x1=_r(x1), y1=_r(y1), x2=_r(x2), y2=_r(y2), nx1=_r(n1, 4), ny1=_r(m1, 4), nx2=_r(n2, 4), ny2=_r(m2, 4))
        out["hint"] = f"işaret: ok, ({_r(x1)}, {_r(y1)}) noktasından ({_r(x2)}, {_r(y2)}) noktasına — ucu {_region(x2 / W, y2 / H)}"
    else:
        nx, ny, nw, nh = pick("nx", "x", W), pick("ny", "y", H), pick("nw", "w", W), pick("nh", "h", H)
        x, y, w, h = nx * W, ny * H, nw * W, nh * H
        cx, cy = x + w / 2, y + h / 2
        out.update(x=_r(x), y=_r(y), w=_r(w), h=_r(h), nx=_r(nx, 4), ny=_r(ny, 4), nw=_r(nw, 4), nh=_r(nh, 4), center=[_r(cx), _r(cy)])
        name = "elips" if typ == "ellipse" else "dikdörtgen"
        out["hint"] = f"işaret: {name}, merkez ({_r(cx)}, {_r(cy)}), {_r(w)}x{_r(h)} px — {_region(cx / W, cy / H)}"
    lays = [l.get("name") or l.get("id") for l in (m.get("layers") or []) if isinstance(l, dict)]
    out["layers"] = lays
    if lays:
        out["hint"] += " — altındaki katmanlar (üstten alta): " + ", ".join(lays)
    return out


def burn_marks(im, views):
    """Draw the marks (as `mark_view` dicts) onto a PIL image in cue yellow, 3 px at 1000 px wide, no fill."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    w, h = im.size
    lw = max(3, round(w * 0.004))
    for v in views:
        if v["type"] == "arrow":
            x1, y1, x2, y2 = v["nx1"] * w, v["ny1"] * h, v["nx2"] * w, v["ny2"] * h
            d.line([(x1, y1), (x2, y2)], fill=MARK_COLOR, width=lw)
            import math
            a = math.atan2(y2 - y1, x2 - x1)
            L = max(12, lw * 5)
            for da in (2.6, -2.6):
                d.line([(x2, y2), (x2 + L * math.cos(a + da), y2 + L * math.sin(a + da))], fill=MARK_COLOR, width=lw)
        else:
            box = [v["nx"] * w, v["ny"] * h, (v["nx"] + v["nw"]) * w, (v["ny"] + v["nh"]) * h]
            (d.ellipse if v["type"] == "ellipse" else d.rectangle)(box, outline=MARK_COLOR, width=lw)
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
        out["marks"] = [mark_view(m, proj.get("width", 1920), proj.get("height", 1080)) for m in n["marks"]]
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
    with open(tmp, "w", encoding="utf-8") as f:
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
    with open(tmp, "w", encoding="utf-8") as f:
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
    with open(inbox_path(d), "a", encoding="utf-8") as f:
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


def wait_for_send(d, since=None, timeout=None, poll=0.4, cancel=None, commit=True):
    """Return the inbox entries the agent has not consumed yet (seq above `since`, default: the saved cursor), blocking until there is one.
    Only notes that are still `sent` and not `done` are returned, rebuilt from the current project. Without `since` the cursor moves to what was
    returned, so a send made while the agent was fixing the previous batch is delivered by the next call. `since` given: the cursor is neither
    read nor moved. timeout None = wait forever, 0 = look once, N = N seconds; None comes back on timeout or when `cancel` (a threading.Event) is
    set. `commit=False` leaves the cursor alone and returns (entries, commit_fn): the caller moves it only once the answer is really delivered."""
    use_cursor = since is None
    base = get_cursor(d) if use_cursor else int(since)
    end = None if timeout is None else time.monotonic() + timeout
    next_recover, last_size = 0.0, -1
    while True:
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
