"""POST /api/ai with a fake agent (tests/fake_agent.py), plus command building and i18n key parity."""
import json
import os
import re
import shlex
import sys
import threading
import time

import pytest

import ai
import server
from test_server import Client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="agent runner uses POSIX process groups and sh stubs")
FAKE = f"{shlex.quote(sys.executable)} {shlex.quote(os.path.join(ROOT, 'tests', 'fake_agent.py'))}"


@pytest.fixture()
def ai_srv(project_dir, monkeypatch):
    monkeypatch.setenv("KURGU_AGENT_CMD", FAKE)
    s = server.make_server(project_dir, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield Client(s.server_address[1]), project_dir, s.app
    s.app.ai.shutdown()
    s.shutdown()
    s.server_close()


def start(c, prompt, selected=("k_buyuk",), t=2.5):
    return c.req("POST", "/api/ai", {"prompt": prompt, "scope": {"selected": list(selected), "time": t}, "lang": "tr"})


def wait(c, jid, since=0, timeout=20):
    events, end = [], time.time() + timeout
    while time.time() < end:
        st, d = c.req("GET", f"/api/ai/{jid}?since={since}")
        assert st == 200
        events += d["events"]
        since = d["next"]
        if d["status"] != "running":
            return d, events
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def opacity(d, lid="k_buyuk"):
    return next(l for l in json.load(open(os.path.join(d, "project.json"), encoding="utf-8"))["layers"] if l["id"] == lid).get("opacity")


def test_done_edits_project_and_streams_events(ai_srv):
    c, d, app = ai_srv
    st, job = start(c, "make it dim")
    assert st == 202 and job["status"] == "running"
    final, events = wait(c, job["id"])
    assert final["status"] == "done" and final["final"] == "Done: I dimmed it." and final["changed"] is True
    assert final["error"] is None and final["elapsed"] >= 0
    assert opacity(d) == 0.5
    keys = [(e["kind"], e["key"]) for e in events]
    assert keys == [("thinking", "ai.ev.thinking"), ("tool", "ai.ev.read"), ("tool", "ai.ev.read"), ("tool", "ai.ev.edit"),
                    ("tool", "ai.ev.look"), ("text", "ai.ev.text")]
    assert [e["i"] for e in events] == list(range(len(events)))
    edit = next(e for e in events if e["key"] == "ai.ev.edit")
    assert edit["params"] == {"n": 1}
    assert next(e for e in events if e["key"] == "ai.ev.look")["params"] == {"t": 2.5}
    assert next(e for e in events if e["kind"] == "text")["text"] == "Done: I dimmed it."
    # `since` pages the event list
    st, d2 = c.req("GET", f"/api/ai/{job['id']}?since=4")
    assert [e["i"] for e in d2["events"]] == [4, 5] and d2["next"] == 6
    # the temp MCP config is gone
    assert not os.path.exists(app.ai.jobs[job["id"]]["mcp"])


def test_noedit_reports_unchanged(ai_srv):
    c, d, _ = ai_srv
    _, job = start(c, "noedit please")
    final, _ = wait(c, job["id"])
    assert final["status"] == "done" and final["changed"] is False


def test_error_and_login_codes(ai_srv):
    c, d, _ = ai_srv
    _, job = start(c, "error now")
    final, _ = wait(c, job["id"])
    assert final["status"] == "error" and final["code"] == "failed" and "kaboom" in final["error"] and final["final"] is None
    _, job = start(c, "login please")
    final, _ = wait(c, job["id"])
    assert final["status"] == "error" and final["code"] == "login"


def test_cancel_kills_process_tree_and_frees_the_slot(ai_srv):
    c, d, app = ai_srv
    _, job = start(c, "hang forever")
    assert start(c, "second")[0] == 409                   # one job at a time
    time.sleep(0.5)
    pid = app.ai.jobs[job["id"]]["proc"].pid
    st, res = c.req("POST", f"/api/ai/{job['id']}/cancel")
    assert st == 200 and res["status"] == "cancelled" and res["error"] is None
    assert app.ai.jobs[job["id"]]["proc"].poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    st, job2 = start(c, "make it dim")                    # slot is free again
    assert st == 202
    wait(c, job2["id"])


def test_timeout(ai_srv, monkeypatch):
    c, d, _ = ai_srv
    monkeypatch.setenv("KURGU_AI_TIMEOUT", "1")
    _, job = start(c, "hang forever")
    final, _ = wait(c, job["id"], timeout=15)
    assert final["status"] == "error" and final["code"] == "timeout"


def test_validation_and_unknown_job(ai_srv):
    c, d, _ = ai_srv
    assert c.req("POST", "/api/ai", {"prompt": "  "})[0] == 400
    assert c.req("GET", "/api/ai/nope")[0] == 404
    assert c.req("POST", "/api/ai/nope/cancel")[0] == 404


def test_missing_agent_is_424_with_hint(project_dir, monkeypatch, tmp_path):
    monkeypatch.delenv("KURGU_AGENT_CMD", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))            # no claude, no codex
    s = server.make_server(project_dir, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        st, res = start(Client(s.server_address[1]), "anything")
        assert st == 424 and "Claude Code" in res["error"]
    finally:
        s.shutdown()
        s.server_close()
    monkeypatch.setenv("KURGU_AGENT_CMD", "definitely-not-installed-xyz")
    with pytest.raises(ai.AiError) as e:
        ai.build_command({}, "p", "b", "m.json", project_dir)
    assert e.value.code == 424


def test_default_claude_command_flags(tmp_path, monkeypatch):
    stub = tmp_path / "claude"
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)
    monkeypatch.delenv("KURGU_AGENT_CMD", raising=False)
    monkeypatch.delenv("KURGU_AGENT", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    argv, fmt = ai.build_command({}, "do it", "the brief", "/tmp/m.json", "/proj")
    assert fmt == "claude" and argv[1:3] == ["-p", "do it"]
    for flag in ("--output-format", "--verbose", "--mcp-config", "--strict-mcp-config", "--allowedTools", "--append-system-prompt"):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert allowed == ["mcp__kurgu__" + t for t in ai.MCP_TOOLS] and "mcp__kurgu__render" not in allowed
    assert argv[argv.index("--append-system-prompt") + 1] == "the brief"
    # config picks codex explicitly
    (tmp_path / "codex").write_text("#!/bin/sh\n")
    (tmp_path / "codex").chmod(0o755)
    argv, fmt = ai.build_command({"agent": "codex"}, "do it", "b", "/tmp/m.json", "/proj")
    assert fmt == "codex" and argv[1:3] == ["exec", "--json"]


def test_brief_mentions_scope_and_language():
    b = ai.brief({"selected": ["a", "b"], "time": 12.34}, "tr", "/p")
    assert "a, b" in b and "12.34" in b and "Turkish" in b and "ONE edit_project" in b
    assert "moment at the playhead" in ai.brief({"selected": [], "time": None}, "en", "/p")


def test_i18n_key_sets_identical_and_ai_keys_exist():
    en = json.load(open(os.path.join(ROOT, "web", "i18n", "en.json"), encoding="utf-8"))
    tr = json.load(open(os.path.join(ROOT, "web", "i18n", "tr.json"), encoding="utf-8"))
    assert set(en) == set(tr)
    used = set(re.findall(r"t\('(ai\.[\w.]+)'", open(os.path.join(ROOT, "web", "js", "ai.js"), encoding="utf-8").read()))
    used.discard("ai.ph.")
    used.discard("ai.brief.err.")
    used |= {"ai.ph.follow", "ai.earlier", "ai.new", "ai.newTip", "ai.undone"}
    used |= {"ai.brief.err." + k for k in ("no_session", "unsupported", "no_agent", "timeout", "login", "limit", "empty", "busy", "failed", "tooLong")}
    used |= {"ai.ph." + str(i) for i in range(1, 5)} | {"ai.ev." + k for k in ("thinking", "read", "fonts", "edit", "look", "working", "text")}
    used |= {"ai.err." + k for k in ("noAgent", "login", "limit", "timeout", "busy", "failed")}
    assert used <= set(en), sorted(used - set(en))


# ------------------------------------------------------------------ project brief
FAKE_BRIEF = f"{shlex.quote(sys.executable)} {shlex.quote(os.path.join(ROOT, 'tests', 'fake_brief_agent.py'))}"


def wait_brief(c, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        st, d = c.req("GET", "/api/brief/generate")
        assert st == 200
        if d["status"] != "running":
            return d
        time.sleep(0.05)
    raise AssertionError("brief job did not finish")


def test_brief_get_put_backup_and_changes(ai_srv):
    c, d, _ = ai_srv
    assert c.req("GET", "/api/brief")[1]["text"] == ""
    assert c.req("PUT", "/api/brief", {"text": "- one"})[0] == 200
    assert c.req("PUT", "/api/brief", {"text": "- two"})[0] == 200
    assert open(os.path.join(d, "brief.md"), encoding="utf-8").read().strip() == "- two"
    assert c.req("GET", "/api/brief")[1]["text"] == "- two"
    backups = [x for x in os.listdir(os.path.join(d, ".kurgu", "backups")) if x.startswith("brief-")]
    assert len(backups) == 1 and open(os.path.join(d, ".kurgu", "backups", backups[0]), encoding="utf-8").read().strip() == "- one"
    assert "brief edited" in open(os.path.join(d, "changes.md"), encoding="utf-8").read()
    assert c.req("PUT", "/api/brief", {"text": "x" * 2001})[0] == 400
    assert c.req("PUT", "/api/brief", {})[0] == 400


def test_brief_in_ai_system_prompt(project_dir):
    assert "Project brief" not in ai.brief({"selected": [], "time": None}, "en", project_dir)
    with open(os.path.join(project_dir, "brief.md"), "w", encoding="utf-8") as f:
        f.write("- no orange\n" + "y" * 3000)
    b = ai.brief({"selected": [], "time": None}, "en", project_dir)
    assert "Project brief (from the user's conversation)" in b and "- no orange" in b
    assert b.count("y") < 2100                                   # truncated to 2000 chars


def test_brief_command_flags(monkeypatch, tmp_path):
    monkeypatch.delenv("KURGU_AGENT_CMD", raising=False)
    monkeypatch.delenv("KURGU_BRIEF_CMD", raising=False)
    monkeypatch.setattr(ai.shutil, "which", lambda n: "/bin/" + n if n in ("claude", "codex") else None)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ai.AiError) as e0:                        # --continue would silently start a blank chat: we check first
        ai.brief_command({"agent": "claude"}, "tr", str(tmp_path / "proj"))
    assert e0.value.code == "no_session"
    sd = ai.claude_sessions_dir(str(tmp_path / "proj"))
    os.makedirs(sd)
    open(os.path.join(sd, "abc.jsonl"), "w").write("{}\n")
    argv = ai.brief_command({"agent": "claude"}, "tr", str(tmp_path / "proj"))
    for flag in ("--continue", "--fork-session", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--model") + 1] == "haiku" and argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--output-format") + 1] == "json" and "Turkish" in argv[2]
    with pytest.raises(ai.AiError) as e:
        ai.brief_command({"agent": "codex"}, "en", "/p")
    assert e.value.code == "unsupported"


