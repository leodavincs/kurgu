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
    assert c.req("POST", "/api/notes/send", {"ids": ["n_a"]})[1]["sent"] == 1
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


def test_wait_ignores_sends_made_before_it_started(project_dir):
    seed(project_dir)
    proj = json.load(open(os.path.join(project_dir, "project.json"), encoding="utf-8"))
    import schema
    agent_notes.append_inbox(project_dir, schema.normalize(proj), [], "2026-11-07T09:00:00")
    assert agent_notes.wait_for_send(project_dir, timeout=0.3, poll=0.05) is None


def test_launcher_dispatches_notes_and_keeps_editor_args(project_dir):
    seed(project_dir)
    r = subprocess.run(["bash", LAUNCHER, "notes", "list", project_dir, "--all"], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0 and "title appears too late" in r.stdout
    r = subprocess.run(["bash", LAUNCHER, "/no/such/folder"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "No such folder" in r.stderr             # `kurgu <dir>` still goes the editor way


def test_mcp_notes_tools(project_dir):
    seed(project_dir, [dict(NOTES[0], sent="2026-11-07T10:05:00"), NOTES[1]])
    m = Mcp(project_dir)
    try:
        listed = json.loads(first_text(m.ok("notes_list")))
        assert [n["id"] for n in listed["notes"]] == ["n_a"] and listed["notes"][0]["status"] == "sent"
        assert json.loads(first_text(m.ok("notes_list", status="open")))["notes"][0]["id"] == "n_b"
        waited = json.loads(first_text(m.ok("notes_wait", timeout=0.4)))
        assert waited["timeout"] is True and waited["inbox"] == 0
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
        return sum(1 for r, g, b in im.getdata() if r > 200 and 150 < g < 235 and b < 130)
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
