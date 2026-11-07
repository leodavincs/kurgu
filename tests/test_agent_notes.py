"""Watch + note loop: POST /api/notes/send, NOTLAR.md, frame JPEGs, .kurgu/inbox.jsonl, and the agent side
(`kurgu notes list|wait|done`, MCP notes_list / notes_wait / notes_done)."""
import json
import os
import subprocess
import sys
import threading
import time

import pytest

import agent_notes
import server
from test_mcp import Mcp, first_text
from test_server import Client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "kurgu")
NOTES = [{"id": "n_a", "t": 1.0, "text": "title appears too late", "created": "2026-11-07T10:00:00"},
         {"id": "n_b", "t": 3.5, "text": "sound a bit loud here", "created": "2026-11-07T10:01:00"},
         {"id": "n_c", "t": 5.0, "text": "agent remark", "author": "agent"}]


def seed(d, notes=NOTES):
    path = os.path.join(d, "project.json")
    p = json.load(open(path, encoding="utf-8"))
    p["notes"] = [dict(n) for n in notes]
    json.dump(p, open(path, "w", encoding="utf-8"))
    return p


def cli(*args, timeout=60):
    return subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "notes", *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout)


@pytest.fixture()
def srv(project_dir):
    seed(project_dir)
    s = server.make_server(project_dir, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield Client(s.server_address[1]), project_dir
    s.shutdown()
    s.server_close()


def project(d):
    return json.load(open(os.path.join(d, "project.json"), encoding="utf-8"))


def test_send_marks_notes_writes_notlar_frames_and_inbox(srv, monkeypatch):
    c, d = srv
    st, data = c.req("POST", "/api/notes/send", {})
    assert st == 200 and data["sent"] == 2 and data["ids"] == ["n_a", "n_b"] and data["file"] == "NOTLAR.md"   # the agent's own note is not sent
    notes = {n["id"]: n for n in project(d)["notes"]}
    assert notes["n_a"]["sent"] and notes["n_b"]["sent"] == data["at"] and not notes["n_c"].get("sent")
    for nid in ("n_a", "n_b"):
        jpg = os.path.join(d, ".kurgu", "notes", nid + ".jpg")
        assert os.path.getsize(jpg) > 500 and open(jpg, "rb").read(3) == b"\xff\xd8\xff"
    md = open(os.path.join(d, "NOTLAR.md"), encoding="utf-8").read()
    assert "title appears too late" in md and "0:01.0 · sent · `n_a`" in md and ".kurgu/notes/n_b.jpg" in md and "layers then:" in md
    assert "agent remark" in md and "· open · `n_c`" in md
    lines = open(os.path.join(d, ".kurgu", "inbox.jsonl"), encoding="utf-8").read().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert [n["id"] for n in entry["notes"]] == ["n_a", "n_b"] and entry["notes"][0]["frame"] == ".kurgu/notes/n_a.jpg"
    assert "2 note(s) sent" in open(os.path.join(d, "changes.md"), encoding="utf-8").read()
    # nothing left to send; explicit ids re-send; bad input is a 400
    assert c.req("POST", "/api/notes/send", {})[0] == 400
    assert c.req("POST", "/api/notes/send", {"ids": "n_a"})[0] == 400
    assert c.req("POST", "/api/notes/send", {"ids": ["n_a"]})[0] == 409                  # already out: not announced twice ...
    assert c.req("POST", "/api/notes/send", {"ids": ["n_a"], "resend": True})[1]["sent"] == 1   # ... unless the caller says resend
    assert c.req("GET", "/api/notes/info")[1]["inbox"] == 2


def test_agent_name_from_env_and_config(project_dir, monkeypatch):
    monkeypatch.delenv("KURGU_AGENT_NAME", raising=False)
    assert agent_notes.agent_name(project_dir) == ""
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    json.dump({"agent_name": "Newton"}, open(os.path.join(project_dir, ".kurgu", "config.json"), "w"))
    assert agent_notes.agent_name(project_dir) == "Newton"
    monkeypatch.setenv("KURGU_AGENT_NAME", "Claude")
    assert agent_notes.agent_name(project_dir) == "Claude"


def test_cli_list_filters_and_json(project_dir):
    seed(project_dir, NOTES + [{"id": "n_d", "t": 7.0, "text": "sent one", "sent": "2026-11-07T10:05:00"},
                               {"id": "n_e", "t": 8.0, "text": "fixed one", "sent": "2026-11-07T10:05:00", "done": True, "reply": "ok"}])
    sent = json.loads(cli("list", project_dir, "--json").stdout)           # default = sent (what the agent should fix)
    assert [n["id"] for n in sent["notes"]] == ["n_d"] and sent["notes"][0]["status"] == "sent"
    assert [n["id"] for n in json.loads(cli("list", project_dir, "--open", "--json").stdout)["notes"]] == ["n_a", "n_b", "n_c"]
    allv = json.loads(cli("list", project_dir, "--all", "--json").stdout)["notes"]
    assert [n["status"] for n in allv] == ["open", "open", "open", "sent", "done"] and allv[-1]["reply"] == "ok"
    text = cli("list", project_dir, "--all").stdout
    assert "[n_d] 0:07.0" in text and "reply: ok" in text


def test_cli_done_with_reply_updates_project_and_notlar(project_dir):
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00"), NOTES[1]])
    r = cli("done", project_dir, "n_a", "--reply", "Title moved 0.8 s earlier")
    assert r.returncode == 0 and "n_a" in r.stdout
    n = {x["id"]: x for x in project(project_dir)["notes"]}["n_a"]
    assert n["done"] is True and n["reply"] == "Title moved 0.8 s earlier" and n["resolved_at"]
    assert "Title moved 0.8 s earlier" in open(os.path.join(project_dir, "NOTLAR.md"), encoding="utf-8").read()
    assert "[agent]" in open(os.path.join(project_dir, "changes.md"), encoding="utf-8").read()
    bad = cli("done", project_dir, "n_zzz")
    assert bad.returncode == 1 and "n_zzz" in bad.stderr


def test_cli_wait_times_out_then_wakes_on_send(srv):
    c, d = srv
    t0 = time.time()
    r = cli("wait", d, "--timeout", "1")
    assert r.returncode == 3 and "timeout" in r.stderr and time.time() - t0 < 15
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "notes", "wait", d, "--timeout", "60", "--json"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    time.sleep(1.5)
    assert p.poll() is None                                              # still blocked: nothing was sent since it started
    assert c.req("POST", "/api/notes/send", {"ids": ["n_b"]})[0] == 200
    out, _ = p.communicate(timeout=20)
    assert p.returncode == 0
    entries = json.loads(out)
    assert len(entries) == 1 and [n["id"] for n in entries[0]["notes"]] == ["n_b"] and entries[0]["notes"][0]["text"] == "sound a bit loud here"


def test_wait_cursor_delivers_a_send_made_while_the_agent_was_fixing(srv):
    c, d = srv
    assert c.req("POST", "/api/notes/send", {"ids": ["n_a"]})[0] == 200
    first = agent_notes.wait_for_send(d, timeout=0)                         # look once: the first send is pending
    assert [n["id"] for n in first[0]["notes"]] == ["n_a"] and first[0]["seq"] == 1
    assert agent_notes.get_cursor(d) == 1
    assert c.req("POST", "/api/notes/send", {"ids": ["n_b"]})[0] == 200     # the human sends again while the agent is busy fixing n_a
    second = agent_notes.wait_for_send(d, timeout=0)                        # the next wait must not lose it
    assert [n["id"] for n in second[0]["notes"]] == ["n_b"] and second[0]["seq"] == 2
    assert agent_notes.wait_for_send(d, timeout=0) is None                  # nothing new now
    again = agent_notes.wait_for_send(d, since=0, timeout=0)                # --since peeks: still-sent notes after seq 0, cursor untouched
    assert [e["seq"] for e in again] == [1, 2] and agent_notes.get_cursor(d) == 2
    r = cli("wait", d, "--timeout", "0")                                    # CLI: 0 = look once, exit 3 when nothing is new
    assert r.returncode == 3 and "inbox position 2" in r.stderr
    r = cli("wait", d, "--since", "1", "--timeout", "0")
    assert r.returncode == 0 and "Send #2" in r.stdout and "inbox position 2" in r.stdout and "[n_b]" in r.stdout


def test_wait_skips_notes_that_are_already_done(srv):
    c, d = srv
    c.req("POST", "/api/notes/send", {"ids": ["n_a", "n_b"]})
    assert cli("done", d, "n_a").returncode == 0
    got = agent_notes.wait_for_send(d, timeout=0)
    assert [n["id"] for e in got for n in e["notes"]] == ["n_b"]            # n_a was fixed meanwhile: not handed out again


def test_wait_recovers_sent_notes_whose_inbox_line_was_never_written(project_dir):
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00"), dict(NOTES[1], sent="2026-11-07T10:05:00", done=True)])   # a crash between the project write and the inbox append
    got = agent_notes.wait_for_send(project_dir, timeout=0)
    assert got and got[0].get("recovered") and [n["id"] for n in got[0]["notes"]] == ["n_a"]
    assert agent_notes.wait_for_send(project_dir, timeout=0) is None         # recovered once, not again


def test_wait_stops_polling_when_cancelled(project_dir):
    seed(project_dir)
    ev = threading.Event()
    threading.Timer(0.3, ev.set).start()
    t0 = time.time()
    assert agent_notes.wait_for_send(project_dir, timeout=30, cancel=ev) is None and time.time() - t0 < 5


def test_two_sends_at_once_stamp_and_announce_once(srv):
    c, d = srv
    codes = []
    ts = [threading.Thread(target=lambda: codes.append(c.req("POST", "/api/notes/send", {})[0])) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [200, 409]
    assert len(open(os.path.join(d, ".kurgu", "inbox.jsonl"), encoding="utf-8").read().splitlines()) == 1
    assert c.req("POST", "/api/notes/send", {})[0] == 400                    # nothing left to send


def test_notlar_md_follows_note_edits_made_through_save_project(srv):
    c, d = srv
    c.req("POST", "/api/notes/send", {})
    st, data = c.req("GET", "/api/project")
    p = data["project"]
    p["notes"][0]["done"] = True
    p["notes"][0]["reply"] = "fixed by hand in the editor"
    assert c.req("PUT", "/api/project", {"project": p, "summary": ["x"], "base_version": data["version"]})[0] == 200
    assert "fixed by hand in the editor" in open(os.path.join(d, "NOTLAR.md"), encoding="utf-8").read()


def test_notes_commands_do_not_import_the_editor_modules(project_dir):
    seed(project_dir)
    code = "import sys, agent_notes; agent_notes.main(['list', %r, '--all']); sys.exit(0 if not {'server', 'ai', 'media_tools', 'exports', 'mcp_server'} & set(sys.modules) else 1)" % project_dir
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True).returncode == 0


def test_kurgu_cmd_passes_the_exit_code_on():
    lines = [l.strip() for l in open(os.path.join(ROOT, "kurgu.cmd"), encoding="utf-8").read().splitlines()]
    code = [l for l in lines if l and not l.lower().startswith("rem")]
    depth = 0
    for l in code:
        assert not (depth > 0 and "%errorlevel%" in l.lower()), "%errorlevel% inside a parenthesised block is expanded at parse time: " + l
        depth += l.count("(") - l.count(")")
    assert depth == 0 and code.count("exit /b %errorlevel%") == 2          # editor path and notes path both hand the exit code on
    assert any("agent_notes.py" in l for l in code)


def test_launcher_dispatches_notes_and_keeps_editor_args(project_dir):
    seed(project_dir)
    r = subprocess.run(["bash", LAUNCHER, "notes", "list", project_dir, "--all"], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0 and "title appears too late" in r.stdout
    r = subprocess.run(["bash", LAUNCHER, "/no/such/folder"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "No such folder" in r.stderr             # `kurgu <dir>` still goes the editor way
    r = subprocess.run(["bash", LAUNCHER, "./notes"], capture_output=True, text=True, timeout=30, cwd=project_dir)
    assert r.returncode == 1 and "No such folder: ./notes" in r.stderr   # a folder called notes is `kurgu ./notes`, not the subcommand


def test_mcp_notes_tools(project_dir):
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00"), NOTES[1]])
    m = Mcp(project_dir)
    try:
        listed = json.loads(first_text(m.ok("notes_list")))
        assert [n["id"] for n in listed["notes"]] == ["n_a"] and listed["notes"][0]["status"] == "sent"
        assert json.loads(first_text(m.ok("notes_list", status="open")))["notes"][0]["id"] == "n_b"
        got = json.loads(first_text(m.ok("notes_wait", timeout=0.4)))                  # n_a is sent but was never announced: delivered once, flagged recovered
        assert got["sent"][0]["recovered"] is True and got["sent"][0]["notes"][0]["id"] == "n_a" and got["inbox"] == 1
        waited = json.loads(first_text(m.ok("notes_wait", timeout=0.4)))
        assert waited["timeout"] is True and waited["inbox"] == 1
        assert json.loads(first_text(m.ok("notes_wait", timeout=0)))["timeout"] is True        # 0 = look once, never "forever" over MCP
        assert m.tool("notes_done", ids=["n_nope"])["isError"]
        out = first_text(m.ok("notes_done", ids=["n_a", "n_b"], reply="fixed"))
        assert "2 note(s) marked done" in out
        assert all(n["done"] and n["reply"] == "fixed" for n in project(project_dir)["notes"][:2])
        assert "fixed" in open(os.path.join(project_dir, "NOTLAR.md"), encoding="utf-8").read()
    finally:
        m.close()


def test_default_mode_from_env_and_config(project_dir, monkeypatch):
    monkeypatch.delenv("KURGU_MODE", raising=False)
    assert agent_notes.default_mode(project_dir) == "review"
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    json.dump({"default_mode": "edit"}, open(os.path.join(project_dir, ".kurgu", "config.json"), "w"))
    assert agent_notes.default_mode(project_dir) == "edit"
    monkeypatch.setenv("KURGU_MODE", "review")
    assert agent_notes.default_mode(project_dir) == "review"
    monkeypatch.setenv("KURGU_MODE", "nonsense")
    assert agent_notes.default_mode(project_dir) == "edit"


# ------------------------------------------------------------------ marks drawn on the frame
MARK = {"type": "ellipse", "x": 1080, "y": 340, "w": 260, "h": 180, "nx": 0.5625, "ny": 0.3148, "nw": 0.1354, "nh": 0.1667,
        "layers": [{"id": "k_buyuk", "name": "Big title"}, {"id": "k_a", "name": "Clip A"}]}


def test_schema_keeps_valid_marks_and_reports_bad_ones():
    import schema
    p = schema.normalize({"notes": [{"id": "n1", "t": 1, "text": "x"}, {"id": "n2", "t": 2, "text": "y", "marks": [MARK, {"type": "star"}, {"type": "arrow", "x1": 1, "y1": 2, "x2": 3}]}]})
    assert "marks" not in p["notes"][0]                                   # notes without marks are untouched
    assert [m["type"] for m in p["notes"][1]["marks"]] == ["ellipse"]
    probs = schema.validate({"width": 10, "height": 10, "fps": 30, "duration": 1, "layers": [], "notes": [{"id": "n2", "t": 2, "text": "y", "marks": [{"type": "star"}, {"type": "arrow", "x1": 1}, "junk"]}]})
    assert any("mark #1" in x and "type" in x for x in probs) and any("mark #2" in x and "y1" in x for x in probs) and any("mark #3" in x for x in probs)


def test_send_burns_marks_in_and_writes_hint_layers_and_both_coordinate_systems(srv):
    c, d = srv
    seed(d, [dict(NOTES[0], marks=[MARK]), dict(NOTES[1], t=1.0, text="same second, no mark")])
    st, data = c.req("POST", "/api/notes/send", {})
    assert st == 200 and data["sent"] == 2
    from PIL import Image
    def yellow(nid):
        im = Image.open(os.path.join(d, ".kurgu", "notes", nid + ".jpg")).convert("RGB")
        return sum(1 for r, g, b in getattr(im, 'get_flattened_data', im.getdata)() if r > 200 and 150 < g < 235 and b < 130)
    assert yellow("n_a") > yellow("n_b") + 100                            # the ellipse is burned in; the same frame without marks has far less yellow
    assert open(os.path.join(d, ".kurgu", "notes", "n_a.jpg"), "rb").read() != open(os.path.join(d, ".kurgu", "notes", "n_b.jpg"), "rb").read()
    entry = json.loads(open(os.path.join(d, ".kurgu", "inbox.jsonl"), encoding="utf-8").read().splitlines()[0])
    mk = [n for n in entry["notes"] if n["id"] == "n_a"][0]["marks"][0]
    assert mk["x"] == 1080 and mk["w"] == 260 and mk["center"] == [1210, 430] and mk["nx"] == 0.5625 and mk["nw"] == 0.1354 and mk["layers"] == ["Big title", "Clip A"]
    assert "işaret: elips, merkez (1210, 430), 260x180 px — sağ üst çeyrek" in mk["hint"] and "Big title, Clip A" in mk["hint"]
    assert "marks" not in [n for n in entry["notes"] if n["id"] == "n_b"][0]
    md = open(os.path.join(d, "NOTLAR.md"), encoding="utf-8").read()
    assert "işaret: elips, merkez (1210, 430)" in md and "nx=0.5625" in md and "drawn on it in yellow" in md


def test_arrow_hint_and_cli_json_shows_marks(project_dir):
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00", marks=[{"type": "arrow", "x1": 200, "y1": 900, "x2": 300, "y2": 800, "layers": []}])])
    n = json.loads(cli("list", project_dir, "--json").stdout)["notes"][0]
    assert n["marks"][0]["x2"] == 300 and n["marks"][0]["nx2"] == 0.1562
    assert "ok" in n["marks"][0]["hint"] and "sol alt çeyrek" in n["marks"][0]["hint"]
    assert "işaret: ok" in cli("list", project_dir).stdout


# ------------------------------------------------------------------ robustness of the inbox / cursor
def test_seq_never_goes_backwards_when_the_inbox_is_deleted(srv):
    c, d = srv
    c.req("POST", "/api/notes/send", {"ids": ["n_a"]})
    assert agent_notes.wait_for_send(d, timeout=0)[0]["seq"] == 1 and agent_notes.get_cursor(d) == 1
    os.remove(os.path.join(d, ".kurgu", "inbox.jsonl"))                      # the file is gone but the high-water mark is not
    c.req("POST", "/api/notes/send", {"ids": ["n_b"]})
    got = agent_notes.wait_for_send(d, timeout=0)
    assert got and got[0]["seq"] == 2 and [n["id"] for n in got[0]["notes"]] == ["n_b"]
    os.remove(os.path.join(d, ".kurgu", "inbox.jsonl")); os.remove(os.path.join(d, ".kurgu", "inbox.seq"))      # everything gone, cursor still 2
    c.req("POST", "/api/notes/send", {"ids": ["n_a"], "resend": True})
    got = agent_notes.wait_for_send(d, timeout=0)
    assert got and [n["id"] for n in got[0]["notes"]] == ["n_a"]             # a cursor above the highest seq is reset: new sends still arrive


def test_a_partial_last_line_does_not_swallow_the_next_send(srv):
    c, d = srv
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    with open(os.path.join(d, ".kurgu", "inbox.jsonl"), "w", encoding="utf-8") as f:
        f.write('{"seq": 1, "sent": "x", "project": "p", "dir": "d", "notes": [{"id": "n_zz"')     # crash mid-write: no closing, no newline
    c.req("POST", "/api/notes/send", {"ids": ["n_a"]})
    assert [e["notes"][0]["id"] for e in agent_notes.read_inbox(d)] == ["n_a"]     # the broken line is skipped, the new one is intact
    got = agent_notes.wait_for_send(d, timeout=0)
    assert got and got[0]["notes"][0]["id"] == "n_a"


def test_wait_looks_for_unannounced_sends_while_it_waits(project_dir, monkeypatch):
    seed(project_dir)
    monkeypatch.setattr(agent_notes, "RECOVER_EVERY", 0.3)
    def late():
        time.sleep(0.6)
        seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00"), NOTES[1]])   # a send that stamped the project but never wrote its inbox line
    threading.Thread(target=late, daemon=True).start()
    got = agent_notes.wait_for_send(project_dir, timeout=10, poll=0.1)
    assert got and got[0].get("recovered") and got[0]["notes"][0]["id"] == "n_a"


def test_cursor_moves_only_when_the_caller_commits(srv):
    c, d = srv
    c.req("POST", "/api/notes/send", {"ids": ["n_a"]})
    entries, commit = agent_notes.wait_for_send(d, timeout=0, commit=False)
    assert entries and agent_notes.get_cursor(d) == 0                         # a cancelled MCP call never reaches commit(): the batch stays unconsumed
    assert agent_notes.wait_for_send(d, timeout=0, commit=False) is not None
    commit()
    assert agent_notes.get_cursor(d) == 1 and agent_notes.wait_for_send(d, timeout=0) is None


def test_wait_shows_the_note_as_it_is_now_not_as_it_was_sent(srv):
    c, d = srv
    c.req("POST", "/api/notes/send", {"ids": ["n_a"]})
    st, data = c.req("GET", "/api/project")
    p = data["project"]
    [n for n in p["notes"] if n["id"] == "n_a"][0]["text"] = "title appears 2 s too late (edited)"
    assert c.req("PUT", "/api/project", {"project": p, "summary": ["x"], "base_version": data["version"]})[0] == 200
    got = agent_notes.wait_for_send(d, timeout=0)
    assert got[0]["notes"][0]["text"] == "title appears 2 s too late (edited)"


def test_the_frame_picture_is_redrawn_when_a_sent_note_moves(srv):
    c, d = srv
    c.req("POST", "/api/notes/send", {"ids": ["n_a"]})
    jpg = os.path.join(d, ".kurgu", "notes", "n_a.jpg")
    before = open(jpg, "rb").read()
    st, data = c.req("GET", "/api/project")
    p = data["project"]
    [n for n in p["notes"] if n["id"] == "n_a"][0]["t"] = 6.5                  # the pipeline moved the note to another second
    assert c.req("PUT", "/api/project", {"project": p, "summary": ["x"], "base_version": data["version"]})[0] == 200
    sig = os.path.join(d, ".kurgu", "notes", "n_a.sig")
    for _ in range(100):
        time.sleep(0.3)
        if open(jpg, "rb").read() != before:
            break
    assert open(jpg, "rb").read() != before
    assert open(sig, encoding="utf-8").read()


def test_marks_follow_the_normalised_coordinates_when_the_resolution_changes():
    m = {"type": "ellipse", "x": 100, "y": 100, "w": 50, "h": 50, "nx": 0.5, "ny": 0.25, "nw": 0.1, "nh": 0.2}
    v = agent_notes.mark_view(m, 1280, 720)                                     # the project was resized after the note was written
    assert (v["x"], v["y"], v["w"], v["h"]) == (640, 180, 128, 144) and v["center"] == [704, 252] and v["nx"] == 0.5
    a = agent_notes.mark_view({"type": "arrow", "x1": 1, "y1": 1, "x2": 2, "y2": 2, "nx1": 0.1, "ny1": 0.2, "nx2": 0.5, "ny2": 0.5}, 1000, 500)
    assert (a["x1"], a["y1"], a["x2"], a["y2"]) == (100, 100, 500, 250)
    assert agent_notes.mark_view({"type": "rect", "x": 10, "y": 20, "w": 30, "h": 40}, 100, 200)["nx"] == 0.1   # pixels only: normalised is derived


def test_mcp_notes_wait_rejects_a_bad_since_and_leaves_the_cursor(project_dir):
    seed(project_dir)
    m = Mcp(project_dir)
    try:
        assert m.tool("notes_wait", since="abc", timeout=0)["isError"] and m.tool("notes_wait", since=1.5, timeout=0)["isError"]
        assert agent_notes.get_cursor(project_dir) == 0
    finally:
        m.close()


def test_launcher_prefers_a_python_310_and_cmd_keeps_long_argument_lists(tmp_path, project_dir):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "python3.13").write_text("#!/bin/sh\nexit 1\n")                     # an interpreter that fails the version guard
    (fake / "python3.13").chmod(0o755)
    (fake / "python3.12").symlink_to(sys.executable)
    seed(project_dir)
    env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"])
    r = subprocess.run(["bash", LAUNCHER, "notes", "list", project_dir, "--all"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and "title appears too late" in r.stdout
    text = open(LAUNCHER, encoding="utf-8").read()
    assert "python3.10" in text and "sys.version_info >= (3, 10)" in text and "needs Python 3.10" in text
    cmd = open(os.path.join(ROOT, "kurgu.cmd"), encoding="utf-8").read()
    assert "NARGS" in cmd and "%1 %2" not in cmd and "sys.version_info >= (3, 10)" in cmd and "nopython" in cmd


def test_explicit_resend_needs_the_flag_and_does_not_wake_the_agent_twice(srv):
    c, d = srv
    assert c.req("POST", "/api/notes/send", {"ids": ["n_x_missing"]})[0] == 400
    assert c.req("POST", "/api/notes/send", {"ids": ["n_a"]})[0] == 200
    assert c.req("POST", "/api/notes/send", {"ids": ["n_a"]})[0] == 409
    assert c.req("POST", "/api/notes/send", {"ids": ["n_a", "n_b"]})[1]["ids"] == ["n_b"]       # only the not-yet-sent one goes
    assert len(open(os.path.join(d, ".kurgu", "inbox.jsonl"), encoding="utf-8").read().splitlines()) == 2
    assert c.req("POST", "/api/notes/send", {"ids": ["n_a"], "resend": True})[0] == 200
    assert agent_notes.inbox_len(d) == 3


def test_marks_without_layers_get_them_from_the_server(project_dir):
    # a mark injected into project.json (agent / pipeline / older client) has no `layers`: the server hit-tests the layers active at the note time
    seed(project_dir, [dict(NOTES[0], t=3.0, sent="2026-11-07T10:05:00", marks=[{"type": "ellipse", "x": 860, "y": 440, "w": 200, "h": 200}])])
    mk = json.loads(cli("list", project_dir, "--json").stdout)["notes"][0]["marks"][0]
    assert mk["layers"][-1] == "A clip" and "Big title" in mk["layers"]            # the full-frame clip is under the mark, the title in front of it
    assert not {"Music", "Logo", "Frozen frame", "Black gap"} & set(mk["layers"])   # audio, off-frame and inactive layers are left out
    assert "altındaki katmanlar (üstten alta): " in mk["hint"] and mk["hint"].endswith("A clip")
    a = agent_notes.layers_under_mark({"width": 1920, "height": 1080, "layers": [{"id": "v", "name": "Scene", "type": "video", "start": 0, "end": 5}]}, 1.0, [(10, 10)])
    assert a == ["Scene"]                                                           # a full-frame video without any box counts as covering the frame
