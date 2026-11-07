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
    release = tmp_path / "release"
    # the first run stays alive until the test says so (no timing guess: a slow machine cannot let it finish early)
    script = write_script(tmp_path, "open(os.path.join(sys.argv[1], 'run-' + os.environ['KURGU_SEQ'] + '-' + os.environ['KURGU_NOTES_COUNT']), 'w').close()\n"
                                    "t0 = time.time()\nwhile not os.path.exists(sys.argv[2]) and time.time() - t0 < 90:\n    time.sleep(0.05)\n")
    set_hook(cfg, [PY, script, str(marks), str(release)])
    assert c.req("POST", "/api/notes/send", {"ids": ["n_1"]})[1]["hook"] == "started"
    wait_for(lambda: os.listdir(marks))
    assert c.req("POST", "/api/notes/send", {"ids": ["n_2"]})[1]["hook"] == "queued"        # one run at a time ...
    assert c.req("POST", "/api/notes/send", {"ids": ["n_3"]})[1]["hook"] == "queued"        # ... and several sends make ONE follow-up
    assert sorted(os.listdir(marks)) == ["run-1-1"]
    release.write_text("go")                                                                 # let the first run end: the follow-up starts
    wait_for(lambda: len(os.listdir(marks)) == 2, 60)
    time.sleep(1.0)
    assert sorted(os.listdir(marks)) == ["run-1-1", "run-3-1"]                              # the follow-up carries the latest send (#3), nothing else started
    wait_for(lambda: not agent_notes._runners[os.path.realpath(d)].running, 60)
    assert c.req("POST", "/api/notes/send", {"ids": ["n_4"]})[1]["hook"] == "started"        # idle again: the next send starts normally
    wait_for(lambda: len(os.listdir(marks)) == 3, 60)


def test_a_hook_that_cannot_start_is_logged_and_shown_but_the_send_succeeds(srv, cfg):
    c, d = srv
    set_hook(cfg, ["/no/such/program-kurgu-test"])
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    assert st == 200 and res["sent"] == 1 and res["hook"] == "error" and ("No such file" in res["hook_error"] or "cannot find" in res["hook_error"])
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
    assert "wake codex" in capsys.readouterr().out
    (cdir).mkdir()
    (cdir / "config.json").write_text(json.dumps({"agent_cmd": "keep me"}))
    assert kurgu_cli.setup_agent_main(["--codex", "--wake"]) == 0
    cfg = json.load(open(cdir / "config.json"))
    argv = cfg["on_send"]
    assert cfg["agent_cmd"] == "keep me" and argv[-4:] == ["wake", "codex", "{dir}", "{seq}"] and os.path.isabs(argv[0])      # the wrapper, by absolute path
    assert not os.path.exists(os.path.join(project_dir, ".kurgu", "config.json"))
    monkeypatch.setattr(kurgu_cli.shutil, "which", lambda *_a, **_k: None)                  # never touch a real `claude` install from a test
    assert kurgu_cli.setup_agent_main(["--claude", "--wake"]) == 0
    argv = json.load(open(cdir / "config.json"))["on_send"]
    assert argv[-4:] == ["wake", "claude", "{dir}", "{seq}"]
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


