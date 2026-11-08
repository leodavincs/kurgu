"""The on_send hook: pressing Send can start the agent. User-level configuration only; detached; one run at a time; failures are logged and shown."""
import json
import shutil
import os
import sys
import threading
import time
from pathlib import Path

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
    script = write_script(tmp_path, "time.sleep(6)\njson.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'env': {k: v for k, v in os.environ.items() if k.startswith('KURGU_')}}, open(sys.argv[1], 'w'))\nprint('hook says hi')\n")
    set_hook(cfg, [PY, script, out, "{dir}", "{seq}"])
    t0 = time.time()
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1", "n_2"]})
    assert st == 200 and res["hook"] == "started" and not res["hook_error"]
    assert time.time() - t0 < 5                                                     # the hook sleeps 6 s: the answer did not wait for it (slack for slow CI runners)
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


def wake_env_for(tmp_path, bin_dir, **extra):
    """A hermetic environment for `kurgu wake`: its own Kurgu config folder and a Codex home that has a (fake) login to link."""
    real = tmp_path / "realcodex"
    real.mkdir(exist_ok=True)
    (real / "auth.json").write_text('{"fake": true}')
    (tmp_path / "kconf").mkdir(exist_ok=True)
    return dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], CODEX_HOME=str(real), KURGU_CONFIG=str(tmp_path / "kconf"), **extra)


def run_wake(agent, project_dir, bin_dir):
    import subprocess
    env = wake_env_for(bin_dir.parent, bin_dir)
    return subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", agent, project_dir, "7"], capture_output=True, text=True, env=env, timeout=60)


def test_wake_codex_falls_back_to_a_new_session_when_there_is_nothing_to_resume(tmp_path, project_dir):
    bin_dir, log = fake_agent(tmp_path, "codex", "resume")                      # `codex exec resume --last` fails: no session in this folder yet
    r = run_wake("codex", project_dir, bin_dir)
    calls = [json.loads(l) for l in open(log).read().splitlines()]
    assert r.returncode == 0 and len(calls) == 2 and "new session" in r.stdout
    first, second = calls
    assert first[:3] == ["exec", "resume", "--last"] and "--json" in first and "sandbox_mode=workspace-write" in first and "approval_policy=never" in first
    assert not any('"' in a for a in first[:-1])                                    # nothing cmd.exe would read as a quote toggle (codex.cmd)
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
        assert "Bash(kurgu notes:*)" in tools and "Bash(kurgu frame:*)" in tools and "Bash(kurgu strip:*)" in tools
        assert "Bash(kurgu detect:*)" in tools and "Bash(kurgu rerender:*)" in tools and "mcp__kurgu" in tools and "ffprobe" not in tools      # ffprobe can read URLs


        assert "ffprobe" not in tools and "mcp__kurgu" in tools
        assert "Bash(*" not in tools and "ffmpeg" not in tools and "python" not in tools and "render.py" not in tools                # no general Bash, nothing that can write files


def test_wake_reports_a_missing_agent_and_a_double_failure(tmp_path, project_dir):
    import subprocess
    empty = tmp_path / "empty"
    empty.mkdir()
    env = dict(os.environ, PATH=str(empty), KURGU_CONFIG=str(tmp_path / "kconf"), CODEX_HOME=str(tmp_path / "nohome"))      # neither `codex` nor `claude` on PATH
    r = subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", "codex", project_dir], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 127 and "not found" in r.stderr
    bin_dir, log = fake_agent(tmp_path, "codex", "exec")                          # every call fails
    r = run_wake("codex", project_dir, bin_dir)
    assert r.returncode == 1 and len(open(log).read().splitlines()) == 2
    assert subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", "gemini", project_dir], capture_output=True, text=True).returncode == 2


def test_the_launchers_route_wake():
    assert 'if [ "$1" = "wake" ]' in open(os.path.join(ROOT, "kurgu"), encoding="utf-8").read()
    cmd = open(os.path.join(ROOT, "kurgu.cmd"), encoding="utf-8").read()
    assert 'kurgu_cli.py" %*' in cmd


