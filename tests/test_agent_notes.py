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
IS_WIN = os.name == "nt"


def launch(*args, env=None, cwd=None, timeout=60):
    """Run the checkout launcher for this platform: kurgu.cmd through cmd.exe on Windows (CI is the only place it really runs), `kurgu` through bash elsewhere."""
    argv = ["cmd", "/c", os.path.join(ROOT, "kurgu.cmd"), *args] if IS_WIN else ["bash", LAUNCHER, *args]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=cwd, timeout=timeout)
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


def test_send_and_answer_say_whether_someone_else_wrote_since_base_version(srv):
    """The editor loads the send's answer itself; `changed_outside` tells it whether that answer also carries a write by someone else."""
    c, d = srv
    v0 = c.req("GET", "/api/version")[1]["version"]
    st, data = c.req("POST", "/api/notes/send", {"ids": ["n_a"], "base_version": v0})
    assert st == 200 and data["changed_outside"] is False and data["version"] != v0      # its own write is not "outside"
    assert data["previous_version"] == v0                                                  # the version this write replaced
    p = project(d)
    p["name"] = "renamed by someone else"
    json.dump(p, open(os.path.join(d, "project.json"), "w", encoding="utf-8"))
    st, data = c.req("POST", "/api/notes/send", {"ids": ["n_b"], "base_version": data["version"]})
    assert st == 200 and data["changed_outside"] is True and data["project"]["name"] == "renamed by someone else"
    assert c.req("POST", "/api/notes/send", {"ids": ["n_a"], "resend": True})[1]["changed_outside"] is False   # no base_version: no claim
    assert cli("ask", d, "n_a", "--question", "which one?").returncode == 0
    st, data = c.req("POST", "/api/notes/answer", {"id": "n_a", "answer": "the first", "base_version": "0" * 40})
    assert st == 200 and data["changed_outside"] is True


def test_the_server_remembers_which_editor_tab_wrote_each_version(srv):
    """An editor tab sends its `client` id with saves and server writes; /api/version and a 409 name the tab that wrote the current version,
    so a tab recognises its own write (e.g. a send that answered late) and does not report it as the agent's."""
    c, d = srv
    v0 = c.req("GET", "/api/version")[1]["version"]
    assert c.req("GET", "/api/version")[1]["writer"] == ""                                   # written by nobody we know (the fixture)
    st, data = c.req("POST", "/api/notes/send", {"ids": ["n_a"], "base_version": v0, "client": "tab-a"})
    assert st == 200
    v = c.req("GET", "/api/version")[1]
    assert v["version"] == data["version"] and v["writer"] == "tab-a"
    st, res = c.req("PUT", "/api/project", {"project": data["project"], "summary": ["x"], "base_version": v0, "client": "tab-a"})
    assert st == 409 and res["writer"] == "tab-a"                                           # stale base, but the newer file is this tab's own
    st, res = c.req("PUT", "/api/project", {"project": data["project"], "summary": ["x"], "base_version": data["version"], "client": "tab-b"})
    assert st == 200 and c.req("GET", "/api/version")[1]["writer"] == "tab-b"


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
    code = "import sys, agent_notes; agent_notes.main(['list', %r, '--all']); sys.exit(0 if not {'server', 'ai', 'exports', 'mcp_server'} & set(sys.modules) else 1)" % project_dir
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True).returncode == 0


def test_kurgu_cmd_passes_the_exit_code_on():
    raw = open(os.path.join(ROOT, "kurgu.cmd"), "rb").read()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")           # a batch file needs CRLF
    lines = [l.strip() for l in raw.decode("utf-8").splitlines()]
    code = [l for l in lines if l and not l.lower().startswith("rem")]
    depth = 0
    for l in code:
        assert not (depth > 0 and "%errorlevel%" in l.lower()), "%errorlevel% inside a parenthesised block is expanded at parse time: " + l
        depth += l.count("(") - l.count(")")
    assert depth == 0
    for i, l in enumerate(code):                                                  # every real launch of Python hands its exit code on, on the next line
        if l.startswith("%PYEXE% ") and "-c " not in l:
            assert code[i + 1] == "exit /b %errorlevel%", l
    launches = [l for l in code if l.startswith("%PYEXE% ") and "-c " not in l]
    assert launches == ['%PYEXE% "%~dp0kurgu_cli.py" %*']                          # one launch: kurgu_cli.py routes every command (notes, setup-agent, a folder ...)
    assert not any(l.lower().startswith("shift") for l in code)                   # SHIFT also moves %0: %~dp0 after it pointed at the current folder


