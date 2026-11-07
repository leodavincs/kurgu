"""Diagnostics: the event log (.kurgu/log.jsonl), `kurgu report`, redaction, refusal reasons end to end, the listener lock."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

import agent_notes
import diag
import server
from test_server import Client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTE_TEXT = "SECRET-NOTE-TEXT make the title bigger"


def events(d):
    return diag.read_events(d, 1000)


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
    c = tmp_path / "userconf"
    c.mkdir()
    monkeypatch.setenv("KURGU_CONFIG", str(c))
    monkeypatch.delenv("KURGU_ON_SEND", raising=False)
    agent_notes._runners.clear()
    return c


def start(d):
    p = json.load(open(os.path.join(d, "project.json"), encoding="utf-8"))
    p["notes"] = [{"id": f"n_{i}", "t": float(i), "text": NOTE_TEXT} for i in range(1, 4)]
    json.dump(p, open(os.path.join(d, "project.json"), "w", encoding="utf-8"))
    s = server.make_server(d, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, Client(s.server_address[1])


@pytest.fixture()
def srv(project_dir, cfg):
    s, c = start(project_dir)
    yield c, project_dir, s
    s.shutdown()
    s.server_close()


# ---------------------------------------------------------------- the log
def test_log_rotates_at_the_size_limit_and_keeps_three_files(project_dir, monkeypatch):
    monkeypatch.setattr(diag, "MAX_BYTES", 3000)
    for i in range(200):
        diag.event(project_dir, "test", f"event number {i} " + "x" * 40)
    k = os.path.join(project_dir, ".kurgu")
    names = sorted(n for n in os.listdir(k) if n.startswith("log.jsonl"))
    assert names == ["log.jsonl", "log.jsonl.1", "log.jsonl.2"]
    assert all(os.path.getsize(os.path.join(k, n)) < 3600 for n in names)
    ev = events(project_dir)
    assert ev[-1]["msg"].startswith("event number 199") and len(ev) < 200 and all({"ts", "kind", "level", "msg"} <= set(e) for e in ev)


def test_log_refuses_a_symlink_and_leaves_the_target_alone(project_dir, tmp_path):
    target = tmp_path / "profile"
    target.write_text("keep\n")
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    os.symlink(target, os.path.join(project_dir, ".kurgu", "log.jsonl"))
    assert diag.event(project_dir, "test", "x") is False and target.read_text() == "keep\n"
    assert events(project_dir) == []


def test_events_for_send_refusal_error_frontend_and_wake_without_note_text(srv, cfg):
    c, d, s = srv
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})                                      # a send
    assert st == 200 and res["listening"] is False
    c.req("POST", "/api/diag/client", {"message": "TypeError: x is undefined", "source": "http://127.0.0.1:1/web/js/main.js?v=3", "line": 12, "col": 4})
    s.app.get_state = lambda: (_ for _ in ()).throw(RuntimeError("boom"))                              # a 5xx
    assert c.req("GET", "/api/state")[0] == 500
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)                                              # a refusal
    if os.path.lexists(os.path.join(d, ".kurgu", "inbox.seq")):
        os.remove(os.path.join(d, ".kurgu", "inbox.seq"))
    os.symlink(os.path.join(d, "project.json"), os.path.join(d, ".kurgu", "inbox.seq"))
    assert c.req("POST", "/api/notes/send", {"ids": ["n_2"]})[0] == 409
    os.remove(os.path.join(d, ".kurgu", "inbox.seq"))
    (cfg / "config.json").write_text(json.dumps({"on_send": [sys.executable, "-c", "import sys; print('a'); print('Not logged in - please run /login', file=sys.stderr); sys.exit(3)"]}))
    c.req("POST", "/api/notes/send", {"ids": ["n_3"]})                                                  # a wake run
    wait_for(lambda: any(e["kind"] == "wake" and e["msg"] == "hook run finished" for e in events(d)))
    ev = events(d)
    kinds = {e["kind"] for e in ev}
    assert {"send", "refusal", "error", "frontend", "wake"} <= kinds
    send = next(e for e in ev if e["kind"] == "send")
    assert send["ctx"]["count"] == 1 and send["ctx"]["listening"] is False and send["ctx"]["wake"] == "none"
    assert next(e for e in ev if e["kind"] == "refusal")["code"] == "symlink"
    err = next(e for e in ev if e["kind"] == "error")
    assert err["ctx"]["route"] == "/api/state" and err["ctx"]["exc"] == "RuntimeError" and err["code"] == "500"
    fe = next(e for e in ev if e["kind"] == "frontend")
    assert fe["ctx"]["source"] == "main.js" and fe["ctx"]["line"] == 12
    fin = next(e for e in ev if e["msg"] == "hook run finished")
    assert fin["ctx"]["exit"] == 3 and fin["ctx"]["cause"] == "not_logged_in" and "tail" not in fin["ctx"] and "login" not in json.dumps(fin).lower() and next(e for e in ev if e["msg"] == "hook run started")["ctx"]["exe"]
    assert NOTE_TEXT not in open(os.path.join(d, ".kurgu", "log.jsonl"), encoding="utf-8").read()
    assert NOTE_TEXT not in diag.build_report(d)


def test_server_start_and_stop_are_logged(project_dir):
    port = __import__("cdp").free_port()
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), project_dir, "--port", str(port), "--no-open"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(lambda: any(e["msg"] == "server started" for e in events(project_dir)), __import__("cdp").STARTUP_WAIT)   # 60 s on CI: the macOS runners are slow
        assert next(e for e in events(project_dir) if e["msg"] == "server started")["ctx"]["version"]
    finally:
        p.terminate()
        p.wait(10)
    if os.name != "nt":                         # Windows terminate() is TerminateProcess: no shutdown code runs, so nothing can be logged
        assert any(e["msg"] == "server stopped" for e in events(project_dir))


def test_client_error_endpoint_is_rate_limited(srv):
    c, d, s = srv
    codes = [c.req("POST", "/api/diag/client", {"message": f"e{i}", "source": "a.js"})[0] for i in range(13)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429] * 3
    assert len([e for e in events(d) if e["kind"] == "frontend"]) == 10


# ---------------------------------------------------------------- redaction + report
def test_redaction_hides_home_emails_and_tokens_but_keeps_names():
    home = os.path.expanduser("~")
    txt = (f"{home}/Videos/my_project_final.mp4 mail me@example.com Authorization: Bearer abc.DEF-123_xyz sk-ant-api03-ABCDEFGH12345678 "
           "hash 0123456789abcdef0123456789abcdef01234567 key aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5h token=hunter2 api_key: 'zz' path /tmp/x/clip-01.mov")
    out = diag.redact(txt)
    assert "~/Videos/my_project_final.mp4" in out and home not in out and "clip-01.mov" in out
    for leaked in ("me@example.com", "abc.DEF-123", "ABCDEFGH12345678", "0123456789abcdef0123456789abcdef", "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5h", "hunter2", "'zz'"):
        assert leaked not in out, leaked
    assert "<email>" in out and "<redacted>" in out


def test_report_has_the_facts_the_log_and_the_hook_log_and_is_redacted(srv, cfg):
    c, d, s = srv
    (cfg / "config.json").write_text(json.dumps({"on_send": ["/secret/path/kurgu", "wake", "claude", "{dir}", "{seq}"]}))
    diag.event(d, "test", f"contact me@example.com from {os.path.expanduser('~')}")
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    open(os.path.join(d, ".kurgu", "hook.log"), "w").write("\n".join(f"[2026-01-01T10:00:00] run (send #{i}, 1 notes): kurgu wake claude /p {i}" for i in range(80)) + "\n| agent says hi\n")
    text = diag.build_report(d)
    for part in ("Kurgu report", "kurgu:", "python:", "ffmpeg:", "chrome:", "Agent setup", "wake:      configured (claude)", "listening: no", "Doctor", "Last events", "hook.log (last 50 lines, agent output left out)"):
        assert part in text, part
    assert "/secret/path" not in text and "me@example.com" not in text and "<email>" in text and "(send #79," in text and "(send #20," not in text and "agent says hi" not in text
    assert c.req("GET", "/api/report")[1]["text"].startswith("Kurgu report")


def test_kurgu_report_cli_prints_or_writes_a_file(project_dir):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "kurgu_cli.py"), "report", project_dir], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and r.stdout.startswith("Kurgu report")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "kurgu_cli.py"), "report", project_dir, "-o"], capture_output=True, text=True, encoding="utf-8")
    path = r.stdout.strip()
    assert r.returncode == 0 and os.path.basename(path).startswith("report-") and open(path, encoding="utf-8").read().startswith("Kurgu report")


# ---------------------------------------------------------------- refusal reasons end to end (server side)
def test_reason_symlinked_kurgu_folder_or_inbox_refuses_the_send_as_an_error(srv, tmp_path):
    c, d, s = srv
    os.makedirs(tmp_path / "elsewhere")
    shutil.rmtree(os.path.join(d, ".kurgu"), ignore_errors=True)
    os.symlink(tmp_path / "elsewhere", os.path.join(d, ".kurgu"))
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    assert st == 409 and res["reason"] == "symlink" and res["params"] == {"path": ".kurgu"}
    assert c.req("GET", "/api/notes/info")[1]["problem"] == {"code": "symlink", "params": {"path": ".kurgu"}}
    assert not any(n.get("sent") for n in c.req("GET", "/api/project")[1]["project"]["notes"]) if "project" in c.req("GET", "/api/project")[1] else True
    assert os.listdir(tmp_path / "elsewhere") == []                                                        # nothing was written through the link


def test_reason_symlinked_inbox_refuses_and_hook_log_is_reported(srv, tmp_path):
    c, d, s = srv
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    os.symlink(tmp_path / "target", os.path.join(d, ".kurgu", "inbox.jsonl"))
    st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    assert st == 409 and res["params"] == {"path": ".kurgu/inbox.jsonl"} and not (tmp_path / "target").exists()
    os.remove(os.path.join(d, ".kurgu", "inbox.jsonl"))
    os.symlink(tmp_path / "target", os.path.join(d, ".kurgu", "hook.log"))
    assert c.req("GET", "/api/notes/info")[1]["problem"]["params"] == {"path": ".kurgu/hook.log"}


def test_reason_windows_special_characters_in_the_project_path(tmp_path, cfg, monkeypatch, project_dir):
    odd = tmp_path / "films & more"
    shutil.copytree(project_dir, odd)
    s, c = start(str(odd))
    try:
        monkeypatch.setattr(agent_notes, "IS_WINDOWS", True)
        monkeypatch.setattr(agent_notes, "resolve_exe", lambda n: "C:\\Users\\A&B\\npm\\claude.cmd")       # the launcher path itself has an & : fine
        (cfg / "config.json").write_text(json.dumps({"on_send": ["claude", "wake", "claude", "{dir}", "{seq}"]}))
        info = c.req("GET", "/api/notes/info")[1]
        assert info["problem"]["code"] == "shell_chars" and info["problem"]["params"]["chars"] == "&"
        st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
        assert st == 200 and res["hook"] == "error" and res["hook_reason"]["code"] == "shell_chars"
        assert res["hook_reason"]["params"]["path"].endswith("films & more") and res["sent"] == 1
        assert any(e["kind"] == "refusal" and e["code"] == "shell_chars" for e in events(str(odd)))
        ok = tmp_path / "films"
        shutil.copytree(project_dir, ok)
        s2, c2 = start(str(ok))
        try:
            assert c2.req("POST", "/api/notes/send", {"ids": ["n_1"]})[1]["hook_reason"] is None          # no special character: not refused
        finally:
            s2.shutdown()
            s2.server_close()
    finally:
        s.shutdown()
        s.server_close()


# ---------------------------------------------------------------- listener lock
BEATER = ("import sys, time, agent_notes\nd = sys.argv[1]\nfor i in range(40):\n    agent_notes._beat(d, time.time(), None)\n"
          "print('done', flush=True)\nsys.stdin.readline()\n")


def test_four_concurrent_beaters_lose_no_entry(project_dir):
    procs = [subprocess.Popen([sys.executable, "-c", BEATER, project_dir], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) for _ in range(4)]
    try:
        for p in procs:
            assert p.stdout.readline().strip() == "done"
        got = agent_notes._read_listeners(project_dir)
        assert sorted(got) == sorted(str(p.pid) for p in procs)
    finally:
        for p in procs:
            p.stdin.close()
            p.wait(10)


def test_beat_prunes_dead_pids(project_dir):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    agent_notes._write_listeners(project_dir, {"x": {"pid": dead.pid, "since": 0, "beat": time.time()}, "y": {"pid": os.getpid(), "since": 0, "beat": time.time()}})
    agent_notes._beat(project_dir, time.time(), None)
    assert sorted(agent_notes._read_listeners(project_dir)) == sorted(["y", str(os.getpid())])


# ---------------------------------------------------------------- agent output stays out of the report unless asked for
def test_note_text_in_agent_output_is_only_in_the_opt_in_report(srv, cfg):
    c, d, s = srv
    code = f"print({NOTE_TEXT!r}); print('Permission denied')"
    (cfg / "config.json").write_text(json.dumps({"on_send": [sys.executable, "-c", code, "--token=SUPERSECRETVALUE"]}))
    c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
    wait_for(lambda: any(e["msg"] == "hook run finished" for e in events(d)))
    default, opt_in = diag.build_report(d), diag.build_report(d, include_agent_output=True)
    assert NOTE_TEXT not in default and "| " + NOTE_TEXT not in default and NOTE_TEXT in opt_in
    assert NOTE_TEXT not in open(os.path.join(d, ".kurgu", "log.jsonl"), encoding="utf-8").read()
    fin = next(e for e in events(d) if e["msg"] == "hook run finished")
    assert fin["ctx"] == {"exit": 0, "cause": "permission_denied"}                                  # a code from a fixed list, never the lines
    log = open(os.path.join(d, ".kurgu", "hook.log"), encoding="utf-8").read()
    assert "custom on_send, 3 arguments" in log and "SUPERSECRETVALUE" not in log and NOTE_TEXT in log          # custom argv is not logged; the agent's lines are (prefixed)
    assert "SUPERSECRETVALUE" not in default and "SUPERSECRETVALUE" not in opt_in
    assert c.req("GET", "/api/report?agent_output=1")[1]["text"].count(NOTE_TEXT) == 1 and NOTE_TEXT not in c.req("GET", "/api/report")[1]["text"]
    r = subprocess.run([sys.executable, os.path.join(ROOT, "kurgu_cli.py"), "report", d, "--include-agent-output"], capture_output=True, text=True, encoding="utf-8")
    assert NOTE_TEXT in r.stdout


def test_default_wake_argv_is_logged_in_full_custom_only_as_a_count():
    shown = agent_notes._Runner._shown
    assert shown(["/x/kurgu", "wake", "claude", "{dir}", "{seq}"], ["/x/kurgu", "wake", "claude", "/p", "3"]) == "/x/kurgu wake claude /p 3"
    assert shown(["/x/tool", "--key", "abc"], ["/x/tool", "--key", "abc"]) == "tool (custom on_send, 2 arguments)"


def test_causes_come_from_a_fixed_list():
    assert diag.cause_from(["x", "Error: command not found: claude"]) == "command_not_found"
    assert diag.cause_from(["PolicyProblem: blocked"]) == "policy_problem" and diag.cause_from(["just some text with a note"]) == ""


def test_home_redaction_is_case_insensitive_for_windows_and_generic_for_other_users():
    out = diag.redact("c:\\users\\leo\\Videos\\a.mp4 and C:/Users/LEO/b.mp4", home="C:\\Users\\Leo")
    assert out == "~\\Videos\\a.mp4 and ~/b.mp4"
    out = diag.redact("/Users/other/x /home/bob/y D:\\Users\\Zed\\z /srv/keep/me", home="/nobody")
    assert "other" not in out and "bob" not in out and "Zed" not in out and out.count("<user-home>") == 3 and "/srv/keep/me" in out


def test_reading_through_a_symlinked_kurgu_folder_is_refused(project_dir, tmp_path):
    other = tmp_path / "other"
    (other).mkdir()
    (other / "log.jsonl").write_text(json.dumps({"ts": "t", "kind": "x", "level": "info", "msg": "LEAK"}) + "\n")
    (other / "hook.log").write_text("LEAK\n")
    shutil.rmtree(os.path.join(project_dir, ".kurgu"), ignore_errors=True)
    os.symlink(other, os.path.join(project_dir, ".kurgu"))
    assert diag.read_events(project_dir) == []
    text = diag.build_report(project_dir, include_agent_output=True)
    assert "refused: .kurgu is a symbolic link" in text and "refused: hook.log is a symbolic link" in text and "LEAK" not in text


def test_legacy_unprefixed_agent_output_in_hook_log_is_left_out_of_the_default_report(project_dir):
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    legacy = 'Fixed note: make the logo bigger'
    open(os.path.join(project_dir, ".kurgu", "hook.log"), "w", encoding="utf-8").write(
        "[2026-01-01T10:00:00] run (send #1, 1 notes): /x/kurgu wake claude /p 1\n"
        + json.dumps({"type": "result", "result": legacy}) + "\n" + legacy + "\n[not a timestamp] " + legacy + "\n"
        + "  finished, exit code 0\n| " + legacy + "\n")
    default = diag.build_report(project_dir)
    assert legacy not in default and "run (send #1, 1 notes)" in default and "  finished, exit code 0" in default
    assert legacy in diag.build_report(project_dir, include_agent_output=True)


def test_a_project_shipped_listening_json_with_junk_beats_cannot_crash_the_heartbeat(project_dir):
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    junk = {"1": {"pid": 1, "beat": "x"}, "2": {"pid": os.getpid(), "beat": None}, "3": {"pid": os.getpid(), "beat": float("inf")}, "4": "text", "5": {"pid": os.getpid(), "beat": [1]}}
    open(os.path.join(project_dir, ".kurgu", "listening.json"), "w").write(json.dumps(junk).replace("Infinity", "1e999"))
    assert agent_notes.is_listening(project_dir) is False
    agent_notes._beat(project_dir, time.time(), None)                                                  # does not raise
    assert list(agent_notes._read_listeners(project_dir)) == [str(os.getpid())]
    assert agent_notes.is_listening(project_dir) is True


def test_legacy_output_shaped_like_a_kurgu_line_still_stays_out(project_dir):
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    note = "my note: make the title red"
    open(os.path.join(project_dir, ".kurgu", "hook.log"), "w", encoding="utf-8").write(
        f"[2026-11-07T10:00:01] {note}\n  could not start: {note}\n  finished, exit code 0 {note}\n"
        "  could not start: [Errno 2] No such file or directory: 'claude'\n")
    default = diag.build_report(project_dir)
    assert note not in default and "could not start: [Errno 2]" in default


def test_huge_numbers_and_deep_nesting_in_a_shipped_listening_json_cannot_crash_the_heartbeat(project_dir):
    k = os.path.join(project_dir, ".kurgu")
    os.makedirs(k, exist_ok=True)
    p = os.path.join(k, "listening.json")
    for body in ('{"1":{"pid":%d,"beat":1%s}}' % (os.getpid(), "0" * 400), '{"1":{"pid":%d,"beat":-1%s}}' % (os.getpid(), "0" * 400), "[" * 100000 + "]" * 100000):
        open(p, "w").write(body)
        assert agent_notes.is_listening(project_dir) is False
        agent_notes._beat(project_dir, time.time(), None)                                              # does not raise
        assert agent_notes.is_listening(project_dir) is True


def test_default_report_rebuilds_kurgu_lines_from_numbers_so_free_text_after_a_kurgu_prefix_is_dropped(project_dir):
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    note = "SECRETNOTE make title red"
    open(os.path.join(project_dir, ".kurgu", "hook.log"), "w", encoding="utf-8").write(
        f"[2026-11-07T10:00:01] run (send #1, 2 notes): {note}\n"
        f"  could not start: [Errno 2] {note}\n"
        f"[٢٠٢٦-11-07T10:00:01] run (send #1, 2 notes): {note}\n"
        "[2026-11-07T10:00:02] run (send #3, 1 notes): /opt/My Tools/kurgu wake codex /p/My Film 3\n"
        "[2026-11-07T10:00:03] run (send #4, 1 notes): my-hook (custom on_send, 2 arguments)\n"
        "  stopped: still running after 600 s\n")
    default = diag.build_report(project_dir)
    assert "SECRETNOTE" not in default and "My Tools" not in default and "My Film" not in default
    for want in ("[2026-11-07T10:00:01] run (send #1, 2 notes): (command not shown)", "  could not start: [Errno 2]",
                 "run (send #3, 1 notes): kurgu wake codex", "run (send #4, 1 notes): custom on_send", "  stopped: still running after 600 s"):
        assert want in default
    assert "SECRETNOTE" in diag.build_report(project_dir, include_agent_output=True)


def test_browser_and_server_error_messages_keep_only_the_error_class_by_default(project_dir):
    diag.event(project_dir, "frontend", "TypeError: note 'SECRETNOTE' is undefined", "error")
    diag.event(project_dir, "error", "KeyError: 'SECRETNOTE'", "error")
    diag.event(project_dir, "frontend", "SECRETNOTE without a class", "error")
    default = diag.build_report(project_dir)
    assert "SECRETNOTE" not in default and '"TypeError"' in default and '"KeyError"' in default
    assert "SECRETNOTE" in diag.build_report(project_dir, include_agent_output=True)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_in_place_of_project_side_files_cannot_hang_the_heartbeat_or_the_report(project_dir):
    k = os.path.join(project_dir, ".kurgu")
    os.makedirs(k, exist_ok=True)
    for name in ("listening.json", "listening.lock", "hook.log", "log.jsonl"):
        os.mkfifo(os.path.join(k, name))
    done = []
    def run():
        agent_notes.is_listening(project_dir)
        agent_notes._beat(project_dir, time.time(), None)
        diag.event(project_dir, "test", "x")
        diag.build_report(project_dir)
        done.append(True)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(20)
    assert done == [True]


# ---------------------------------------------------------------- shipped symlinks and ids (a downloaded project is untrusted)
@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_changes_md_brief_md_and_note_signatures_never_write_through_a_shipped_symlink(srv, tmp_path):
    c, d, _ = srv
    profile = tmp_path / "zshrc"
    profile.write_text("export SAFE=1\n")
    precious = tmp_path / "precious.txt"
    precious.write_text("keep\n")
    os.symlink(profile, os.path.join(d, "changes.md"))
    os.symlink(precious, os.path.join(d, "brief.md"))
    st, data = c.req("GET", "/api/project")
    proj, v0 = data["project"], data["version"]
    st, res = c.req("PUT", "/api/project", {"project": proj, "summary": ['"a"; touch /tmp/x; "b"\nsecond line: x 1 -> 2'], "base_version": v0})
    c.req("PUT", "/api/brief", {"text": "new brief"})
    c.req("POST", "/api/notes/send", {})
    assert profile.read_text() == "export SAFE=1\n" and precious.read_text() == "keep\n"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_a_symlinked_notes_folder_is_refused_and_signatures_stay_inside(srv, tmp_path):
    c, d, _ = srv
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    os.symlink(elsewhere, os.path.join(d, ".kurgu", "notes"))
    c.req("POST", "/api/notes/send", {})
    assert list(elsewhere.iterdir()) == []


def test_a_note_id_cannot_become_a_path():
    for bad in ("../../x/photo", "a/b", "..", "", "a" * 65, None, 5):
        with pytest.raises(ValueError):
            agent_notes.safe_nid(bad)
    assert agent_notes.safe_nid("n_1a2B-3") == "n_1a2B-3"


def test_change_summary_lines_stay_one_line_each(srv):
    c, d, _ = srv
    st, data = c.req("GET", "/api/project")
    c.req("PUT", "/api/project", {"project": data["project"], "summary": ['"a\nb\r\nc": x 1 -> 2'], "base_version": data["version"]})
    text = open(os.path.join(d, "changes.md"), encoding="utf-8").read()
    assert '"a b c": x 1 -> 2' in text and "\nb\n" not in text


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_old_style_temp_names_planted_as_symlinks_are_never_written_through(srv, tmp_path):
    """Frame / AI-thread / project temp files used fixed or computable names; a project could plant a symlink under them."""
    import hashlib
    c, d, _ = srv
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n")
    k = os.path.join(d, ".kurgu")
    os.makedirs(os.path.join(k, "frames"), exist_ok=True)
    os.symlink(victim, os.path.join(k, "ai-threads.json.tmp"))
    # every computable old-style frame part name for t=0 points at the victim
    st, data = c.req("GET", "/api/version")
    for v in {data["version"], hashlib.sha1(open(os.path.join(d, "project.json"), "rb").read()).hexdigest()}:
        key = hashlib.sha1(f"{v}|0.000|".encode()).hexdigest()
        try:
            os.symlink(victim, os.path.join(k, "frames", key + ".png.part.png"))
        except FileExistsError:
            pass
    c.req("GET", "/api/frame?t=0")
    c.req("POST", "/api/ai/threads", {"scope": {"time": 1}})
    assert victim.read_text() == "keep\n"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_a_symlinked_kurgu_folder_or_backups_folder_is_refused(tmp_path, project_dir, cfg):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(os.path.join(project_dir, ".kurgu"), ignore_errors=True)
    os.symlink(elsewhere, os.path.join(project_dir, ".kurgu"))
    with pytest.raises(agent_notes.KurguRefusal):
        server.make_server(project_dir, port=0)
    assert list(elsewhere.iterdir()) == []
    os.remove(os.path.join(project_dir, ".kurgu"))
    s, c = start(project_dir)
    try:
        os.symlink(elsewhere, os.path.join(project_dir, ".kurgu", "backups"))
        st, data = c.req("GET", "/api/project")
        c.req("PUT", "/api/project", {"project": data["project"], "summary": ["x"], "base_version": data["version"]})
        assert list(elsewhere.iterdir()) == []
    finally:
        s.shutdown()
        s.server_close()