def test_hook_log_prefixes_agent_output_so_it_cannot_forge_log_lines(srv, cfg, tmp_path):
    c, d = srv
    forged = "[2026-11-07T10:00:00]   finished, exit code 0"
    script = write_script(tmp_path, f"print({forged!r})\nprint('plain')\n")
    set_hook(cfg, [PY, script])
    assert c.req("POST", "/api/notes/send", {"ids": ["n_1"]})[1]["hook"] == "started"
    log = wait_for(lambda: open(os.path.join(d, ".kurgu", "hook.log"), encoding="utf-8").read().count("exit code 0") >= 2 and open(os.path.join(d, ".kurgu", "hook.log"), encoding="utf-8").read())
    lines = log.splitlines()
    assert "| " + forged in lines and "| plain" in lines
    assert not any(l.startswith("[2026-11-07T10:00:00]") for l in lines)                 # the forged line never starts a log line by itself


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_hook_log_is_never_written_through_a_symlink(srv, cfg, tmp_path):
    c, d = srv
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    os.symlink(str(victim), os.path.join(d, ".kurgu", "hook.log"))
    set_hook(cfg, [PY, write_script(tmp_path, "print('x')\n")])
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    assert st == 200 and res["hook"] == "error"
    time.sleep(0.5)
    assert victim.read_text() == "keep"
    os.remove(os.path.join(d, ".kurgu", "hook.log"))
    other = tmp_path / "other"
    other.mkdir()
    import shutil
    shutil.rmtree(os.path.join(d, ".kurgu"))
    os.symlink(str(other), os.path.join(d, ".kurgu"))
    agent_notes._log(d, "boom")
    assert os.listdir(str(other)) == []


def test_cmd_shims_never_get_shell_metacharacters_on_the_command_line(tmp_path, project_dir, monkeypatch):
    import kurgu_paths
    assert kurgu_paths.shim_unsafe("C:/x/claude.cmd", ["claude", "-p", "a&b"]) == ["a&b"]
    assert kurgu_paths.shim_unsafe("C:/x/claude.exe", ["claude", "-p", "a&b"]) == []
    assert kurgu_paths.shim_unsafe("/x/codex.BAT", ["codex", "50%", "x"]) == ["50%"]
    calls = []

    class R:
        returncode = 0
    monkeypatch.setattr("shutil.which", lambda n: "C:/x/claude.cmd")
    monkeypatch.setattr("subprocess.run", lambda run, **kw: (calls.append((run, kw)), R())[1])
    assert kurgu_cli.wake_main(["claude", project_dir, "1"]) == 0
    run, kw = calls[0]
    assert kurgu_cli.WAKE_PROMPT.split("{dir}")[0] in kw["input"] and not any("Kurgu'dan yeni" in a for a in run)      # the prompt went to stdin
    assert all(not any(ch in a for ch in kurgu_paths.SHELL_META) for a in run[1:])
    calls.clear()
    evil = tmp_path / "a&calc"
    evil.mkdir()
    (evil / "project.json").write_text(open(os.path.join(project_dir, "project.json")).read())
    monkeypatch.setattr(agent_notes, "IS_WINDOWS", True)
    assert kurgu_cli.wake_main(["claude", str(evil), "1"]) == 1 and calls == []     # a project path cmd.exe would read is refused as a whole (shim_refusal), like the hook
    monkeypatch.setattr(agent_notes, "IS_WINDOWS", os.name == "nt")
    monkeypatch.setattr(kurgu_cli, "wake_attempts", lambda agent, d, session=None: [("x", ["claude", "-p", "x", "--add-dir", "C:/a&b"])])
    assert kurgu_cli.wake_main(["claude", project_dir, "1"]) == 126 and calls == []   # an argument that cmd.exe would interpret: refuse to spawn
    # the on_send hook refuses too
    cmd = agent_notes.expand_command(["C:/x/tool.cmd", "{dir}"], str(evil), 1, 1, "f")
    assert kurgu_paths.shim_unsafe(cmd[0], cmd) == [str(evil)]


