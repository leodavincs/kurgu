"""The on_send hook: pressing Send can start the agent. User-level configuration only; detached; one run at a time; failures are logged and shown."""
import json
import os
import sys
import threading
import time

import pytest

import agent_notes
import kurgu_cli
import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_server import Client  # noqa: E402

PY = sys.executable


def write_script(tmp_path, body, name="hook.py"):
    p = tmp_path / name
    p.write_text("import os, sys, time, json\n" + body)
    return str(p)


def wait_for(pred, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition never true")


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """An empty user config folder and no env hook."""
    cdir = tmp_path / "userconf"
    cdir.mkdir()
    monkeypatch.setenv("KURGU_CONFIG", str(cdir))
    monkeypatch.delenv("KURGU_ON_SEND", raising=False)
    agent_notes._runners.clear()
    return cdir


@pytest.fixture()
def srv(project_dir, cfg):
    p = json.load(open(os.path.join(project_dir, "project.json"), encoding="utf-8"))
    p["notes"] = [{"id": f"n_{i}", "t": float(i), "text": f"note {i}"} for i in range(1, 5)]
    json.dump(p, open(os.path.join(project_dir, "project.json"), "w", encoding="utf-8"))
    s = server.make_server(project_dir, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield Client(s.server_address[1]), project_dir
    s.shutdown()
    s.server_close()


def set_hook(cfg, argv):
    (cfg / "config.json").write_text(json.dumps({"on_send": argv}))


def test_hook_comes_from_the_user_config_and_env_but_never_from_the_project(project_dir, cfg, monkeypatch):
    assert agent_notes.on_send_command() is None
    os.makedirs(os.path.join(project_dir, ".kurgu"))
    json.dump({"on_send": ["touch", "pwned"]}, open(os.path.join(project_dir, ".kurgu", "config.json"), "w"))     # a shared project folder
    assert agent_notes.on_send_command() is None and agent_notes.run_on_send(project_dir, 1, 1) == {}              # ... cannot run commands
    set_hook(cfg, ["echo", "from user config"])
    assert agent_notes.on_send_command() == ["echo", "from user config"]
    monkeypatch.setenv("KURGU_ON_SEND", json.dumps(["echo", "from env"]))
    assert agent_notes.on_send_command() == ["echo", "from env"]                                                  # env wins
    monkeypatch.setenv("KURGU_ON_SEND", "not json")
    assert agent_notes.on_send_command() is None
    monkeypatch.setenv("KURGU_ON_SEND", json.dumps("a string, not an argv list"))
    assert agent_notes.on_send_command() is None
    assert agent_notes.expand_command(["x", "{dir}/a", "{seq}-{count}", "{file}", "{nothing}"], "/p", 7, 3, "/p/NOTLAR.md") == ["x", "/p/a", "7-3", "/p/NOTLAR.md", "{nothing}"]


def test_send_runs_the_hook_detached_with_env_placeholders_and_log(srv, cfg, tmp_path):
    c, d = srv
    out = str(tmp_path / "out.json")
    script = write_script(tmp_path, "time.sleep(1.5)\njson.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env': {k: v for k, v in os.environ.items() if k.startswith('KURGU_')}}, open(sys.argv[1], 'w'))\nprint('hook says hi')\n")
    set_hook(cfg, [PY, script, out, "{dir}", "{seq}"])
    t0 = time.time()
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1", "n_2"]})
    assert st == 200 and res["hook"] == "started" and not res["hook_error"]
    assert time.time() - t0 < 1.4                                                   # the hook sleeps 1.5 s: the answer did not wait for it
    got = json.load(open(wait_for(lambda: os.path.exists(out) and out), "r"))
    assert got["argv"][1:] == [os.path.realpath(d), "1"] and os.path.realpath(got["cwd"]) == os.path.realpath(d)
    e = got["env"]
    assert e["KURGU_PROJECT_DIR"] == os.path.realpath(d) and e["KURGU_SEQ"] == "1" and e["KURGU_NOTES_COUNT"] == "2"
    assert e["KURGU_NOTES_FILE"] == os.path.join(os.path.realpath(d), "NOTLAR.md")
    log = wait_for(lambda: "exit code 0" in open(os.path.join(d, ".kurgu", "hook.log"), encoding="utf-8").read() and open(os.path.join(d, ".kurgu", "hook.log"), encoding="utf-8").read())
    assert "hook says hi" in log and "send #1, 2 notes" in log


def test_a_send_during_a_hook_run_queues_exactly_one_follow_up(srv, cfg, tmp_path):
    c, d = srv
    marks = tmp_path / "runs"
    marks.mkdir()
    script = write_script(tmp_path, "open(os.path.join(sys.argv[1], 'run-' + os.environ['KURGU_SEQ'] + '-' + os.environ['KURGU_NOTES_COUNT']), 'w').close()\ntime.sleep(2)\n")
    set_hook(cfg, [PY, script, str(marks)])
    assert c.req("POST", "/api/notes/send", {"ids": ["n_1"]})[1]["hook"] == "started"
    wait_for(lambda: os.listdir(marks))
    assert c.req("POST", "/api/notes/send", {"ids": ["n_2"]})[1]["hook"] == "queued"        # one run at a time ...
    assert c.req("POST", "/api/notes/send", {"ids": ["n_3"]})[1]["hook"] == "queued"        # ... and several sends make ONE follow-up
    assert sorted(os.listdir(marks)) == ["run-1-1"]
    wait_for(lambda: len(os.listdir(marks)) == 2, 30)
    time.sleep(2.5)
    assert sorted(os.listdir(marks)) == ["run-1-1", "run-3-1"]                              # the follow-up carries the latest send (#3)
    wait_for(lambda: not agent_notes._runners[os.path.realpath(d)].running, 30)
    assert c.req("POST", "/api/notes/send", {"ids": ["n_4"]})[1]["hook"] == "started"        # idle again: the next send starts normally


def test_a_hook_that_cannot_start_is_logged_and_shown_but_the_send_succeeds(srv, cfg):
    c, d = srv
    set_hook(cfg, ["/no/such/program-kurgu-test"])
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    assert st == 200 and res["sent"] == 1 and res["hook"] == "error" and "No such file" in res["hook_error"]
    log = open(os.path.join(d, ".kurgu", "hook.log"), encoding="utf-8").read()
    assert "could not start" in log or "failed to start" in log
    assert agent_notes.wait_for_send(d, timeout=0)                                          # the notes are in the inbox regardless
    agent_notes._runners.clear()
    set_hook(cfg, [PY, "-c", "import sys; sys.exit(3)"])                                    # starts, then fails: only the log knows
    assert c.req("POST", "/api/notes/send", {"ids": ["n_2"]})[1]["hook"] == "started"
    wait_for(lambda: "exit code 3" in open(os.path.join(d, ".kurgu", "hook.log"), encoding="utf-8").read())


def test_no_hook_configured_changes_nothing(srv):
    c, d = srv
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    assert st == 200 and res["hook"] == "" and not os.path.exists(os.path.join(d, ".kurgu", "hook.log"))


def test_setup_agent_wake_writes_the_user_config_only(tmp_path, monkeypatch, capsys, project_dir):
    cdir = tmp_path / "conf"
    monkeypatch.setenv("KURGU_CONFIG", str(cdir))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    assert kurgu_cli.setup_agent_main(["--wake"]) == 2                                      # which agent?
    assert kurgu_cli.setup_agent_main(["--codex", "--wake", "--dry-run"]) == 0
    assert not (cdir / "config.json").exists()                                              # dry run writes nothing
    assert "codex exec resume --last" in capsys.readouterr().out
    (cdir).mkdir()
    (cdir / "config.json").write_text(json.dumps({"agent_cmd": "keep me"}))
    assert kurgu_cli.setup_agent_main(["--codex", "--wake"]) == 0
    cfg = json.load(open(cdir / "config.json"))
    argv = cfg["on_send"]
    assert cfg["agent_cmd"] == "keep me" and argv[:4] == ["codex", "exec", "resume", "--last"] and "sandbox_mode=\"workspace-write\"" in argv
    assert "{dir}" in argv[-1] and "kurgu notes wait" in argv[-1] and "kurgu notes done" in argv[-1]
    assert not os.path.exists(os.path.join(project_dir, ".kurgu", "config.json"))
    monkeypatch.setattr(kurgu_cli.shutil, "which", lambda *_a, **_k: None)                  # never touch a real `claude` install from a test
    assert kurgu_cli.setup_agent_main(["--claude", "--wake"]) == 0
    argv = json.load(open(cdir / "config.json"))["on_send"]
    assert argv[:2] == ["claude", "-p"] and "--continue" in argv and "--permission-mode" in argv
    assert kurgu_cli.setup_agent_main(["--no-wake"]) == 0
    assert "on_send" not in json.load(open(cdir / "config.json")) and json.load(open(cdir / "config.json"))["agent_cmd"] == "keep me"
    assert 'tool_timeout_sec = 3600' in open(tmp_path / ".codex" / "config.toml", encoding="utf-8").read()


def test_notes_wait_ceiling_is_3500_seconds_over_mcp(project_dir, monkeypatch):
    import mcp_server
    seen = []
    monkeypatch.setattr(agent_notes, "wait_for_send", lambda d, since=None, timeout=None, **kw: (seen.append(timeout), None)[1])
    mcp_server.tool_notes_wait({"project_dir": project_dir, "timeout": 99999}, {"cancel": None})
    mcp_server.tool_notes_wait({"project_dir": project_dir}, {"cancel": None})
    mcp_server.tool_notes_wait({"project_dir": project_dir, "timeout": 3000}, {"cancel": None})
    assert seen == [3500.0, 120.0, 3000.0]