def test_brief_generate_writes_file_and_backs_up(ai_srv, monkeypatch):
    c, d, _ = ai_srv
    monkeypatch.setenv("KURGU_BRIEF_CMD", FAKE_BRIEF)
    c.req("PUT", "/api/brief", {"text": "- old"})
    st, snap = c.req("POST", "/api/brief/generate", {"lang": "tr"})
    assert st == 202
    end = wait_brief(c)
    assert end["status"] == "done" and end["brief"]["text"].startswith("- 12 s space teaser (tr)")
    assert "no orange" in open(os.path.join(d, "brief.md"), encoding="utf-8").read()
    assert any(x.startswith("brief-") for x in os.listdir(os.path.join(d, ".kurgu", "backups")))
    assert "[agent] brief summarised" in open(os.path.join(d, "changes.md"), encoding="utf-8").read()


def test_brief_generate_no_session_and_empty(ai_srv, monkeypatch):
    c, d, _ = ai_srv
    monkeypatch.setenv("KURGU_BRIEF_CMD", FAKE_BRIEF)
    open(os.path.join(d, "nosession"), "w").close()
    c.req("POST", "/api/brief/generate", {"lang": "en"})
    end = wait_brief(c)
    assert end["status"] == "error" and end["code"] == "no_session" and not os.path.exists(os.path.join(d, "brief.md"))
    os.remove(os.path.join(d, "nosession"))
    open(os.path.join(d, "nobrief"), "w").close()
    c.req("POST", "/api/brief/generate", {"lang": "en"})
    assert wait_brief(c)["code"] == "empty"


