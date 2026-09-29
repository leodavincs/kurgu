"""mcp_server.py over real stdio: handshakes, tools, and both write paths (no editor / running editor)."""
import base64
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP = os.path.join(ROOT, "mcp_server.py")
SERVER = os.path.join(ROOT, "server.py")


class Mcp:
    """Minimal newline-delimited JSON-RPC client."""

    def __init__(self, cwd):
        self.p = subprocess.Popen([sys.executable, MCP], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1)
        self.q, self.n, self.notes = queue.Queue(), 0, []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=lambda: [None for _ in self.p.stderr], daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.q.put(json.loads(line))

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def call(self, method, params=None, timeout=90, meta=None):
        self.n += 1
        params = dict(params or {})
        if meta:
            params["_meta"] = dict(params.get("_meta") or {}, **meta)
        self.send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        while True:
            m = self.q.get(timeout=timeout)
            if m.get("id") == self.n:
                return m
            self.notes.append(m)

    def tool(self, name, **args):
        r = self.call("tools/call", {"name": name, "arguments": args})
        assert "result" in r, r
        return r["result"]

    def ok(self, name, **args):
        r = self.tool(name, **args)
        assert not r.get("isError"), r
        return r["content"]

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def first_text(content):
    return next(c["text"] for c in content if c["type"] == "text")


@pytest.fixture()
def mcp(project_dir):
    m = Mcp(project_dir)
    m.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    m.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    yield m
    m.close()


@pytest.fixture()
def editor(project_dir):
    """A real server.py for the project dir, on a free port; request log captured."""
    log = open(os.path.join(project_dir, "server-out.log"), "w")
    p = subprocess.Popen([sys.executable, SERVER, project_dir, "--port", "0", "--no-open"], stdout=log, stderr=subprocess.STDOUT)
    info = os.path.join(project_dir, ".kurgu", "server.json")
    for _ in range(100):
        if os.path.exists(info):
            break
        time.sleep(0.1)
    else:
        p.kill()
        pytest.fail("server.json never appeared")
    yield {"proc": p, "dir": project_dir, "info": info, "log": os.path.join(project_dir, "server-out.log")}
    if p.poll() is None:
        p.terminate()
        p.wait(10)
    log.close()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def project_of(d):
    return json.loads(read(os.path.join(d, "project.json")))


def layer(d, lid):
    return next(l for l in project_of(d)["layers"] if l["id"] == lid)


# ------------------------------------------------------------------ protocol
def test_legacy_handshake_and_tools_list(project_dir):
    m = Mcp(project_dir)
    try:
        r = m.call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        assert r["result"]["protocolVersion"] == "2025-03-26"          # echoed when supported
        assert r["result"]["serverInfo"]["name"] == "kurgu" and "tools" in r["result"]["capabilities"]
        m.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert m.call("ping")["result"] == {}
        names = [t["name"] for t in m.call("tools/list")["result"]["tools"]]
        assert names == ["open_editor", "get_state", "get_project", "edit_project", "render_frame", "render",
                         "get_changes", "get_brief", "update_brief", "get_notes", "update_note", "add_note", "list_fonts", "list_media", "import_file",
                         "transcribe", "make_captions"]
        for t in m.call("tools/list")["result"]["tools"]:
            assert t["inputSchema"]["type"] == "object" and "project_dir" in t["inputSchema"]["properties"] and t["description"]
        assert m.call("nope")["error"]["code"] == -32601
        assert m.call("tools/call", {"name": "nope"})["error"]["code"] == -32602
    finally:
        m.close()