# ------------------------------------------------------------------ wake status: .kurgu/wake.json + /api/notes/info
def test_wake_status_reports_running_then_a_failed_run_with_its_cause(srv, cfg):
    c, d = srv
    d = os.path.realpath(d)
    assert c.req("GET", "/api/notes/info")[1]["wake"] == {}                                  # nothing ran yet
    release = os.path.join(d, "release")
    script = write_script(Path(os.path.dirname(release)), "t0 = time.time()\nwhile not os.path.exists(sys.argv[1]) and time.time() - t0 < 60:\n    time.sleep(0.05)\n"
                                                  "print('\\x1b[31mNot logged in · Please run /login\\x1b[0m')\nsys.exit(1)\n", "failing.py")
    set_hook(cfg, [PY, script, release])
    assert c.req("POST", "/api/notes/send", {"ids": ["n_1"]})[1]["hook"] == "started"
    w = c.req("GET", "/api/notes/info")[1]["wake"]
    assert w["state"] == "running" and w["seq"] == 1 and w["count"] == 1 and w["exit_code"] is None
    open(release, "w").close()
    w = wait_for(lambda: (lambda x: x if x.get("state") == "finished" else None)(c.req("GET", "/api/notes/info")[1]["wake"]))
    assert w["exit_code"] == 1 and w["reason"] == "login" and w["summary"] == "Not logged in · Please run /login" and "\x1b" not in "".join(w["tail"])
    st = json.load(open(os.path.join(d, ".kurgu", "wake.json"), encoding="utf-8"))
    assert st["state"] == "finished" and "offset" not in st
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{c.port}/api/notes/hook-log") as r:
        assert "exit code 1" in r.read().decode() and r.headers["Content-Type"].startswith("text/plain")


def test_wake_status_ok_run_has_no_error_and_json_result_lines_are_unwrapped(srv, cfg):
    c, d = srv
    script = write_script(Path(os.path.dirname(os.path.realpath(d))), "print(json.dumps({'type': 'result', 'result': 'all good'}))\n", "ok.py")
    set_hook(cfg, [PY, script])
    c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    w = wait_for(lambda: (lambda x: x if x.get("state") == "finished" else None)(c.req("GET", "/api/notes/info")[1]["wake"]))
    assert w["exit_code"] == 0 and w["reason"] == "" and w["summary"] == "" and w["tail"] == []
    assert agent_notes.log_tail(os.path.realpath(d), 0)[-1] == "all good"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_wake_status_missing_command_and_symlink_safety(srv, cfg, tmp_path):
    c, d = srv
    d = os.path.realpath(d)
    set_hook(cfg, ["/no/such/program-kurgu-test"])
    c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    w = c.req("GET", "/api/notes/info")[1]["wake"]
    assert w["state"] == "finished" and w["reason"] == "missing" and w["exit_code"] == 127
    victim = tmp_path / "victim.txt"                                                      # a planted symlink is replaced, never written through
    victim.write_text("keep")
    wp = os.path.join(d, ".kurgu", "wake.json")
    os.remove(wp)
    os.symlink(victim, wp)
    agent_notes.write_wake(d, {"state": "finished", "exit_code": 1})
    assert victim.read_text() == "keep" and not os.path.islink(wp)
    os.remove(wp)
    os.symlink(victim, wp)
    assert agent_notes.read_wake(d) is None                                               # and a symlink is never read
    assert agent_notes.wake_reason(1, ["bash: claude: command not found"]) == "missing"


def test_a_running_record_without_a_live_runner_is_reported_as_nothing(srv):
    c, d = srv
    d = os.path.realpath(d)
    agent_notes.write_wake(d, {"state": "running", "seq": 3})
    agent_notes._runners.clear()
    assert agent_notes.wake_status(d) == {}


def test_wake_status_strips_the_log_prefix_and_explains_a_codex_policy_stop(srv, cfg, tmp_path):
    c, d = srv
    d = os.path.realpath(d)
    script = write_script(Path(os.path.dirname(d)), "print('kurgu wake: codex not started (no_auth_file): command rules could not be applied: no auth.json')\nsys.exit(78)\n", "codexstop.py")
    set_hook(cfg, [PY, script])
    c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    w = wait_for(lambda: (lambda x: x if x.get("state") == "finished" else None)(c.req("GET", "/api/notes/info")[1]["wake"]))
    assert w["exit_code"] == 78 and w["reason"] == "codex_no_auth_file" and "keyring" in w["summary"]
    assert not any(l.startswith("| ") for l in w["tail"])
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{c.port}/api/notes/hook-log") as r:
        text = r.read().decode()
    assert "codex not started" in text and "| kurgu wake" not in text
    assert agent_notes.wake_reason(78, []) == "codex_policy"
    assert agent_notes.wake_reason(1, ["Not logged in · Please run /login"]) == "login"


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