def test_launcher_dispatches_notes_and_keeps_editor_args(project_dir):
    seed(project_dir)
    r = launch("notes", "list", project_dir, "--all")
    assert r.returncode == 0 and "title appears too late" in r.stdout
    r = launch("/no/such/folder")
    assert r.returncode == 1 and ("No such folder" in r.stderr or "does not exist" in r.stderr)         # `kurgu <dir>` still goes the editor way
    home = os.path.join(project_dir, "home")
    os.makedirs(home)
    r = launch("setup-agent", "--codex", "--dry-run", env=dict(os.environ, HOME=home, USERPROFILE=home, CODEX_HOME=""))
    assert r.returncode == 0 and "Dry run" in r.stdout and "config.toml" in r.stdout                    # `kurgu setup-agent` is routed to kurgu_cli.py
    r = launch("./notes", cwd=project_dir)
    assert r.returncode == 1 and "notes" in r.stderr and (IS_WIN or "No such folder: ./notes" in r.stderr)   # a folder called notes is `kurgu ./notes`, not the subcommand
    # the exit code of the real program reaches the caller (kurgu.cmd used to lose it)
    assert launch("notes", "wait", project_dir, "--timeout", "0").returncode == 3
    assert launch("wake", "gemini", project_dir).returncode == 2


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
    assert "işaret: sarı elips, merkez (1210, 430), 260x180 px — sağ üst çeyrek" in mk["hint"] and mk["color"] == "#f3c34b" and mk["color_name"] == "yellow" and "Big title, Clip A" in mk["hint"]
    assert "marks" not in [n for n in entry["notes"] if n["id"] == "n_b"][0]
    md = open(os.path.join(d, "NOTLAR.md"), encoding="utf-8").read()
    assert "işaret: sarı elips, merkez (1210, 430)" in md and "nx=0.5625" in md and "drawn on it in" in md


def test_arrow_hint_and_cli_json_shows_marks(project_dir):
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00", marks=[{"type": "arrow", "x1": 200, "y1": 900, "x2": 300, "y2": 800, "layers": []}])])
    n = json.loads(cli("list", project_dir, "--json").stdout)["notes"][0]
    assert n["marks"][0]["x2"] == 300 and n["marks"][0]["nx2"] == 0.1562
    assert "ok" in n["marks"][0]["hint"] and "sol alt çeyrek" in n["marks"][0]["hint"]
    assert "işaret: sarı ok" in cli("list", project_dir).stdout


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
    seed(project_dir)
    if not IS_WIN:                                                                # the interpreter choice of the bash launcher (kurgu.cmd is checked below)
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "python3.13").write_text("#!/bin/sh\nexit 1\n")                 # an interpreter that fails the version guard
        (fake / "python3.13").chmod(0o755)
        (fake / "python3.12").symlink_to(sys.executable)
        env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"])
        r = launch("notes", "list", project_dir, "--all", env=env)
        assert r.returncode == 0 and "title appears too late" in r.stdout
    else:                                                                         # long argument lists survive kurgu.cmd (no %1..%9 cut-off)
        r = launch("notes", "done", project_dir, *(["n_a"] * 12), "--reply", "a reply with spaces")      # 15 arguments: more than %1..%9
        assert r.returncode == 0, r.stderr
        n = {x["id"]: x for x in json.load(open(os.path.join(project_dir, "project.json"), encoding="utf-8"))["notes"]}["n_a"]
        assert n["done"] is True and n["reply"] == "a reply with spaces"          # the last argument arrived whole
    text = open(LAUNCHER, encoding="utf-8").read()
    assert "python3.10" in text and "sys.version_info >= (3, 10)" in text and "needs Python 3.10" in text
    cmd = open(os.path.join(ROOT, "kurgu.cmd"), encoding="utf-8").read()
    assert 'kurgu_cli.py" %*' in cmd and "%1 %2" not in cmd and "sys.version_info >= (3, 10)" in cmd and "nopython" in cmd


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
    assert "altındaki katmanlar (üstten alta): " in mk["hint"] and "A clip" in mk["hint"]
    a = agent_notes.layers_under_mark({"width": 1920, "height": 1080, "layers": [{"id": "v", "name": "Scene", "type": "video", "start": 0, "end": 5}]}, 1.0, [(10, 10)])
    assert a == ["Scene"]                                                           # a full-frame video without any box counts as covering the frame


