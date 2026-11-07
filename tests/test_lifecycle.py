"""Server lifecycle: stops itself when no editor tab is around, one server per project, stale server.json, clean exit."""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

import pytest

import agent_notes
import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import cdp  # noqa: E402  (free_port / wait_http)


def start(project_dir, *args, env=None, wait=True):
    e = dict(os.environ, KURGU_CACHE=os.path.join(project_dir, "..", "cache"), **(env or {}))
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), project_dir, "--no-open", *args], env=e, cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if wait:
        info = os.path.join(project_dir, ".kurgu", "server.json")
        for _ in range(600):
            if os.path.exists(info) or p.poll() is not None:
                break
            time.sleep(0.05)
    return p


def port_of(project_dir):
    return json.load(open(os.path.join(project_dir, ".kurgu", "server.json")))["port"]


def get(port, path, tab=False):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Sec-Fetch-Site": "same-origin"} if tab else {})
    return urllib.request.urlopen(req, timeout=5).read()


def stop(p):
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()


def test_idle_server_stops_itself_and_notes_still_work_without_it(project_dir):
    p = start(project_dir, "--port", str(cdp.free_port()), env={"KURGU_IDLE_MIN": "0.03"})       # 1.8 s
    try:
        assert p.wait(30) == 0
        out = p.stdout.read()
        assert "no editor open" in out and not os.path.exists(os.path.join(project_dir, ".kurgu", "server.json"))
    finally:
        stop(p)
    assert agent_notes.wait_for_send(project_dir, timeout=0) is None                               # the agent loop is file based: no server needed
    r = subprocess.run([sys.executable, os.path.join(ROOT, "agent_notes.py"), "list", project_dir, "--all"], capture_output=True, text=True)
    assert r.returncode == 0


def test_an_open_tab_keeps_the_server_alive_and_agent_requests_do_not(project_dir):
    p = start(project_dir, "--port", str(cdp.free_port()), env={"KURGU_IDLE_MIN": "0.05"})        # 3 s
    try:
        port = port_of(project_dir)
        t0 = time.time()
        while time.time() - t0 < 6:                                                                 # a tab (fetch metadata) polling: alive well past 3 s
            get(port, "/api/version", tab=True)
            time.sleep(0.4)
        assert p.poll() is None
        t0 = time.time()
        while p.poll() is None and time.time() - t0 < 20:                                           # curl / MCP style requests are not a heartbeat
            try:
                get(port, "/api/version")
            except OSError:
                break
            time.sleep(0.4)
        assert p.wait(20) == 0
    finally:
        stop(p)


def test_idle_settings_and_busy_work_block_the_stop(project_dir, monkeypatch):
    monkeypatch.delenv("KURGU_IDLE_MIN", raising=False)
    app = server.App(project_dir)
    assert app.idle_seconds() == 600
    os.makedirs(app.kurgu, exist_ok=True)
    json.dump({"idle_minutes": 2}, open(os.path.join(app.kurgu, "config.json"), "w"))
    assert app.idle_seconds() == 120
    monkeypatch.setenv("KURGU_IDLE_MIN", "0")
    assert app.idle_seconds() == 0                                                                   # 0 = never
    assert not app.busy()
    app.render["status"] = "running"
    assert app.busy()                                                                                # a running render keeps it up
    app.render["status"] = "idle"
    app.proxy_state["k"] = "running"
    assert app.busy()
    app.proxy_state["k"] = "ready"
    assert not app.busy()


def test_a_second_start_reuses_the_running_server(project_dir):
    port = cdp.free_port()
    a = start(project_dir, "--port", str(port))
    try:
        assert port_of(project_dir) == port
        b = start(project_dir, wait=False)
        out, _err = b.communicate(timeout=20)
        assert b.returncode == 0 and f"http://127.0.0.1:{port}/" in out and "already running" in out
        c = start(project_dir, "--port", str(port), wait=False)                                      # the same port named explicitly is fine
        assert c.wait(20) == 0
        d = start(project_dir, "--port", str(cdp.free_port()), wait=False)                           # another port for the same project is refused
        out, err = d.communicate(timeout=20)
        assert d.returncode == 1 and "already running" in err and str(port) in err
        assert a.poll() is None and json.loads(get(port, "/api/version"))["dir"] == os.path.realpath(project_dir)
    finally:
        stop(a)


def test_a_stale_server_json_is_cleaned_up_and_does_not_block_a_start(project_dir):
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    info = os.path.join(project_dir, ".kurgu", "server.json")
    json.dump({"pid": gone.pid, "port": 59999}, open(info, "w"))                                      # dead pid
    assert server.running_server(project_dir) is None and not os.path.exists(info)
    json.dump({"pid": os.getpid(), "port": cdp.free_port()}, open(info, "w"))                         # a living pid, but nothing answers there
    assert server.running_server(project_dir) is None and not os.path.exists(info)
    json.dump({"pid": gone.pid, "port": 59999}, open(info, "w"))
    p = start(project_dir, "--port", str(cdp.free_port()))
    try:
        for _ in range(600):                                                                          # up to 30 s: a loaded machine starts slowly
            try:
                if json.load(open(info))["pid"] == p.pid:
                    break
            except (OSError, ValueError):
                pass                                                                                  # the file is replaced atomically; a read can land in between
            time.sleep(0.05)
        assert json.load(open(info))["pid"] == p.pid                                                  # started normally, file rewritten
    finally:
        stop(p)
    assert not os.path.exists(info)                                                                   # and removed on exit


def test_sigterm_ends_children_and_removes_temp_files(project_dir):
    os.makedirs(os.path.join(project_dir, ".kurgu", "proxies"), exist_ok=True)
    part = os.path.join(project_dir, ".kurgu", "proxies", "x.part.mp4")
    open(part, "w").write("half a proxy")
    p = start(project_dir, "--port", str(cdp.free_port()))
    try:
        assert p.poll() is None
        p.send_signal(signal.SIGTERM)
        assert p.wait(20) == 0
    finally:
        stop(p)
    assert not os.path.exists(part) and not os.path.exists(os.path.join(project_dir, ".kurgu", "server.json"))


def test_stop_children_ends_tracked_processes():
    p = server.popen([sys.executable, "-c", "import time; time.sleep(60)"])
    q = server.popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL)
    assert p.poll() is None and q.poll() is None
    t0 = time.time()
    server.stop_children()
    assert p.poll() is not None and q.poll() is not None and time.time() - t0 < 10