def test_wake_starts_a_new_claude_session_when_the_last_one_is_active(tmp_path, monkeypatch, project_dir):
    import re
    conf = tmp_path / "cc"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(conf))
    sess = conf / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(project_dir))
    sess.mkdir(parents=True)
    labels = lambda: [l for l, _ in kurgu_cli.wake_attempts("claude", project_dir)]
    assert labels() == ["continue the last session", "new session"]                         # no session yet
    f = sess / "abc.jsonl"
    f.write_text("{}")
    assert len(labels()) == 1 and labels()[0].startswith("new session") and "--continue" not in kurgu_cli.wake_attempts("claude", project_dir)[0][1]   # fresh = open
    os.utime(f, (time.time() - 600, time.time() - 600))
    assert labels() == ["continue the last session", "new session"]                         # idle for 10 minutes: continuing is safe


SID = "13ff2c99-344a-4ff0-8de8-a914d239b9e8"
SID2 = "01a1b6f3-808d-7740-9d00-818cd6b19354"


def test_session_is_recorded_from_env_only_and_the_innermost_agent_wins(project_dir, monkeypatch):
    env = {"CLAUDE_CODE_SESSION_ID": SID, "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "123"}
    assert agent_notes.env_session(env) == {"agent": "claude", "session_id": SID, "pid": 123, "entrypoint": "cli"}
    assert agent_notes.env_session(dict(env, CODEX_THREAD_ID=SID2, CODEX_SESSION_ID=SID2))["agent"] == "codex"      # Codex started inside Claude: both sets present
    assert agent_notes.env_session(dict(env, CLAUDE_CODE_SESSION_ID="../../etc; rm -rf")) is None                  # not a uuid
    assert agent_notes.env_session(dict(env, KURGU_WAKE="1")) is None                                               # a woken run is not the user's session
    assert agent_notes.env_session({}) is None
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    agent_notes.record_agent(project_dir)
    got = agent_notes.read_agents(project_dir)
    assert len(got) == 1 and got[0]["session_id"] == SID and got[0]["last_seen"] > 0
    assert agent_notes.latest_session(project_dir, "claude")["session_id"] == SID and agent_notes.latest_session(project_dir, "codex") is None
    assert agent_notes.latest_session(project_dir, "claude", now=time.time() + 90000) is None                       # a day old: not resumed
    open(os.path.join(project_dir, ".kurgu", "agents.json"), "w").write(json.dumps([{"agent": "claude", "session_id": "evil; id", "last_seen": time.time()}]))
    assert agent_notes.latest_session(project_dir, "claude") is None                                                # a hand-edited file cannot inject an argument


def test_agents_json_keeps_the_last_five(project_dir):
    for i in range(7):
        agent_notes._last_recorded.clear()
        agent_notes.record_agent(project_dir, {"CLAUDE_CODE_SESSION_ID": "00000000-0000-4000-8000-00000000000%d" % i})
    ids = [e["session_id"][-1] for e in agent_notes.read_agents(project_dir)]
    assert ids == ["2", "3", "4", "5", "6"]


def test_wake_routes_to_the_recorded_session(project_dir, cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    cl = kurgu_cli.wake_attempts("claude", project_dir, {"session_id": SID})
    assert cl[0][1][:4] == ["claude", "--bg", "--resume", SID] and cl[0][1][4].startswith("Kurgu'dan") and cl[0][1][-2:] == ["--allowedTools", kurgu_cli.CLAUDE_TOOLS]
    assert [l for l, _ in cl][1:] == ["continue the last session", "new session"]                                    # fallbacks stay
    cx = kurgu_cli.wake_attempts("codex", project_dir, {"session_id": SID2})
    assert cx[0][1][:4] == ["codex", "exec", "resume", SID2] and cx[1][0] == "new session"
    roll = tmp_path / "cx" / "sessions" / "2026" / "11" / "07"
    roll.mkdir(parents=True)
    (roll / f"rollout-2026-11-07T10-00-00-{SID2}.jsonl").write_text("{}")
    act = kurgu_cli.wake_attempts("codex", project_dir, {"session_id": SID2})                                        # rollout written just now
    assert len(act) == 1 and act[0][0].startswith("new session (Codex session") and "resume" not in act[0][1]
    assert kurgu_cli.wake_attempts("codex", project_dir)[0][1][2:4] == ["resume", "--last"]


def test_send_response_names_the_wake_route(srv, cfg):
    c, d = srv
    set_hook(cfg, [sys.executable, "-c", "pass", "wake", "claude", "{dir}"])                                       # looks like a wake hook, starts nothing
    r = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})[1]["wake_route"]
    assert r == {"route": "new", "agent": "claude"}
    agent_notes.record_agent(d, {"CLAUDE_CODE_SESSION_ID": SID})
    r = c.req("POST", "/api/notes/send", {"ids": ["n_2"]})[1]["wake_route"]
    assert r == {"route": "session", "agent": "claude", "session_id": SID}


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_hook_log_is_refused_and_its_target_is_untouched(srv, cfg, tmp_path, capfd):
    c, d = srv
    target = tmp_path / "zshrc"
    target.write_text("export SAFE=1\n")
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    os.symlink(target, os.path.join(d, ".kurgu", "hook.log"))
    set_hook(cfg, [sys.executable, "-c", "print('injected')"])
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    assert st == 200 and res["hook"] == "error" and "symbolic link" in res["hook_error"]            # the send itself still works
    assert res["hook_reason"] == {"code": "symlink", "params": {"path": ".kurgu/hook.log"}}
    assert target.read_text() == "export SAFE=1\n"
    assert "symbolic link" in capfd.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_kurgu_folder_is_not_written_through(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    proj = tmp_path / "p"
    proj.mkdir()
    os.symlink(real, proj / ".kurgu")
    with pytest.raises(OSError, match="symbolic link"):
        agent_notes.open_nofollow(str(proj / ".kurgu" / "inbox.jsonl"), "a")
    os.makedirs(tmp_path / "q" / ".kurgu")
    os.symlink(tmp_path / "evil", tmp_path / "q" / ".kurgu" / "inbox.cursor.tmp")                   # a planted tmp file is never written through: temp names are random
    agent_notes.set_cursor(str(tmp_path / "q"), 3)
    assert not (tmp_path / "evil").exists() and agent_notes.get_cursor(str(tmp_path / "q")) in (0, 3)
    assert open(tmp_path / "q" / ".kurgu" / "inbox.cursor").read() == "3"


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


def test_windows_cmd_shims_refuse_shell_metacharacters_in_the_project_path_only(monkeypatch, capsys):
    monkeypatch.setattr(agent_notes, "IS_WINDOWS", True)
    r = agent_notes.shim_refusal("C:\\npm\\claude.cmd", "C:\\films\\a & calc")
    assert r.code == "shell_chars" and r.params == {"chars": "&", "path": "C:\\films\\a & calc"}
    for ch in '&|<>^%"!':
        assert agent_notes.shim_refusal("C:\\npm\\codex.CMD", "C:\\f" + ch + "g").code == "shell_chars", ch
    assert "Move or rename" in str(agent_notes.shim_refusal("c.bat", "a|b"))
    assert agent_notes.shim_refusal("C:\\npm\\claude.cmd", "C:\\films\\my film (2)") is None            # spaces and parentheses are fine
    assert agent_notes.shim_refusal("C:\\Python\\python.exe", "a & b") is None                            # a real exe is not parsed by cmd.exe
    assert agent_notes.shim_refusal("C:\\Users\\A&B\\AppData\\npm\\claude.cmd", "C:\\films\\ok") is None   # argv[0] (a profile called A&B) is NOT the project's
    monkeypatch.setattr(kurgu_cli.shutil, "which", lambda n: "C:\\npm\\claude.cmd")
    ran = []
    monkeypatch.setattr(kurgu_cli.subprocess, "run", lambda *a, **k: ran.append(a) or None)
    assert kurgu_cli.wake_main(["claude", "C:\\films\\a&b", "3"]) == 1 and not ran
    assert not os.path.exists("C:\\films\\a&b")                                                           # diag does not create a folder for a path that is not there
    assert "Move or rename" in capsys.readouterr().err
    monkeypatch.setattr(agent_notes, "IS_WINDOWS", False)
    assert agent_notes.shim_refusal("claude.cmd", "a&b") is None                                                  # not Windows


def test_claude_wake_never_loads_the_project_folders_own_claude_settings_or_mcp_servers():
    """A downloaded project is untrusted and `claude -p` skips the workspace trust prompt: a project's .claude/settings*.json hooks and .mcp.json servers
    would run on Send. EVERY Claude attempt (fresh, --continue, --bg --resume) restricts the setting sources to the user's own."""
    for session in (None, {"session_id": "11111111-1111-1111-1111-111111111111"}):
        att = kurgu_cli.wake_attempts("claude", "/tmp/p", session)
        assert att
        for _, argv in att:
            assert argv[argv.index("--setting-sources") + 1] == "user", argv
    assert any("--bg" in argv for _, argv in kurgu_cli.wake_attempts("claude", "/tmp/p", {"session_id": "11111111-1111-1111-1111-111111111111"}))


def test_wake_allows_read_only_discovery_and_probe_but_not_ffprobe_and_help_never_wakes():
    tools = kurgu_cli.CLAUDE_TOOLS
    for need in ("Bash(kurgu --help)", "Bash(kurgu help:*)", "Bash(kurgu * --help)", "Bash(kurgu probe:*)", "Bash(kurgu detect:*)", "Bash(kurgu rerender:*)", "mcp__kurgu"):
        assert need in tools, need
    assert "ffprobe" not in tools and "Bash(*" not in tools
    assert kurgu_cli.wake_main(["claude", "/tmp/x", "--help"]) == 0 and kurgu_cli.wake_main(["--help"]) == 0          # `kurgu wake ... --help` prints usage, starts nothing
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "What a woken Claude Code may run" in readme and "kurgu probe" in readme and "ffprobe" in readme.split("What a woken Claude Code may run")[1][:700]
    assert "probe" in kurgu_cli.help_text()


def test_kurgu_probe_reports_a_projects_media_and_nothing_else(project_dir, tmp_path):
    from test_agent_notes import launch
    r = launch("probe", project_dir)
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert j and all("file" in x for x in j) and any(x.get("duration") for x in j)
    for bad in ("http://example.com/x.mp4", "/etc/hosts", "../../etc/hosts"):
        r = launch("probe", project_dir, bad)
        assert r.returncode == 1 and "layer id or a file inside the project" in r.stderr, bad


def test_help_anywhere_on_the_line_only_prints_help_and_runs_nothing(project_dir, tmp_path):
    """The wake allows `kurgu * --help`; `kurgu doctor --fix -- --help` matches that rule, so --help anywhere must turn the command into 'print help'."""
    from test_agent_notes import launch
    assert kurgu_cli._help_only(["doctor", "--fix", "--", "--help"]) == ["doctor", "--help"]
    assert kurgu_cli._help_only(["notes", "add", "d", "--t", "1", "--", "-h"]) == ["notes", "add", "--help"]
    assert kurgu_cli._help_only(["frame", "d", "1"]) == ["frame", "d", "1"]
    assert kurgu_cli._help_only(["--port", "9", "--help"]) == ["--help"]
    marker = tmp_path / "marker"
    for cmd in (["doctor", "--fix"], ["setup-ffmpeg"], ["setup-agent", "--claude", "--wake"], ["wake", "claude", project_dir], ["rerender", project_dir, "S01"],
                ["import", str(tmp_path)], ["probe", project_dir], ["report", project_dir], ["frame", project_dir, "1"], ["notes", "add", project_dir, "--t", "1", "--text", "x"]):
        r = launch(*cmd, "--", "--help", env=dict(os.environ, KURGU_CONFIG=str(tmp_path / "uc"), HOME=str(tmp_path / "home")))
        assert r.returncode == 0 and r.stdout.lstrip().lower().startswith("usage"), (cmd, r.stdout[:200], r.stderr[:200])
    assert not (tmp_path / "uc").exists() and not (tmp_path / "home").exists()           # setup-agent --wake / setup-ffmpeg did not write anything
    assert not os.path.exists(os.path.join(project_dir, ".kurgu", "look")) and not os.path.exists(os.path.join(project_dir, "changes.md"))


def test_kurgu_probe_does_not_read_metadata_of_media_outside_the_project_and_trusted_folders(project_dir, tmp_path):
    from test_agent_notes import launch
    outside = tmp_path / "private.mp4"
    shutil.copy(os.path.join(project_dir, "media", "a.mp4"), outside)
    d = os.path.realpath(project_dir)
    p = json.load(open(os.path.join(d, "project.json")))
    p["layers"].append({"id": "sneaky", "type": "video", "src": str(outside), "start": 0, "end": 1})
    json.dump(p, open(os.path.join(d, "project.json"), "w"))
    env = dict(os.environ, KURGU_CONFIG=str(tmp_path / "uc"))
    r = launch("probe", d, "sneaky", env=env)
    j = json.loads(r.stdout)
    assert j[0].get("skipped") and "duration" not in j[0] and "streams" not in j[0]
    assert all("streams" not in x for x in json.loads(launch("probe", d, env=env).stdout) if x.get("layers") == ["sneaky"])
    (tmp_path / "uc").mkdir()
    json.dump({"render_trusted_roots": [{"root": str(tmp_path), "project": d}]}, open(tmp_path / "uc" / "config.json", "w"))
    j = json.loads(launch("probe", d, "sneaky", env=env).stdout)
    assert j[0]["duration"] > 0                                                                  # a folder the user trusted for this project is fine


def test_kurgu_help_audio_teaches_the_envelope_format_and_mix_json(tmp_path):
    from test_agent_notes import launch
    r = launch("help", "audio", env=dict(os.environ, KURGU_CONFIG=str(tmp_path / "uc")))
    assert r.returncode == 0 and "[[t, dB], ...]" in r.stdout and "[[0, 0], [1, 0], [1.5, -6], [6, -6], [7, 0]]" in r.stdout and "audio/mix.json" in r.stdout and "gain_db" in r.stdout
    r = launch("help", env=dict(os.environ, KURGU_CONFIG=str(tmp_path / "uc")))
    assert "audio" in r.stdout and launch("help", "nosuchtopic").returncode == 0
    assert "kurgu help audio" in kurgu_cli.AGENT_BLOCK and "kurgu help audio" in open(os.path.join(ROOT, "skills", "kurgu", "SKILL.md"), encoding="utf-8").read()
    assert "add" in kurgu_cli.COMMANDS["notes"]
    for name in ("README.md", "README.tr.md", "AGENTS.md", os.path.join("skills", "kurgu", "SKILL.md"), "SPEC.md"):
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        assert "ask | add" in text or "ask|add" in text or "notes add" in text, name


def test_a_note_whose_video_could_not_be_rerendered_must_not_be_closed_says_the_guidance(tmp_path, monkeypatch):
    """Trial: Codex could not start Chrome in its sandbox and closed the note anyway."""
    import kurgu_paths
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    msg = kurgu_paths.chrome_blocked_reason()
    assert "do not mark the note done" in msg and "notes ask" in msg
    skill = open(os.path.join(ROOT, "skills", "kurgu", "SKILL.md"), encoding="utf-8").read()
    assert "do NOT mark the note done" in skill and "audio/mix.json" in skill
    assert "notu kapatma" in kurgu_cli.WAKE_PROMPT and len(kurgu_cli.WAKE_PROMPT) < 1400



def test_claude_wake_never_loads_the_project_s_own_claude_settings_or_mcp_servers():
    """A downloaded project folder is untrusted: claude -p skips the workspace trust prompt, so the project's .claude/settings*.json hooks
    and .mcp.json servers would run on Send. Every Claude wake attempt restricts setting sources to the user's own."""
    for _, argv in kurgu_cli.wake_attempts("claude", "/tmp/p"):
        i = argv.index("--setting-sources")
        assert argv[i + 1] == "user"


def test_codex_writable_roots_skip_what_a_toml_literal_string_cannot_hold(monkeypatch, capsys):
    import render_adaptors
    monkeypatch.setattr(render_adaptors, "agent_roots", lambda d: ["/films/ok", "/films/O'Brien", "/films/a\nb"])
    att = kurgu_cli.wake_attempts("codex", "/tmp/p", None)
    for _, argv in att:
        roots = [a for a in argv if a.startswith("sandbox_workspace_write.writable_roots=")]
        assert roots == ["sandbox_workspace_write.writable_roots=['/films/ok']"], argv
    err = capsys.readouterr().err
    assert "O'Brien" in err and "a\\nb" in err