# ------------------------------------------------------------------ mark colours
def test_mark_colour_schema_default_and_validation():
    import schema
    base = {"type": "ellipse", "x": 1, "y": 2, "w": 3, "h": 4}
    n = schema.normalize({"notes": [{"id": "a", "t": 1, "marks": [dict(base, color="#FF5446"), dict(base), dict(base, color="red"), dict(base, color="#12345")]}]})["notes"][0]
    assert [m.get("color") for m in n["marks"]] == ["#ff5446", None, None, None]               # lowercased; absent / invalid = the default yellow
    probs = schema.validate({"width": 10, "height": 10, "fps": 30, "duration": 1, "layers": [], "notes": [{"id": "a", "t": 1, "marks": [dict(base, color="red")]}]})
    assert any("color must be #rrggbb" in x for x in probs)
    assert not any("color" in x for x in schema.validate({"width": 10, "height": 10, "fps": 30, "duration": 1, "layers": [], "notes": [{"id": "a", "t": 1, "marks": [dict(base, color="#2ec4b6")]}]}))


def test_coloured_marks_are_burned_in_named_in_the_hint_and_redrawn_when_recoloured(srv):
    c, d = srv
    red = {"type": "ellipse", "x": 600, "y": 300, "w": 700, "h": 400, "color": "#ff5446"}
    seed(d, [dict(NOTES[0], marks=[red]), dict(NOTES[1], t=1.0, marks=[dict(red, color="#ffffff")])])
    assert c.req("POST", "/api/notes/send", {})[0] == 200
    from PIL import Image

    def count(nid, rgb, tol=40):
        im = Image.open(os.path.join(d, ".kurgu", "notes", nid + ".jpg")).convert("RGB")
        return sum(1 for p in getattr(im, "get_flattened_data", im.getdata)() if all(abs(p[i] - rgb[i]) < tol for i in range(3)))
    assert count("n_a", (255, 84, 70)) > 150 and count("n_a", (255, 84, 70)) > count("n_b", (255, 84, 70)) + 100      # red on the first, not on the white one
    assert count("n_b", (255, 255, 255), 12) > count("n_a", (255, 255, 255), 12) + 100
    entry = json.loads(open(os.path.join(d, ".kurgu", "inbox.jsonl"), encoding="utf-8").read().splitlines()[0])
    m = [n for n in entry["notes"] if n["id"] == "n_a"][0]["marks"][0]
    assert m["color"] == "#ff5446" and m["color_name"] == "red" and "işaret: kırmızı elips, merkez" in m["hint"]
    assert "işaret: beyaz elips" in open(os.path.join(d, "NOTLAR.md"), encoding="utf-8").read()
    # changing the colour of a sent note redraws its picture (the colour is part of the signature)
    before = open(os.path.join(d, ".kurgu", "notes", "n_a.jpg"), "rb").read()
    st, data = c.req("GET", "/api/project")
    p = data["project"]
    [n for n in p["notes"] if n["id"] == "n_a"][0]["marks"][0]["color"] = "#6cc592"
    assert c.req("PUT", "/api/project", {"project": p, "summary": ["x"], "base_version": data["version"]})[0] == 200
    for _ in range(100):
        time.sleep(0.3)
        if open(os.path.join(d, ".kurgu", "notes", "n_a.jpg"), "rb").read() != before:
            break
    assert count("n_a", (108, 197, 146)) > 150