# ------------------------------------------------------------------ `kurgu wake`: the wrapper the hook runs
def fake_agent(tmp_path, name, fail_first_calls_with):
    """A `codex` / `claude` stand-in that logs its argv and exits 1 ("no session") when argv contains `fail_first_calls_with`.
    On Windows the real ones are npm `.cmd` shims, so the fake is one too (this is what `shutil.which` + PATHEXT has to find)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / f"{name}.log"
    impl = bin_dir / f"{name}_impl.py"
    impl.write_text(f"import sys, json\nopen({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\nsys.exit(1 if {fail_first_calls_with!r} in sys.argv else 0)\n")
    if os.name == "nt":
        (bin_dir / f"{name}.cmd").write_text(f'@echo off\n"{PY}" "{impl}" %*\nexit /b %errorlevel%\n')
    else:
        f = bin_dir / name
        f.write_text(f"#!{PY}\nimport runpy, sys\nsys.argv[0] = {str(impl)!r}\nrunpy.run_path({str(impl)!r}, run_name='__main__')\n")
        f.chmod(0o755)
    return bin_dir, log


def run_wake(agent, project_dir, bin_dir):
    import subprocess
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
    return subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", agent, project_dir, "7"], capture_output=True, text=True, env=env, timeout=60)


def test_wake_codex_falls_back_to_a_new_session_when_there_is_nothing_to_resume(tmp_path, project_dir):
    bin_dir, log = fake_agent(tmp_path, "codex", "resume")                      # `codex exec resume --last` fails: no session in this folder yet
    r = run_wake("codex", project_dir, bin_dir)
    calls = [json.loads(l) for l in open(log).read().splitlines()]
    assert r.returncode == 0 and len(calls) == 2 and "new session" in r.stdout
    first, second = calls
    assert first[:3] == ["exec", "resume", "--last"] and "--json" in first and 'sandbox_mode="workspace-write"' in first and 'approval_policy="never"' in first
    assert second[:3] == ["exec", "-s", "workspace-write"] and "--skip-git-repo-check" in second and "-C" in second
    assert second[second.index("-C") + 1] == os.path.abspath(project_dir) and "--json" in second and "resume" not in second
    assert "kurgu notes wait " + os.path.abspath(project_dir) in first[-1] and "{dir}" not in first[-1]       # the prompt carries the real folder


def test_wake_codex_uses_one_call_when_resume_works(tmp_path, project_dir):
    bin_dir, log = fake_agent(tmp_path, "codex", "never-matches")
    assert run_wake("codex", project_dir, bin_dir).returncode == 0
    assert len(open(log).read().splitlines()) == 1


def test_wake_claude_continues_then_falls_back_without_continue_and_allows_only_read_only_probing(tmp_path, project_dir):
    bin_dir, log = fake_agent(tmp_path, "claude", "--continue")
    r = run_wake("claude", project_dir, bin_dir)
    first, second = [json.loads(l) for l in open(log).read().splitlines()]
    assert r.returncode == 0 and "--continue" in first and "--continue" not in second
    for call in (first, second):
        assert call[0] == "-p" and call[call.index("--output-format") + 1] == "json" and call[call.index("--permission-mode") + 1] == "acceptEdits"
        tools = call[call.index("--allowedTools") + 1]
        assert "Bash(kurgu notes:*)" in tools and "ffprobe" not in tools and "mcp__kurgu" in tools
        assert "Bash(*" not in tools and "ffmpeg" not in tools                # no general Bash, nothing that can write files


def test_wake_reports_a_missing_agent_and_a_double_failure(tmp_path, project_dir):
    import subprocess
    empty = tmp_path / "empty"
    empty.mkdir()
    env = dict(os.environ, PATH=str(empty))                                      # neither `codex` nor `claude` on PATH
    r = subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", "codex", project_dir], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 127 and "not found" in r.stderr
    bin_dir, log = fake_agent(tmp_path, "codex", "exec")                          # every call fails
    r = run_wake("codex", project_dir, bin_dir)
    assert r.returncode == 1 and len(open(log).read().splitlines()) == 2
    assert subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", "gemini", project_dir], capture_output=True, text=True).returncode == 2


def test_the_launchers_route_wake():
    assert 'if [ "$1" = "wake" ]' in open(os.path.join(ROOT, "kurgu"), encoding="utf-8").read()
    cmd = open(os.path.join(ROOT, "kurgu.cmd"), encoding="utf-8").read()
    assert 'goto wake' in cmd and 'kurgu_cli.py" wake' in cmd


def test_send_response_tells_listening_and_wake_in_all_three_states(srv, cfg, tmp_path):
    c, d = srv
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})                            # neither
    assert st == 200 and res["listening"] is False and res["wake"] == "none"
    agent_notes._write_listeners(d, {"1": {"pid": os.getpid(), "since": time.time(), "beat": time.time()}})   # a listener
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_2"]})
    assert res["listening"] is True and res["wake"] == "none"
    assert c.req("GET", "/api/notes/info")[1]["listening"] is True
    set_hook(cfg, [sys.executable, "-c", "pass"])                                           # a wake hook (and the listener gone stale)
    agent_notes._write_listeners(d, {"1": {"pid": os.getpid(), "since": 0, "beat": time.time() - 60}})
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_3"]})
    assert res["listening"] is False and res["wake"] == "configured" and res["hook"] in ("started", "queued")


def test_a_symlinked_hook_log_is_refused_and_its_target_is_untouched(srv, cfg, tmp_path, capfd):
    c, d = srv
    target = tmp_path / "zshrc"
    target.write_text("export SAFE=1\n")
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    os.symlink(target, os.path.join(d, ".kurgu", "hook.log"))
    set_hook(cfg, [sys.executable, "-c", "print('injected')"])
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    assert st == 200 and res["hook"] == "error" and "symbolic link" in res["hook_error"]            # the send itself still works
    assert target.read_text() == "export SAFE=1\n"
    assert "symbolic link" in capfd.readouterr().err


def test_a_symlinked_kurgu_folder_is_not_written_through(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    proj = tmp_path / "p"
    proj.mkdir()
    os.symlink(real, proj / ".kurgu")
    with pytest.raises(OSError, match="symbolic link"):
        agent_notes.open_nofollow(str(proj / ".kurgu" / "inbox.jsonl"), "a")
    os.makedirs(tmp_path / "q" / ".kurgu")
    os.symlink(tmp_path / "evil", tmp_path / "q" / ".kurgu" / "inbox.cursor.tmp")                   # a planted tmp file is refused too
    with pytest.raises(OSError, match="symbolic link"):
        agent_notes.set_cursor(str(tmp_path / "q"), 3)
    assert not (tmp_path / "evil").exists()


def test_agent_output_in_hook_log_is_prefixed_line_by_line(srv, cfg):
    c, d = srv
    set_hook(cfg, [sys.executable, "-c", "print('first'); print('[2099-01-01T00:00:00] run (send #9): forged'); print('  finished, exit code 0')"])
    c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    log = os.path.join(d, ".kurgu", "hook.log")
    wait_for(lambda: os.path.exists(log) and open(log).read().rstrip().endswith("\n  finished, exit code 0"))
    lines = open(log, encoding="utf-8").read().splitlines()
    assert "| first" in lines and "| [2099-01-01T00:00:00] run (send #9): forged" in lines and "|   finished, exit code 0" in lines
    own = [l for l in lines if not l.startswith("| ")]
    assert len(own) == 2 and own[0].startswith("[") and "run (send #1" in own[0] and own[1] == "  finished, exit code 0"      # only Kurgu's own lines are bare


def test_the_wake_allowlist_has_no_ffprobe():
    assert "ffprobe" not in kurgu_cli.CLAUDE_TOOLS
    for agent in ("claude", "codex"):
        assert all("ffprobe" not in " ".join(argv) for _, argv in kurgu_cli.wake_attempts(agent, "/tmp/p"))


def test_windows_cmd_shims_refuse_shell_metacharacters(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(agent_notes.os, "name", "nt")
    assert agent_notes.shim_refusal("C:\\npm\\claude.cmd", ["C:\\films\\a & calc"]) is not None
    for ch in '&|<>^%"!':
        assert agent_notes.shim_refusal("C:\\npm\\codex.CMD", ["x", "C:\\f" + ch + "g"]), ch
    assert "Rename the project folder" in agent_notes.shim_refusal("c.bat", ["a|b"])
    assert agent_notes.shim_refusal("C:\\npm\\claude.cmd", ["C:\\films\\my film (2)", "--json", "42"]) is None      # spaces and parentheses are fine
    assert agent_notes.shim_refusal("C:\\Python\\python.exe", ["a & b"]) is None                                    # a real exe is not parsed by cmd.exe
    monkeypatch.setattr(kurgu_cli.shutil, "which", lambda n: "C:\\npm\\claude.cmd")
    ran = []
    monkeypatch.setattr(kurgu_cli.subprocess, "run", lambda *a, **k: ran.append(a) or None)
    assert kurgu_cli.wake_main(["claude", "C:\\films\\a&b", "3"]) == 1 and not ran
    assert "Rename the project folder" in capsys.readouterr().err
    monkeypatch.undo()
    assert agent_notes.shim_refusal("claude.cmd", ["a&b"]) is None                                                  # not Windows