def test_brief_generate_codex_unsupported(ai_srv, monkeypatch):
    c, d, _ = ai_srv
    monkeypatch.delenv("KURGU_BRIEF_CMD", raising=False)
    monkeypatch.setenv("KURGU_AGENT_CMD", "codex exec")
    monkeypatch.setattr(ai.shutil, "which", lambda n: "/bin/" + n)
    st, snap = c.req("POST", "/api/brief/generate", {"lang": "en"})
    assert snap["status"] == "error" and snap["code"] == "unsupported"


def test_brief_generate_cancel_and_single(ai_srv, monkeypatch):
    c, d, _ = ai_srv
    monkeypatch.setenv("KURGU_BRIEF_CMD", FAKE_BRIEF)
    open(os.path.join(d, "hang"), "w").close()
    assert c.req("POST", "/api/brief/generate", {})[0] == 202
    assert c.req("POST", "/api/brief/generate", {})[0] == 409
    st, snap = c.req("POST", "/api/brief/generate/cancel")
    assert snap["status"] == "cancelled"
    monkeypatch.setenv("KURGU_BRIEF_TIMEOUT", "0.5")
    assert c.req("POST", "/api/brief/generate", {})[0] == 202
    assert wait_brief(c)["code"] == "timeout"


# ------------------------------------------------------------------ conversation threads
@pytest.fixture()
def claude_srv(project_dir, monkeypatch, tmp_path):
    """The real built-in `claude` command path, with tests/fake_claude.py standing in for the binary."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "claude"
    stub.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(os.path.join(ROOT, 'tests', 'fake_claude.py'))} \"$@\"\n")
    stub.chmod(0o755)
    log = tmp_path / "claude.log"
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("KURGU_AGENT_CMD", raising=False)
    monkeypatch.delenv("KURGU_AGENT", raising=False)
    s = server.make_server(project_dir, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()

    def calls():
        return [json.loads(x) for x in open(log).read().splitlines()]
    yield Client(s.server_address[1]), project_dir, s.app, calls
    s.app.ai.shutdown()
    s.shutdown()
    s.server_close()


def send(c, prompt, thread=None, selected=("k_buyuk",), t=2.5, **extra):
    st, job = c.req("POST", "/api/ai", dict({"prompt": prompt, "thread": thread, "lang": "en",
                                             "scope": {"selected": list(selected), "time": t}}, **extra))
    assert st == 202, job
    final, _ = wait(c, job["id"])
    return job, final


def test_resume_command_flags(tmp_path, monkeypatch):
    stub = tmp_path / "claude"
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)
    monkeypatch.delenv("KURGU_AGENT_CMD", raising=False)
    monkeypatch.delenv("KURGU_AGENT", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    argv, _ = ai.build_command({}, "less blur", "BRIEF", "/tmp/m.json", "/proj", resume="abc-123")
    assert "--no-session-persistence" not in argv
    assert argv[argv.index("--resume") + 1] == "abc-123" and argv[2] == "less blur"
    assert argv[argv.index("--append-system-prompt") + 1] == "BRIEF"            # re-passed on every turn
    assert argv[argv.index("--tools") + 1] == "" and "--allowedTools" in argv and "--strict-mcp-config" in argv
    assert "--resume" not in ai.build_command({}, "first", "B", "/tmp/m.json", "/proj")[0]
    (tmp_path / "codex").write_text("#!/bin/sh\n")
    (tmp_path / "codex").chmod(0o755)
    argv, fmt = ai.build_command({"agent": "codex"}, "again", "B", "/tmp/m.json", "/proj", resume="thr-1")
    assert fmt == "codex" and argv[1:3] == ["exec", "resume"] and "thr-1" in argv and "-C" not in argv


def test_thread_session_captured_and_follow_up_resumes(claude_srv):
    c, d, app, calls = claude_srv
    job1, f1 = send(c, "make it look like an old film")
    assert f1["final"].startswith("fresh") and f1["thread"] == job1["thread"] and job1["resumed"] is False
    th = c.req("GET", f"/api/ai/threads/{job1['thread']}")[1]
    sid = th["session"]
    assert sid.startswith("sess-") and [m["role"] for m in th["messages"]] == ["user", "agent"]
    assert th["messages"][0]["text"] == "make it look like an old film" and th["messages"][1]["text"] == f1["final"]
    job2, f2 = send(c, "less blur, keep the rest", thread=job1["thread"])
    assert job2["thread"] == job1["thread"] and job2["resumed"] is True and f2["final"].startswith("resumed")
    first, second = calls()
    assert "--resume" not in first["argv"] and "--no-session-persistence" not in first["argv"]
    assert second["argv"][second["argv"].index("--resume") + 1] == sid
    assert first["cwd"].endswith(os.path.join(".kurgu", "ai-work")) and second["cwd"] == first["cwd"]   # not the user's project folder
    th = c.req("GET", f"/api/ai/threads/{job1['thread']}")[1]
    assert len(th["messages"]) == 4 and th["session"] == sid


def test_lost_session_falls_back_to_history_in_prompt(claude_srv):
    c, d, app, calls = claude_srv
    job1, _ = send(c, "first idea")
    path = os.path.join(d, ".kurgu", "ai-threads.json")
    data = json.load(open(path))
    data["threads"][0]["session"] = "gone-1"
    json.dump(data, open(path, "w"))
    _, f2 = send(c, "second idea", thread=job1["thread"])
    assert f2["status"] == "done" and f2["final"].startswith("fresh")
    argvs = [x["argv"] for x in calls()]
    assert "--resume" in argvs[1] and "--resume" not in argvs[2]
    assert "first idea" in argvs[2][1] and "second idea" in argvs[2][1]                 # earlier turns passed as text
    assert c.req("GET", f"/api/ai/threads/{job1['thread']}")[1]["session"].startswith("sess-")


def test_custom_command_gets_history_and_session_is_stored(ai_srv):
    c, d, app = ai_srv
    j1, f1 = send(c, "noedit one")
    j2, _ = send(c, "noedit two", thread=j1["thread"])
    th = c.req("GET", f"/api/ai/threads/{j1['thread']}")[1]
    assert th["session"] == "fake" and len(th["messages"]) == 4 and j2["resumed"] is False
    assert c.req("POST", "/api/ai", {"prompt": "x", "thread": "nope"})[0] == 404


def test_scope_switching_folds_and_restores(ai_srv):
    c, d, app = ai_srv
    q = lambda sel, t: c.req("GET", "/api/ai/threads?sel=" + json.dumps(sel).replace(" ", "") + f"&t={t}")[1]
    j_layer, _ = send(c, "noedit layer", selected=("b", "a"))
    j_moment, _ = send(c, "noedit moment", selected=(), t=10.0)
    assert q(["a", "b"], 3)["active"]["id"] == j_layer["thread"]                      # sorted ids, time irrelevant
    assert q(["a"], 3)["active"] is None                                                # a different layer set: nothing
    assert q([], 10.9)["active"]["id"] == j_moment["thread"]                            # within +-1 s of the start
    assert q([], 9.2)["active"]["id"] == j_moment["thread"]
    far = q([], 12.5)
    assert far["active"] is None and [e["id"] for e in far["earlier"]] == [j_moment["thread"]]   # folded, still reachable
    assert q([], 40)["earlier"] == []
    # "new conversation": the old one folds into `earlier`, the new empty one is active
    new = c.req("POST", "/api/ai/threads", {"scope": {"selected": [], "time": 10.2}})[1]
    got = q([], 10.2)
    assert got["active"]["id"] == new["id"] and [e["id"] for e in got["earlier"]] == [j_moment["thread"]]
    assert got["earlier"][0]["first"] == "noedit moment" and got["earlier"][0]["turns"] == 1


def test_thread_persistence_file_limits_and_mark(ai_srv):
    c, d, app = ai_srv
    j, f = send(c, "noedit hello")
    path = os.path.join(d, ".kurgu", "ai-threads.json")
    raw = json.load(open(path))
    th = raw["threads"][0]
    assert th["key"] == "k_buyuk" and th["kind"] == "layers" and th["session"] == "fake" and th["messages"][0]["ts"]
    assert c.req("POST", f"/api/ai/threads/{j['thread']}/mark", {"message": f["message"], "undone": True})[1]["undone"] is True
    assert json.load(open(path))["threads"][0]["messages"][1]["undone"] is True
    assert c.req("POST", f"/api/ai/threads/{j['thread']}/mark", {"message": "zzz", "undone": True})[0] == 404
    for i in range(45):                                                                  # 20-turn cap (40 messages)
        app.ai.threads.add_message(j["thread"], {"role": "user", "text": f"m{i}"})
    assert len(app.ai.threads.get(j["thread"])["messages"]) == 40
    for i in range(55):                                                                  # 50-thread cap
        app.ai.threads.create([f"l{i}"], None)
    assert len(json.load(open(path))["threads"]) == 50
    assert ai.Threads(app).get(j["thread"]) is None       # (evicted by the 50-thread cap, oldest first)


# ---------------------------------------------------------------- H2: project folder must not choose a command
def test_project_config_cannot_set_agent_cmd(tmp_path, monkeypatch, capsys):
    import json as _json
    monkeypatch.delenv("KURGU_AGENT_CMD", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(ai.sys, "platform", "linux")
    ai._warned.clear()
    kd = tmp_path / ".kurgu"
    kd.mkdir()
    (kd / "config.json").write_text(_json.dumps({"agent_cmd": "touch /tmp/pwned", "brief_cmd": "x", "ai_timeout": 5, "agent": "codex"}))
    cfg = ai.read_config(str(kd))
    assert "agent_cmd" not in cfg and "brief_cmd" not in cfg
    assert cfg["ai_timeout"] == 5 and cfg["agent"] == "codex"
    assert "ignoring agent_cmd, brief_cmd" in capsys.readouterr().err
    ai.read_config(str(kd))
    assert capsys.readouterr().err == ""        # warned once


def test_user_config_and_env_still_work(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(ai.sys, "platform", "linux")
    assert ai.user_config_path() == str(tmp_path / "xdg" / "kurgu" / "config.json")
    os.makedirs(tmp_path / "xdg" / "kurgu")
    (tmp_path / "xdg" / "kurgu" / "config.json").write_text(_json.dumps({"agent_cmd": "sh -c true"}))
    kd = tmp_path / ".kurgu"
    kd.mkdir()
    assert ai.read_config(str(kd))["agent_cmd"] == "sh -c true"
    monkeypatch.setattr(ai.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "ad"))
    assert ai.user_config_path() == os.path.join(str(tmp_path / "ad"), "kurgu", "config.json")