def test_launcher_dispatches_frame(project_dir, tmp_path):
    out = os.path.join(project_dir, ".kurgu", "look", "via-launcher.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    r = launch("frame", project_dir, "1.0", "-o", out)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().replace("\\", "/").endswith("via-launcher.jpg") and open(out, "rb").read(3) == b"\xff\xd8\xff"


# ---------------------------------------------------------------- the "asked" state: the agent asks instead of guessing
def test_cli_ask_marks_asked_and_notlar_shows_question(project_dir):
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00"), NOTES[1]])
    assert cli("ask", project_dir, "n_a").returncode != 0                                   # --question is required
    r = cli("ask", project_dir, "n_a", "--question", "earlier by 0.5 s or 1 s?")
    assert r.returncode == 0 and "n_a" in r.stdout
    n = {x["id"]: x for x in project(project_dir)["notes"]}["n_a"]
    assert n["question"] == "earlier by 0.5 s or 1 s?" and n["asked"] and not n["done"]
    assert agent_notes.status_of(n) == "asked"
    assert [x["id"] for x in json.loads(cli("list", project_dir, "--asked", "--json").stdout)["notes"]] == ["n_a"]
    assert json.loads(cli("list", project_dir, "--json").stdout)["count"] == 0              # no longer something to fix
    md = open(os.path.join(project_dir, "NOTLAR.md"), encoding="utf-8").read()
    assert "· asked · `n_a`" in md and "question: earlier by 0.5 s or 1 s?" in md and "waiting for the human" in md and "1 asked" in md
    assert cli("ask", project_dir, "n_nope", "--question", "x").returncode == 1
    cli("done", project_dir, "n_a", "--reply", "ok")                                          # closing it anyway clears the open question
    assert "asked" not in {x["id"]: x for x in project(project_dir)["notes"]}["n_a"]


def test_asked_note_is_not_delivered_until_answered_then_gets_a_new_seq(srv):
    c, d = srv
    assert c.req("POST", "/api/notes/send", {})[1]["sent"] == 2
    assert agent_notes.wait_for_send(d, timeout=0) is not None                                # consume send #1
    assert cli("ask", d, "n_a", "--question", "how much earlier?").returncode == 0
    assert agent_notes.wait_for_send(d, timeout=0) is None
    assert agent_notes.wait_for_send(d, since=0, timeout=0)[0]["notes"][0]["id"] == "n_b"    # the old entry no longer lists the asked note
    # answering: needs a real question, an answer, and an asked note
    assert c.req("POST", "/api/notes/answer", {"id": "n_a", "answer": " "})[0] == 400
    assert c.req("POST", "/api/notes/answer", {"id": "n_b", "answer": "x"})[0] == 409
    assert c.req("POST", "/api/notes/answer", {"id": "n_zzz", "answer": "x"})[0] == 404
    st, data = c.req("POST", "/api/notes/answer", {"id": "n_a", "answer": "1 second"})
    assert st == 200 and data["inbox"]["seq"] == 2 and data["ids"] == ["n_a"]
    n = {x["id"]: x for x in project(d)["notes"]}["n_a"]
    assert n["answer"] == "1 second" and "asked" not in n and n["question"] == "how much earlier?" and agent_notes.status_of(n) == "sent"
    got = agent_notes.wait_for_send(d, timeout=0)
    assert [e["seq"] for e in got] == [2]
    v = got[0]["notes"][0]
    assert v["id"] == "n_a" and v["question"] == "how much earlier?" and v["answer"] == "1 second"
    assert "the human's answer: 1 second" in agent_notes.render_entries(got)
    assert c.req("POST", "/api/notes/answer", {"id": "n_a", "answer": "again"})[0] == 409    # no open question any more
    md = open(os.path.join(d, "NOTLAR.md"), encoding="utf-8").read()
    assert "answer: 1 second" in md


def test_mcp_notes_ask(project_dir):
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00"), NOTES[1]])
    m = Mcp(project_dir)
    try:
        assert m.tool("notes_ask", ids=["n_a"])["isError"] and m.tool("notes_ask", ids=["n_nope"], question="?")["isError"]
        out = first_text(m.ok("notes_ask", ids=["n_a"], question="which red?"))
        assert "1 note(s) marked asked" in out
        listed = json.loads(first_text(m.ok("notes_list", status="asked")))
        assert listed["notes"][0]["id"] == "n_a" and listed["notes"][0]["status"] == "asked" and listed["notes"][0]["question"] == "which red?"
        assert "notes_ask" in [t["name"] for t in m.call("tools/list")["result"]["tools"]]
        got = json.loads(first_text(m.ok("get_notes", status="all")))["notes"]
        assert {n["id"]: n["status"] for n in got}["n_a"] == "asked"
    finally:
        m.close()


def test_old_notes_without_question_fields_are_unchanged(project_dir):
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00")])
    v = agent_notes.list_views(project_dir, "sent")[0]
    assert v["status"] == "sent" and "question" not in v and "answer" not in v


def test_wake_prompt_and_guidance_carry_the_interpret_or_ask_rule():
    import kurgu_cli
    p = kurgu_cli.WAKE_PROMPT
    assert "kurgu notes ask" in p and "değer" in p and len(p) < 1400
    for text in (kurgu_cli.AGENT_BLOCK, open(os.path.join(ROOT, "skills", "kurgu", "SKILL.md"), encoding="utf-8").read(),
                 open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8").read()):
        assert "kurgu notes ask" in text and "stand-in" in text and "frozen" in text
    assert "notes_ask" in open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()


def test_elements_under_falls_back_to_pixels_when_there_is_no_element_map(project_dir, monkeypatch):
    import ocr_boxes
    calls = []

    def fake(d, n, m, frame_png=None):
        calls.append(n["id"])
        return [{"text": "GOAL", "box": [10.0, 10.0, 90.0, 40.0], "approx": True, "source": "ocr"},
                {"kind": "card", "box": [0.0, 0.0, 200.0, 100.0], "approx": True, "source": "pixels", "color": "#112233"}]
    monkeypatch.setattr(ocr_boxes, "things_under_mark", fake)
    mark = dict(MARK, layers=[])
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00", marks=[mark])])
    proj, notes = agent_notes.load_notes(project_dir)
    v = agent_notes.note_view(project_dir, notes[0], proj)
    eu = v["marks"][0]["elements_under"]
    assert calls == ["n_a"] and [e["source"] for e in eu] == ["ocr", "pixels"] and all(e["approx"] for e in eu)
    assert eu[0]["label"] == "GOAL" and eu[0]["kind"] == "text" and len(eu[0]["box"]) == 4 and eu[1]["kind"] == "card"
    assert "approximate" in agent_notes.fmt_element(eu[0]) or agent_notes.fmt_element(eu[0])          # formats without a file/line
    # an open (not yet sent) note does not pay for OCR
    calls.clear()
    seed(project_dir, [dict(NOTES[0], marks=[mark])])
    proj, notes = agent_notes.load_notes(project_dir)
    assert "elements_under" not in agent_notes.note_view(project_dir, notes[0], proj)["marks"][0] and calls == []
    # a failing OCR never breaks the note
    monkeypatch.setattr(ocr_boxes, "things_under_mark", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00", marks=[mark])])
    proj, notes = agent_notes.load_notes(project_dir)
    assert "elements_under" not in agent_notes.note_view(project_dir, notes[0], proj)["marks"][0]


def test_listener_heartbeat_is_written_refreshed_and_removed(project_dir, monkeypatch):
    lp = os.path.join(project_dir, ".kurgu", "listening.json")
    monkeypatch.setattr(agent_notes, "BEAT_EVERY", 0.2)
    assert not agent_notes.is_listening(project_dir)
    res = {}
    th = threading.Thread(target=lambda: res.update(r=agent_notes.wait_for_send(project_dir, timeout=1.6, poll=0.05)))
    th.start()
    deadline = time.time() + 5
    while not os.path.exists(lp) and time.time() < deadline:
        time.sleep(0.02)
    e = json.load(open(lp))[str(os.getpid())]
    assert e["pid"] == os.getpid() and e["since"] <= e["beat"]
    assert agent_notes.is_listening(project_dir)
    time.sleep(0.6)
    assert json.load(open(lp))[str(os.getpid())]["beat"] > e["beat"]                       # refreshed
    th.join()
    assert res["r"] is None and not os.path.exists(lp) and not agent_notes.is_listening(project_dir)   # timeout cleans up


def test_listener_file_removed_on_interrupt_and_look_once_does_not_register(project_dir):
    lp = os.path.join(project_dir, ".kurgu", "listening.json")
    cancel = threading.Event()
    th = threading.Thread(target=lambda: agent_notes.wait_for_send(project_dir, cancel=cancel, poll=0.05))
    th.start()
    deadline = time.time() + 5
    while not os.path.exists(lp) and time.time() < deadline:
        time.sleep(0.02)
    assert os.path.exists(lp)
    cancel.set(); th.join()
    assert not os.path.exists(lp)
    agent_notes.wait_for_send(project_dir, timeout=0)
    assert not os.path.exists(lp)


def test_listener_liveness_needs_a_fresh_beat_and_a_living_pid(project_dir):
    now = time.time()
    w = lambda pid, beat: agent_notes._write_listeners(project_dir, {"x": {"pid": pid, "since": now, "beat": beat}})
    w(os.getpid(), now)
    assert agent_notes.is_listening(project_dir)
    w(os.getpid(), now - 20)
    assert not agent_notes.is_listening(project_dir)                                       # stale beat
    p = subprocess.Popen([sys.executable, "-c", "pass"]); p.wait()
    w(p.pid, now)
    assert not agent_notes.is_listening(project_dir)                                       # dead pid
    agent_notes._write_listeners(project_dir, {"a": {"pid": p.pid, "beat": now}, "b": {"pid": os.getpid(), "beat": now}})
    assert agent_notes.is_listening(project_dir)                                           # several listeners: one alive is enough
    open(os.path.join(project_dir, ".kurgu", "listening.json"), "w").write("garbage")
    assert not agent_notes.is_listening(project_dir)


# ---------------------------------------------------------------- a note id is a file name stem (security review S4)
def test_a_note_id_cannot_walk_out_of_the_notes_folder(project_dir, tmp_path):
    import schema
    victim = tmp_path / "victim"
    victim.mkdir()
    for bad in ("../../../escape", "..\\..\\x", "/etc/passwd", "a/b", "x" * 200, "n_1\x00", "nöte"):
        n = schema.normalize_note({"id": bad, "t": 1, "text": "x"})
        assert schema.NOTE_ID_RE.match(n["id"]) and "/" not in n["id"] and ".." not in n["id"], bad
        with pytest.raises(ValueError):
            agent_notes.audio_path(project_dir, bad)
        with pytest.raises(ValueError):
            agent_notes.sig_path(project_dir, bad)
    assert schema.normalize_note({"id": "n_ok-1", "t": 0, "text": ""})["id"] == "n_ok-1"
    assert schema.normalize_note({"id": "../a", "t": 0, "text": ""})["id"] == schema.normalize_note({"id": "../a", "t": 1, "text": ""})["id"]      # stable


def test_a_hostile_note_id_in_project_json_writes_nothing_outside_dot_kurgu_notes(project_dir, tmp_path):
    import server
    app = server.App(project_dir)
    with pytest.raises(Exception):
        app.note_frame_jpeg("../../../../tmp/kurgu-escape", 1.0)
    assert not os.path.exists("/tmp/kurgu-escape.jpg")
    p = json.load(open(os.path.join(project_dir, "project.json")))
    p["notes"] = [{"id": "../../outside", "t": 1.0, "text": "x", "sent": "2026-11-07T10:00:00"}]
    json.dump(p, open(os.path.join(project_dir, "project.json"), "w"))
    proj, notes = agent_notes.load_notes(project_dir)
    agent_notes.write_notes_md(project_dir, proj)
    assert not os.path.exists(os.path.join(os.path.dirname(project_dir), "outside.audio.json"))
    assert all(".." not in n["id"] for n in notes)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_notes_folder_is_refused(project_dir, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    os.symlink(str(other), os.path.join(project_dir, ".kurgu", "notes"))
    with pytest.raises(agent_notes.KurguRefusal):
        agent_notes.check_writable(project_dir)


# ---------------------------------------------------------------- which session a wake resumes (S6, T2) and which editor the CLI talks to (S5)
UUID = "11111111-2222-3333-4444-555555555555"


def _agent_env(tmp_path, **extra):
    env = dict(os.environ, KURGU_CONFIG=str(tmp_path / "uc"), CLAUDE_CODE_SESSION_ID=UUID)
    env.pop("KURGU_WAKE", None)
    env.update(extra)
    return env


def test_only_a_listening_agent_is_recorded_as_the_session_to_wake(project_dir, tmp_path):
    d = os.path.realpath(project_dir)
    ag = os.path.join(d, ".kurgu", "agents.json")
    env = _agent_env(tmp_path)
    for args in (("frame", d, "0.5"), ("notes", "list", d), ("notes", "wait", d, "--timeout", "0")):
        launch(*args, env=env)
        assert not os.path.exists(ag), args                         # running a command inside a session is not listening
    r = launch("notes", "wait", d, "--timeout", "1", env=env)
    assert r.returncode == 3 and os.path.exists(ag)
    import importlib
    monkey_env = {"KURGU_CONFIG": str(tmp_path / "uc")}
    old = {k: os.environ.get(k) for k in monkey_env}
    os.environ.update(monkey_env)
    try:
        assert agent_notes.latest_session(d, "claude")["session_id"] == UUID
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    os.remove(ag)
    launch("notes", "wait", d, "--timeout", "1", env=_agent_env(tmp_path, KURGU_WAKE="1"))
    assert not os.path.exists(ag)                                   # a woken run never becomes "the user's session"


def test_a_project_cannot_choose_the_session_a_wake_resumes(project_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("KURGU_CONFIG", str(tmp_path / "uc"))
    d = os.path.realpath(project_dir)
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    now = time.time()
    forged = [{"agent": "claude", "session_id": UUID, "pid": 1, "entrypoint": "cli", "last_seen": now},
              {"agent": "claude", "session_id": UUID, "pid": 1, "last_seen": now, "mac": "0" * 64}]
    json.dump(forged, open(os.path.join(d, ".kurgu", "agents.json"), "w"))
    assert agent_notes.latest_session(d, "claude") is None                                         # no valid MAC: not this user's record
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", UUID)
    monkeypatch.delenv("KURGU_WAKE", raising=False)
    agent_notes._last_recorded.clear()
    agent_notes.record_agent(d)
    assert agent_notes.latest_session(d, "claude")["session_id"] == UUID
    assert agent_notes.latest_session(d, "claude", now=time.time() - 3600) is None                 # a last_seen in the future is rejected
    items = json.load(open(os.path.join(d, ".kurgu", "agents.json")))
    items[-1]["session_id"] = UUID.replace("1", "2")
    json.dump(items, open(os.path.join(d, ".kurgu", "agents.json"), "w"))
    assert agent_notes.latest_session(d, "claude") is None                                         # edited after the fact: the MAC no longer fits
    assert agent_notes._UUID.fullmatch(UUID + "\n") is None


def test_server_json_cannot_point_the_cli_at_another_projects_editor(project_dir, tmp_path):
    import shutil
    import threading
    import mcp_server
    import server
    other = tmp_path / "other"
    shutil.copytree(project_dir, other)
    a_dir = os.path.realpath(project_dir)
    srv = server.make_server(a_dir, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        os.makedirs(other / ".kurgu", exist_ok=True)
        json.dump({"port": port, "pid": os.getpid(), "started": "2026-01-01T00:00:00"}, open(other / ".kurgu" / "server.json", "w"))       # shipped with the "other" project
        assert mcp_server.editor_port(str(other)) is None                                           # that editor serves a different folder
        os.makedirs(os.path.join(a_dir, ".kurgu"), exist_ok=True)
        json.dump({"port": port, "pid": os.getpid(), "started": "2026-01-01T00:00:00"}, open(os.path.join(a_dir, ".kurgu", "server.json"), "w"))
        assert mcp_server.editor_port(a_dir) == port
        json.dump({"port": port, "pid": os.getpid() + 1, "started": "x"}, open(os.path.join(a_dir, ".kurgu", "server.json"), "w"))
        assert mcp_server.editor_port(a_dir) is None                                                # the pid must be the editor's
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_state_json_and_hook_log_that_are_fifos_never_hang(project_dir):
    import mcp_server
    import threading
    import server
    d = os.path.realpath(project_dir)
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    for n in ("state.json", "hook.log"):
        os.mkfifo(os.path.join(d, ".kurgu", n))
    out = {}

    def run():
        out["state"] = mcp_server.tool_get_state({"project_dir": d}, {})
        out["app"] = server.App(d).get_state()
        out["log"] = agent_notes.hook_log_text(d)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(20)
    assert not t.is_alive() and out["log"] == "" and out["app"]["updated"] is None


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_refusal_in_notes_wait_is_a_clean_message_not_a_traceback(project_dir, tmp_path):
    d = os.path.realpath(project_dir)
    seed(d, [dict(NOTES[0], sent="2026-11-07T10:05:00")])
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    os.symlink(str(tmp_path / "victim"), os.path.join(d, ".kurgu", "inbox.jsonl"))
    r = launch("notes", "wait", d, "--timeout", "2")
    assert r.returncode == 1 and "Traceback" not in r.stderr and "error:" in r.stderr and "symbolic link" in r.stderr
    assert not (tmp_path / "victim").exists()


def test_send_with_a_mark_and_no_element_map_does_not_deadlock_the_server(project_dir, tmp_path, monkeypatch):
    """notes_send holds write_lock; the OCR fallback used to GET /api/frame on the same server, which needs write_lock: a deadlock until a 130 s timeout."""
    import threading
    import time as _t
    import server
    from test_server import Client
    import ocr_boxes
    seen = []
    monkeypatch.setattr(ocr_boxes, "read_frame", lambda path, *a, **k: seen.append(path) or {"text": [{"text": "HELLO", "l": 0, "t": 0, "r": 900, "b": 900, "conf": 1}], "objects": [],
                                                                                      "size": [960, 540], "backend": {"text": "fake", "objects": []}})
    p = json.load(open(os.path.join(project_dir, "project.json")))
    p["notes"] = [{"id": "n_1", "t": 1.0, "text": "x", "created": "2026-11-07T10:00:00", "marks": [MARK]}]
    json.dump(p, open(os.path.join(project_dir, "project.json"), "w"))
    s = server.make_server(project_dir, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        t0 = _t.time()
        st, res = Client(s.server_address[1]).req("POST", "/api/notes/send", {"ids": ["n_1"]})
        took = _t.time() - t0
        assert st == 200 and took < 60, took
        assert seen, "the OCR fallback ran"
        assert res["inbox"]["notes"][0]["marks"][0]["elements_under"][0]["label"] == "HELLO"
    finally:
        s.shutdown()
        s.server_close()


def test_notes_add_makes_a_human_note_with_a_mark_and_measured_bounds(project_dir):
    """T7: a script-made note carries bounds measured by Kurgu (renderer font metrics / native media size), not 'estimated'."""
    d = os.path.realpath(project_dir)
    r = launch("notes", "add", d, "--t", "2.0", "--text", "Başlık sola", "--rect", "100,200,300,120")
    assert r.returncode == 0, r.stderr
    p = json.load(open(os.path.join(d, "project.json")))
    n = [x for x in p["notes"] if x["text"] == "Başlık sola"][0]
    assert n["author"] == "user" and n["t"] == 2.0 and n["marks"][0]["type"] == "rect" and n["marks"][0]["w"] == 300 and 0 < n["marks"][0]["nx"] < 1
    assert launch("notes", "add", d, "--t", "1", "--text", "x", "--rect", "1,2,3").returncode == 1
    assert launch("notes", "add", d, "--t", "-1", "--text", "x").returncode == 1
    n["sent"] = "2026-11-07T10:00:00"
    json.dump(p, open(os.path.join(d, "project.json"), "w"))
    proj, notes = agent_notes.load_notes(d)
    v = agent_notes.note_view(d, [x for x in notes if x["id"] == n["id"]][0], proj)
    texts = [l for l in v["layers"] if l.get("type") == "text"]
    assert texts and all("approx" not in l["bounds"] and "measured by Kurgu" in l["anchor"] for l in texts), texts
    assert all(l["bounds"]["w"] > 5 and l["bounds"]["h"] > 5 for l in texts)


# ---------------------------------------------------------------- the Send button names the agent that is really there
def test_the_send_button_name_follows_who_listens_then_what_the_wake_starts(project_dir, tmp_path, monkeypatch):
    import server
    monkeypatch.setenv("KURGU_CONFIG", str(tmp_path / "uc"))
    monkeypatch.delenv("KURGU_AGENT_NAME", raising=False)
    monkeypatch.delenv("KURGU_ON_SEND", raising=False)
    d = os.path.realpath(project_dir)
    app = server.App(d)
    name = lambda: app.notes_info()["agent_name"]
    assert name() == ""                                                                                     # nobody: the editor says "Ajana gönder"
    os.makedirs(tmp_path / "uc", exist_ok=True)
    json.dump({"on_send": ["kurgu", "wake", "claude", "{dir}", "{seq}"]}, open(tmp_path / "uc" / "config.json", "w"))
    assert name() == "Claude"                                                                               # a wake hook that starts claude, nobody listening
    json.dump({"on_send": ["kurgu", "wake", "codex", "{dir}", "{seq}"]}, open(tmp_path / "uc" / "config.json", "w"))
    assert name() == "Codex"
    json.dump({"on_send": ["kurgu", "wake", "claude", "{dir}", "{seq}"]}, open(tmp_path / "uc" / "config.json", "w"))
    agent_notes._write_listeners(d, {"1": {"pid": os.getpid(), "since": time.time(), "beat": time.time(), "agent": "Codex"}})
    assert name() == "Codex"                                                                                # a listening codex beats the wake hook's claude
    monkeypatch.setenv("KURGU_AGENT_NAME", "Newton")
    assert name() == "Newton"                                                                               # an explicit name beats both
    monkeypatch.delenv("KURGU_AGENT_NAME")
    agent_notes._write_listeners(d, {"1": {"pid": os.getpid(), "since": time.time(), "beat": time.time() - 3600, "agent": "Codex"}})
    assert name() == "Claude"                                                                               # a stale heartbeat does not count as listening


def test_a_notes_wait_inside_codex_or_claude_beats_with_that_agents_label(project_dir, tmp_path):
    d = os.path.realpath(project_dir)
    for env_extra, want in (({"CODEX_THREAD_ID": UUID}, "Codex"), ({"CLAUDE_CODE_SESSION_ID": UUID}, "Claude")):
        env = dict(os.environ, KURGU_CONFIG=str(tmp_path / "uc"))
        for k in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "KURGU_AGENT_NAME", "KURGU_WAKE"):
            env.pop(k, None)
        env.update(env_extra)
        import subprocess
        p = subprocess.Popen([sys.executable, os.path.join(ROOT, "agent_notes.py"), "wait", d, "--timeout", "6"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                got = [e.get("agent") for e in agent_notes._read_listeners(d).values()]
                if want in got:
                    break
                time.sleep(0.2)
            assert want in got, got
        finally:
            p.terminate()
            p.wait(10)


@pytest.mark.parametrize("args", [["--t", "nan"], ["--t", "inf"], ["--t", "1", "--rect", "nan,2,3,4"], ["--t", "1", "--rect", "1,2,inf,4"]])
def test_notes_add_rejects_non_finite_numbers_and_writes_nothing(project_dir, args):
    d = os.path.realpath(project_dir)
    before = open(os.path.join(d, "project.json"), "rb").read()
    r = launch("notes", "add", d, "--text", "x", *args)
    assert r.returncode == 1 and "finite" in r.stderr
    assert open(os.path.join(d, "project.json"), "rb").read() == before
