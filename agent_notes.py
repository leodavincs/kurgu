"""agent_notes.py: the "watch + note + send" loop between the human and a coding agent.

The human watches the video in the editor, drops timestamped notes and clicks Send. The editor server (server.App.notes_send)
marks those notes `sent` in project.json, saves a JPEG of each frame under .kurgu/notes/, rewrites NOTLAR.md (readable by both)
and appends ONE JSON line per send to .kurgu/inbox.jsonl. The agent blocks on `kurgu notes wait <dir>` (or the MCP tool
`notes_wait`) and is woken by that line; it fixes the notes, then `kurgu notes done <dir> <id...> --reply "..."`.

Note status (derived, nothing extra stored): done -> "done"; else sent timestamp set -> "sent"; else "open" (written, not sent yet).

CLI:  kurgu notes list <dir> [--open|--sent|--all] [--json]
      kurgu notes wait <dir> [--timeout S] [--json]
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
    out = []
    try:
        with open(inbox_path(d), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def layers_under(proj, t):
    return [{"id": l.get("id"), "name": l.get("name", ""), "type": l.get("type")} for l in proj.get("layers", [])
            if isinstance(l, dict) and not l.get("hidden") and l.get("start", 0) <= t < l.get("end", 0)]


def tc(t):
    m = int(t // 60)
    return f"{m}:{t - m * 60:04.1f}"


def note_view(d, n, proj):
    out = {"id": n["id"], "t": n["t"], "timecode": tc(n["t"]), "text": n["text"], "status": status_of(n),
           "layers": layers_under(proj, n["t"])}
    for k in ("sent", "reply", "resolved_at", "created"):
        if n.get(k):
            out[k] = n[k]
    if os.path.exists(os.path.join(d, FRAMES, n["id"] + ".jpg")):
        out["frame"] = frame_rel(n["id"])
    return out


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
            L.append(f"- frame: ![{v['timecode']}]({v['frame']})")
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


def append_inbox(d, proj, notes, sent_at):
    """One JSON line per send; the line is the wake-up signal for `notes wait`."""
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    entry = {"sent": sent_at, "project": proj.get("name") or os.path.basename(d), "dir": d,
             "notes": [note_view(d, n, proj) for n in notes]}
    with open(inbox_path(d), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def inbox_len(d):
    return len(read_inbox(d))


def wait_for_send(d, since=None, timeout=None, poll=0.4):
    """Block until the inbox holds more than `since` lines (default: its length now). Returns the new entries, or None on timeout."""
    since = inbox_len(d) if since is None else since
    end = None if not timeout else time.monotonic() + timeout
    while True:
        entries = read_inbox(d)
        if len(entries) > since:
            return entries[since:]
        if end is not None and time.monotonic() >= end:
            return None
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
                   + (f"\n    frame: {v['frame']}" if v.get("frame") else "") + (f"\n    reply: {v['reply']}" if v.get("reply") else ""))
    return "\n".join(out)


def render_entries(entries):
    out = []
    for e in entries:
        out.append(f"Sent {e['sent']} from \"{e['project']}\" ({len(e['notes'])} notes):\n" + render_text(e["notes"]))
    return "\n\n".join(out)


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
    pw.add_argument("--timeout", type=float, default=0, help="seconds (0 = wait forever); exit code 3 on timeout")
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
        entries = wait_for_send(d, timeout=a.timeout)
        if entries is None:
            print("timeout: nothing was sent", file=sys.stderr)
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