def test_unknown_client_version_gets_our_newest(project_dir):
    m = Mcp(project_dir)
    try:
        r = m.call("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        assert r["result"]["protocolVersion"] == "2025-11-25"
    finally:
        m.close()


def test_modern_era_without_handshake(project_dir):
    m = Mcp(project_dir)
    try:
        meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}
        d = m.call("server/discover", meta=meta)["result"]
        assert d["supportedVersions"] == ["2026-07-28"] and d["resultType"] == "complete"
        assert d["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "kurgu"
        tl = m.call("tools/list", meta=meta)["result"]
        assert tl["resultType"] == "complete" and len(tl["tools"]) == 17
        r = m.call("tools/call", {"name": "get_project", "arguments": {"summary": True}}, meta=meta)["result"]
        assert r["resultType"] == "complete" and "k_buyuk" in r["content"][0]["text"]
        bad = m.call("tools/list", meta={"io.modelcontextprotocol/protocolVersion": "1900-01-01"})["error"]
        assert bad["code"] == -32022 and bad["data"]["supported"] == ["2026-07-28"]
    finally:
        m.close()


# ------------------------------------------------------------------ read tools
def test_get_project_summary_and_full(mcp):
    s = first_text(mcp.ok("get_project", summary=True))
    assert "| id | type |" in s and "k_buyuk" in s and "k_muzik" in s and "version:" in s
    full = json.loads(first_text(mcp.ok("get_project")))
    assert full["project"]["version"] == 2 and len(full["project"]["layers"]) == 11 and len(full["version"]) == 40
    # another dir through the argument
    err = mcp.tool("get_project", project_dir="/nonexistent-kurgu-dir")
    assert err["isError"]


def test_list_fonts_media(mcp):
    f = first_text(mcp.ok("list_fonts"))
    assert "inter-regular" in f
    assert "gill" not in first_text(mcp.ok("list_fonts", query="zzzz-nothing"))
    m = first_text(mcp.ok("list_media"))
    assert "media/a.mp4" in m and "media/muzik.wav" in m


def test_get_changes_empty_then_after_edit(mcp, project_dir):
    assert "No changes.md" in first_text(mcp.ok("get_changes"))
    mcp.ok("edit_project", update=[{"id": "k_buyuk", "set": {"text": "Changed"}}], summary="rename title")
    out = first_text(mcp.ok("get_changes", limit=5))
    assert "[agent] rename title" in out and out.count("## 20") == 1


def test_get_and_update_brief(mcp, project_dir):
    assert "No brief yet" in first_text(mcp.ok("get_brief"))
    mcp.ok("update_brief", text="- 12 s teaser\n- no orange")
    assert first_text(mcp.ok("get_brief")) == "- 12 s teaser\n- no orange"
    assert read(os.path.join(project_dir, "brief.md")).strip() == "- 12 s teaser\n- no orange"
    mcp.ok("update_brief", text="- v2")
    assert "[agent] brief updated" in first_text(mcp.ok("get_changes"))
    assert mcp.tool("update_brief", text="x" * 2001)["isError"]
    assert mcp.tool("update_brief", text="  ")["isError"]


# ------------------------------------------------------------------ edit_project, no editor running
def test_edit_project_offline(mcp, project_dir):
    before = project_of(project_dir)
    untouched = json.dumps(before["layers"][1:], sort_keys=True)
    msg = first_text(mcp.ok("edit_project", update=[{"id": "k_buyuk", "set": {"text": "Yeni Baslik", "shadow": {"blur": 3}}}],
                            project={"global": {"grain": 2}}, summary="new title"))
    assert "Editor not running" in msg
    after = project_of(project_dir)
    l = after["layers"][0]
    assert l["text"] == "Yeni Baslik" and l["shadow"]["blur"] == 3
    # nested objects merge one level: the other shadow keys survive if they existed
    for k, v in before["layers"][0].get("shadow", {}).items():
        if k != "blur":
            assert l["shadow"][k] == v
    assert after["global"]["grain"] == 2 and after["global"].get("vignette") == before["global"].get("vignette")
    assert json.dumps(after["layers"][1:], sort_keys=True) == untouched         # untouched layers identical
    assert "[agent] new title" in read(os.path.join(project_dir, "changes.md"))
    assert len(os.listdir(os.path.join(project_dir, ".kurgu", "backups"))) == 1


def test_edit_project_add_remove_index(mcp, project_dir):
    mcp.ok("edit_project", add=[{"type": "text", "text": "Hi", "start": 1, "end": 3}, {"id": "c1", "type": "color", "start": 0, "end": 2}],
           remove=["k_slate"], index=1)
    ids = [l["id"] for l in project_of(project_dir)["layers"]]
    assert "k_slate" not in ids and ids[2] == "c1" and ids[1].startswith("text_") and ids[0] == "k_buyuk"
    assert "[agent]" in read(os.path.join(project_dir, "changes.md"))      # auto summary


def test_edit_project_null_deletes_layer_field(mcp, project_dir):
    mcp.ok("edit_project", update=[{"id": "k_buyuk", "set": {"letter_spacing": 0.1}}])
    assert layer(project_dir, "k_buyuk")["letter_spacing"] == 0.1
    mcp.ok("edit_project", update=[{"id": "k_buyuk", "set": {"letter_spacing": None}}])
    assert "letter_spacing" not in layer(project_dir, "k_buyuk")


@pytest.mark.parametrize("batch, needle", [
    ({"update": [{"id": "k_buyuk", "set": {"end": 0}}]}, "end (0"),
    ({"update": [{"id": "ghost", "set": {"x": 1}}]}, 'no layer with id "ghost"'),
    ({"update": [{"id": "k_buyuk", "set": {"opacity": 3}}]}, "opacity must be between 0 and 1"),
    ({"update": [{"id": "k_buyuk", "set": {"x": "left"}}]}, "x must be a number"),
    ({"update": [{"id": "k_buyuk", "set": {"id": "other"}}]}, "cannot be changed"),
    ({"add": [{"id": "k_buyuk", "type": "color", "start": 0, "end": 1}]}, "duplicate id"),
    ({"add": [{"type": "blob", "start": 0, "end": 1}]}, "unknown type"),
    ({"add": [{"type": "video", "start": 0, "end": 1}]}, "missing src"),
    ({"remove": ["ghost"]}, 'no layer with id "ghost"'),
    ({"project": {"layers": []}}, "cannot be set here"),
])
def test_invalid_batch_is_rejected_whole(mcp, project_dir, batch, needle):
    path = os.path.join(project_dir, "project.json")
    raw = open(path, "rb").read()
    good = {"update": [{"id": "k_sol", "set": {"x": 5}}]}                 # a valid part must NOT be applied either
    batch = dict(batch, **{"update": good["update"] + batch.get("update", [])})
    r = mcp.tool("edit_project", **batch)
    assert r["isError"] and needle in r["content"][0]["text"] and "nothing written" in r["content"][0]["text"]
    assert open(path, "rb").read() == raw
    assert not os.path.exists(os.path.join(project_dir, "changes.md"))


def test_warnings_do_not_block(mcp, project_dir):
    msg = first_text(mcp.ok("edit_project", add=[{"id": "t9", "type": "text", "font": "no-such-font", "start": 0, "end": 30},
                                                 {"id": "i9", "type": "image", "src": "media/missing.png", "start": 0, "end": 1}]))
    assert "Warnings" in msg and "no-such-font" in msg and "missing.png" in msg and "project duration" in msg
    assert layer(project_dir, "t9")


def test_nothing_to_do(mcp):
    assert mcp.tool("edit_project")["isError"]


# ------------------------------------------------------------------ frames, render, import
def test_render_frame_returns_png_image(mcp):
    content = mcp.ok("render_frame", t=1.0)
    img = next(c for c in content if c["type"] == "image")
    assert img["mimeType"] == "image/png" and base64.b64decode(img["data"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert mcp.tool("render_frame", t=-1)["isError"]


def test_render_draft_with_progress(project_dir):
    m = Mcp(project_dir)
    try:
        m.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        r = m.call("tools/call", {"name": "render", "arguments": {"draft": True}, "_meta": {"progressToken": "tok1"}}, timeout=300)["result"]
        assert not r.get("isError"), r
        txt = r["content"][0]["text"]
        out = txt.split("draft: ", 1)[1].splitlines()[0]
        assert os.path.exists(out) and out.endswith("_draft.mp4") and "duration:" in txt
        prog = [n["params"] for n in m.notes if n.get("method") == "notifications/progress"]
        assert prog and all(p["progressToken"] == "tok1" for p in prog)
        vals = [p["progress"] for p in prog]
        assert vals == sorted(vals) and len(set(vals)) == len(vals)
    finally:
        m.close()


def test_render_error_is_tool_error(mcp, project_dir):
    mcp.ok("edit_project", add=[{"id": "vX", "type": "video", "src": "media/nope.mp4", "start": 0, "end": 1}])
    r = mcp.tool("render", draft=True)
    assert r["isError"] and "file not found" in r["content"][0]["text"]


def test_import_file_offline(mcp, project_dir, tmp_path):
    src = tmp_path / "pic.png"
    src.write_bytes(open(os.path.join(project_dir, "media", "logo.png"), "rb").read())
    t = first_text(mcp.ok("import_file", path=str(src)))
    assert "media/pic.png" in t and os.path.exists(os.path.join(project_dir, "media", "pic.png"))
    assert "media/pic-2.png" in first_text(mcp.ok("import_file", path=str(src)))       # name clash
    assert mcp.tool("import_file", path=str(tmp_path / "x.woff2"))["isError"]
    (tmp_path / "x.txt").write_text("hi")
    assert mcp.tool("import_file", path=str(tmp_path / "x.txt"))["isError"]


# ------------------------------------------------------------------ with a running editor
def test_state_without_editor_says_so(mcp, project_dir):
    st = json.loads(first_text(mcp.ok("get_state")))
    assert st["editor_running"] is False and st["selected"] == []


def test_stale_server_json_means_not_running(mcp, project_dir):
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    with open(os.path.join(project_dir, ".kurgu", "server.json"), "w") as f:
        json.dump({"port": 1, "pid": dead.pid, "started": "2026-01-01T00:00:00"}, f)
    assert "Editor not running" in first_text(mcp.ok("edit_project", update=[{"id": "k_sol", "set": {"x": 7}}]))


def test_edit_goes_through_running_editor(mcp, editor):
    d = editor["dir"]
    port = json.load(open(editor["info"]))["port"]
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request("GET", "/api/version")
    v0 = json.loads(c.getresponse().read())["version"]
    msg = first_text(mcp.ok("edit_project", update=[{"id": "k_buyuk", "set": {"text": "Live"}}], summary="live edit"))
    assert "reloads live" in msg
    c.request("GET", "/api/version")
    assert json.loads(c.getresponse().read())["version"] != v0                       # the editor sees the new version
    assert layer(d, "k_buyuk")["text"] == "Live"
    assert "[agent] live edit" in read(os.path.join(d, "changes.md"))
    assert len(os.listdir(os.path.join(d, ".kurgu", "backups"))) == 1
    time.sleep(0.3)
    assert "PUT /api/project" in read(editor["log"])                                # proof: it went through the HTTP API


def test_conflict_retry_when_file_changes_between_read_and_write(mcp, editor, monkeypatch):
    # a manual edit through the editor's own API right before ours: our base_version comes from a fresh read, so it just works
    d = editor["dir"]
    port = json.load(open(editor["info"]))["port"]
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request("GET", "/api/project")
    got = json.loads(c.getresponse().read())
    got["project"]["layers"][1]["x"] = 111
    c.request("PUT", "/api/project", body=json.dumps({"project": got["project"], "summary": ["moved by hand"], "base_version": got["version"]}))
    assert c.getresponse().status == 200
    mcp.ok("edit_project", update=[{"id": "k_buyuk", "set": {"text": "After hand edit"}}])
    assert layer(d, "k_sol")["x"] == 111 and layer(d, "k_buyuk")["text"] == "After hand edit"
    out = first_text(mcp.ok("get_changes"))
    assert "moved by hand" in out and "[agent]" in out


def test_get_state_with_editor(mcp, editor):
    port = json.load(open(editor["info"]))["port"]
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request("PUT", "/api/state", body=json.dumps({"time": 2.5, "selected": ["k_buyuk", "ghost"], "playing": False, "language": "tr"}))
    assert c.getresponse().status == 200
    st = json.loads(first_text(mcp.ok("get_state")))
    assert st["editor_running"] and st["time"] == 2.5 and st["language"] == "tr" and st["selected"] == ["k_buyuk", "ghost"]
    assert [l["id"] for l in st["selected_layers"]] == ["k_buyuk"] and st["selected_layers"][0]["text"]
    assert st["selected_ids_not_in_project"] == ["ghost"] and "k_buyuk" in st["layers_active_at_playhead"]


def test_frame_and_import_through_editor(mcp, editor, tmp_path):
    img = next(c for c in mcp.ok("render_frame", t=0.5) if c["type"] == "image")
    assert base64.b64decode(img["data"])[:4] == b"\x89PNG"
    src = tmp_path / "up.png"
    src.write_bytes(open(os.path.join(editor["dir"], "media", "logo.png"), "rb").read())
    assert "media/up.png" in first_text(mcp.ok("import_file", path=str(src)))
    time.sleep(0.3)
    log = read(editor["log"])
    assert "GET /api/frame" in log and "POST /api/import" in log


def test_server_json_lifecycle(editor):
    info = json.load(open(editor["info"]))
    assert info["pid"] == editor["proc"].pid and isinstance(info["port"], int) and info["started"]
    editor["proc"].send_signal(signal.SIGTERM)
    editor["proc"].wait(10)
    assert not os.path.exists(editor["info"])                                        # removed on clean exit


def test_open_editor_starts_and_reuses(mcp, project_dir):
    t = first_text(mcp.ok("open_editor", open_browser=False))
    assert "started" in t and "http://127.0.0.1:" in t
    info = json.load(open(os.path.join(project_dir, ".kurgu", "server.json")))
    try:
        t2 = first_text(mcp.ok("open_editor", open_browser=False))
        assert "already running" in t2 and str(info["port"]) in t2
    finally:
        os.kill(info["pid"], signal.SIGTERM)
        for _ in range(50):
            if not os.path.exists(os.path.join(project_dir, ".kurgu", "server.json")):
                break
            time.sleep(0.1)


# ------------------------------------------------------------------ notes tools
def seed_notes(d):
    p = project_of(d)
    p["notes"] = [{"id": "n_a", "t": 5.0, "text": "blur too strong", "created": "2026-09-29T10:00:00"},
                  {"id": "n_b", "t": 1.0, "text": "title late", "created": "2026-09-29T10:01:00", "done": True, "author": "user"}]
    with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
        json.dump(p, f)


def test_get_notes_open_done_all_with_layers(mcp, project_dir):
    seed_notes(project_dir)
    o = json.loads(first_text(mcp.ok("get_notes")))
    assert [n["id"] for n in o["notes"]] == ["n_a"] and o["notes"][0]["status"] == "open" and o["notes"][0]["author"] == "user"
    assert {"k_sol", "k_orta", "k_logo"} <= {l["id"] for l in o["notes"][0]["layers"]}          # what is on screen at 5.0 s
    assert [n["id"] for n in json.loads(first_text(mcp.ok("get_notes", status="done")))["notes"]] == ["n_b"]
    assert [n["id"] for n in json.loads(first_text(mcp.ok("get_notes", status="all")))["notes"]] == ["n_b", "n_a"]   # sorted by time
    assert mcp.tool("get_notes", status="weird")["isError"]


def test_get_notes_empty(mcp):
    assert "No open notes" in first_text(mcp.ok("get_notes"))


def test_update_note_done_with_reply_offline(mcp, project_dir):
    seed_notes(project_dir)
    msg = first_text(mcp.ok("update_note", id="n_a", done=True, reply="Blur reduced 22 to 8"))
    assert "done" in msg
    n = next(x for x in project_of(project_dir)["notes"] if x["id"] == "n_a")
    assert n["done"] is True and n["reply"] == "Blur reduced 22 to 8" and n["resolved_at"] and n["text"] == "blur too strong"
    assert "[agent] note @ 5s done: Blur reduced 22 to 8" in read(os.path.join(project_dir, "changes.md"))
    mcp.ok("update_note", id="n_a", done=False)
    n = next(x for x in project_of(project_dir)["notes"] if x["id"] == "n_a")
    assert n["done"] is False and n["resolved_at"] == "" and n["reply"] == "Blur reduced 22 to 8"


def test_update_note_errors(mcp, project_dir):
    seed_notes(project_dir)
    assert "No note with id" in mcp.tool("update_note", id="nope", done=True)["content"][0]["text"]
    assert mcp.tool("update_note", id="n_a")["isError"]
    assert mcp.tool("update_note", id="n_a", done="yes")["isError"]
    assert mcp.tool("update_note", id="n_a", t=-2)["isError"]


def test_add_note_by_agent(mcp, project_dir):
    msg = first_text(mcp.ok("add_note", t=3.5, text="Is the logo too small here?"))
    assert "agent note" in msg
    n = project_of(project_dir)["notes"][0]
    assert n["author"] == "agent" and n["t"] == 3.5 and n["done"] is False and n["text"] == "Is the logo too small here?"
    assert "k_orta" in n["layer_ids"] and n["id"].startswith("n_") and n["created"]
    assert mcp.tool("add_note", t=1)["isError"] and mcp.tool("add_note", t=-1, text="x")["isError"]


def test_notes_through_running_editor(mcp, editor):
    d = editor["dir"]
    seed_notes(d)
    mcp.ok("update_note", id="n_a", done=True, reply="fixed")
    assert next(x for x in project_of(d)["notes"] if x["id"] == "n_a")["done"]
    time.sleep(0.3)
    assert "PUT /api/project" in read(editor["log"])


def test_get_project_summary_lists_note_status(mcp, project_dir):
    seed_notes(project_dir)
    t = first_text(mcp.ok("get_project", summary=True))
    assert "1 open of 2" in t and "n_a @ 5s [open]" in t and "n_b @ 1s [done]" in t


# ------------------------------------------------------------------ linked clips
def test_linked_pair_shows_in_summary_and_edit_warns_when_out_of_sync(mcp, project_dir):
    base = {"src": "media/a.mp4", "src_in": 0, "start": 1, "end": 4, "link": "lk_x"}
    mcp.ok("edit_project", add=[dict(base, id="pv", type="video", muted=True), dict(base, id="pa", type="audio")])
    assert layer(project_dir, "pv")["link"] == "lk_x" == layer(project_dir, "pa")["link"]
    txt = first_text(mcp.ok("get_project", summary=True))
    assert "link=lk_x" in txt and "Linked clips" in txt and "lk_x: " in txt and "in sync" in txt and "OUT OF SYNC" not in txt
    msg = first_text(mcp.ok("edit_project", update=[{"id": "pa", "set": {"start": 1.4, "end": 4.4}}]))
    assert "linked clips out of sync" in msg and "+0.4s" in msg
    assert "OUT OF SYNC" in first_text(mcp.ok("get_project", summary=True))
    mcp.ok("edit_project", update=[{"id": "pa", "set": {"link": None}}])       # unlink
    assert "link" not in layer(project_dir, "pa") and layer(project_dir, "pv")["link"] == "lk_x"
    assert "Linked clips" not in first_text(mcp.ok("get_project", summary=True))    # a lone link is not a pair
