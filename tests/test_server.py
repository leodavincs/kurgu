import http.client
import json
import os
import shutil
import threading
import time

import pytest

import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Client:
    def __init__(self, port):
        self.port = port

    def req(self, method, path, body=None, headers=None, raw=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = dict(headers or {})
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        if raw:
            return r, data
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data


@pytest.fixture()
def srv(project_dir):
    s = server.make_server(project_dir, port=0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield Client(s.server_address[1]), project_dir
    s.shutdown()
    s.server_close()


def test_project_get_put_and_409(srv):
    c, d = srv
    st, data = c.req("GET", "/api/project")
    assert st == 200 and data["project"]["version"] == 2 and len(data["version"]) == 40
    assert data["project"]["layers"][0]["id"] == "k_buyuk"
    v0 = data["version"]
    st, v = c.req("GET", "/api/version")
    assert v["version"] == v0 and v["app"] == "kurgu" and v["dir"] == os.path.realpath(d)   # app + dir let a second `kurgu <project>` recognise the running server

    proj = data["project"]
    proj["layers"][0]["x"] = 1000
    st, res = c.req("PUT", "/api/project", {"project": proj, "summary": ['"ORBIT" (text): x 960 -> 1000'], "base_version": v0})
    assert st == 200 and res["version"] != v0
    with open(os.path.join(d, "project.json"), encoding="utf-8") as f:
        assert json.load(f)["layers"][0]["x"] == 1000
    changes = open(os.path.join(d, "changes.md"), encoding="utf-8").read()
    assert changes.startswith("# Changes") and "\n## 20" in changes and '"ORBIT" (text): x 960 -> 1000' in changes
    assert len(os.listdir(os.path.join(d, ".kurgu", "backups"))) == 1

    # stale base -> 409 with the current project
    st, res = c.req("PUT", "/api/project", {"project": proj, "summary": [], "base_version": v0})
    assert st == 409 and res["version"] == c.req("GET", "/api/version")[1]["version"]
    assert res["project"]["layers"][0]["x"] == 1000 and "error" in res


def test_put_rejects_bad_body(srv):
    c, _ = srv
    assert c.req("PUT", "/api/project", {"project": 5})[0] == 400
    assert c.req("PUT", "/api/project", b"{nope")[0] == 400


def test_external_edit_changes_version(srv):
    c, d = srv
    v0 = c.req("GET", "/api/version")[1]["version"]
    p = os.path.join(d, "project.json")
    data = json.load(open(p, encoding="utf-8"))
    data["name"] = "edited outside"
    json.dump(data, open(p, "w", encoding="utf-8"))
    assert c.req("GET", "/api/version")[1]["version"] != v0


def test_media_range_206_and_permissions(srv, tmp_path):
    c, d = srv
    size = os.path.getsize(os.path.join(d, "media", "a.mp4"))
    r, data = c.req("GET", "/media?path=media/a.mp4", headers={"Range": "bytes=10-19"}, raw=True)
    assert r.status == 206 and len(data) == 10
    assert r.getheader("Content-Range") == f"bytes 10-19/{size}" and r.getheader("Accept-Ranges") == "bytes"
    r, data = c.req("GET", "/media?path=media/a.mp4", headers={"Range": "bytes=-5"}, raw=True)
    assert r.status == 206 and len(data) == 5
    r, data = c.req("GET", "/media?path=media/a.mp4", raw=True)
    assert r.status == 200 and len(data) == size
    assert c.req("GET", "/media?path=media/a.mp4", headers={"Range": f"bytes={size + 5}-"})[0] == 416
    assert c.req("GET", "/media?path=/etc/hosts")[0] == 403
    assert c.req("GET", "/media?path=../../../etc/hosts")[0] == 403
    assert c.req("GET", "/media?path=media/none.mp4")[0] == 404


def test_media_outside_dir_allowed_when_referenced(srv, tmp_path):
    c, d = srv
    ext = tmp_path / "outside.wav"
    shutil.copy(os.path.join(d, "media", "muzik.wav"), ext)
    assert c.req("GET", f"/media?path={ext}")[0] == 403
    p = json.load(open(os.path.join(d, "project.json"), encoding="utf-8"))
    p["layers"].append({"id": "x", "type": "audio", "src": str(ext), "start": 0, "end": 1})
    json.dump(p, open(os.path.join(d, "project.json"), "w", encoding="utf-8"))
    assert c.req("GET", f"/media?path={ext}")[0] == 200


def test_host_header_protection(srv):
    c, _ = srv
    r, data = c.req("GET", "/api/version", headers={"Host": "evil.example"}, raw=True)
    assert r.status == 403


def test_state_roundtrip(srv):
    c, d = srv
    st, _ = c.req("PUT", "/api/state", {"time": 12.3, "selected": ["k_buyuk"], "playing": False, "language": "tr"})
    assert st == 200
    state = json.load(open(os.path.join(d, ".kurgu", "state.json"), encoding="utf-8"))
    assert state["time"] == 12.3 and state["selected"] == ["k_buyuk"] and state["language"] == "tr"
    assert state["playing"] is False and "T" in state["updated"]
    assert c.req("GET", "/api/state")[1]["selected"] == ["k_buyuk"]


def test_files_listing(srv):
    c, d = srv
    st, files = c.req("GET", "/api/files")
    kinds = {f["path"]: f["kind"] for f in files}
    assert kinds["media/a.mp4"] == "video" and kinds["media/logo.png"] == "image" and kinds["media/muzik.wav"] == "audio"
    assert all(set(f) == {"path", "kind", "size", "mtime"} for f in files)


def test_fonts_and_font_file(srv):
    c, _ = srv
    st, fl = c.req("GET", "/api/fonts")
    assert st == 200
    by = {f["id"]: f for f in fl}
    assert by["inter-bold"]["source"] == "bundled" and by["inter-bold"]["url"] == "/font-file?id=inter-bold"
    assert {"id", "family", "style", "weight", "italic", "source", "url"} <= set(by["inter-bold"])
    r, data = c.req("GET", "/font-file?id=inter-bold", raw=True)
    assert r.status == 200 and r.getheader("Content-Type") == "font/ttf" and data[:4] in (b"\x00\x01\x00\x00", b"true")
    assert c.req("GET", "/font-file?id=nope-nope")[0] == 404


def test_import_media_and_name_clash(srv):
    c, d = srv
    body = open(os.path.join(d, "media", "logo.png"), "rb").read()
    st, res = c.req("POST", "/api/import?name=New%20Logo.png", body)
    assert st == 200 and res == {"path": "media/New Logo.png", "kind": "image"}
    assert open(os.path.join(d, res["path"]), "rb").read() == body
    st, res = c.req("POST", "/api/import?name=New%20Logo.png", body)        # the same file again (picked from media/ itself): referenced, not copied
    assert res == {"path": "media/New Logo.png", "kind": "image", "existing": True}
    assert not os.path.exists(os.path.join(d, "media", "New Logo-2.png"))
    st, res = c.req("POST", "/api/import?name=New%20Logo.png", body + b"x")  # another file with the same name: a new name
    assert res["path"] == "media/New Logo-2.png"
    st, res = c.req("POST", "/api/import?name=New%20Logo.png", body + b"x")
    assert res["path"] == "media/New Logo-2.png" and res["existing"] is True
    st, res = c.req("POST", "/api/import?name=..%2F..%2Fevil.wav", b"RIFF")
    assert res["path"] == "media/evil.wav" and res["kind"] == "audio"
    st, res = c.req("POST", "/api/import?name=x.exe", b"MZ")
    assert st == 415
    # connection is still usable after an early error (unread body)
    assert c.req("GET", "/api/version")[0] == 200


def test_import_large_streamed(srv):
    c, d = srv
    big = os.urandom(1024) * (5 * 1024)          # 5 MB
    st, res = c.req("POST", "/api/import?name=big.mp3", big)
    assert st == 200 and os.path.getsize(os.path.join(d, res["path"])) == len(big)


def test_import_font_registers_it(srv):
    c, d = srv
    src = os.path.join(ROOT, "fonts", "SpaceGrotesk-Bold.ttf")
    st, res = c.req("POST", "/api/import?name=My%20Grotesk.ttf", open(src, "rb").read())
    assert st == 200 and res["kind"] == "font" and res["path"] == "fonts/My Grotesk.ttf"
    assert res["font_ids"] == ["space-grotesk-bold"]
    fl = {f["id"]: f for f in c.req("GET", "/api/fonts")[1]}
    assert fl["space-grotesk-bold"]["source"] == "project"
    assert c.req("GET", "/font-file?id=space-grotesk-bold")[0] == 200
    st, res = c.req("POST", "/api/import?name=junk.ttf", b"not really a font")
    assert st == 400 and not os.path.exists(os.path.join(d, "fonts", "junk.ttf"))


def test_import_woff_rejected(srv):
    c, _ = srv
    st, res = c.req("POST", "/api/import?name=f.woff2", b"wOF2")
    assert st == 415 and "woff" in res["error"]


def test_media_info_waveform_thumbs(srv, monkeypatch):
    monkeypatch.setenv("KURGU_PROXY", "always")      # the example clip is light (a proxy would not pay); this test is about the proxy mechanics
    c, d = srv
    st, info = c.req("GET", "/api/media-info?path=media/a.mp4")
    assert st == 200 and info["width"] > 0 and info["duration"] > 1 and info["has_audio"] is True
    assert info["width"] > 1280 and info["proxy"].startswith("/media?path=")   # wide video: proxy is generated
    for _ in range(120):
        info = c.req("GET", "/api/media-info?path=media/a.mp4")[1]
        if info["proxy_ready"]:
            break
        time.sleep(0.5)
    assert info["proxy_ready"] is True
    pr, pdata = c.req("GET", info["proxy"], raw=True)
    assert pr.status == 200 and len(pdata) > 1000
    assert c.req("GET", "/api/media-info?path=media/logo.png")[1]["proxy"] is None
    st, wf = c.req("GET", "/api/waveform?path=media/muzik.wav")
    assert wf["rate"] == 100 and len(wf["peaks"]) > 500 and 0 <= max(wf["peaks"]) <= 1
    st, th = c.req("GET", "/api/thumbs?path=media/a.mp4&n=6")
    assert st == 200 and len(th) == 6
    r, data = c.req("GET", th[0], raw=True)
    assert r.status == 200 and data[:2] == b"\xff\xd8"
    assert c.req("GET", "/api/thumbs?path=media/a.mp4&n=6")[1] == th          # cached, same urls
    st, th = c.req("GET", "/api/thumbs?path=media/logo.png&n=3")
    assert st == 200 and len(th) == 3
    assert c.req("GET", "/api/thumbs?path=media/muzik.wav&n=3")[0] == 400


def test_frame_endpoint(srv):
    c, _ = srv
    r, data = c.req("GET", "/api/frame?t=5", raw=True)
    assert r.status == 200 and data[:8] == b"\x89PNG\r\n\x1a\n"
    r2, data2 = c.req("GET", "/api/frame?t=5", raw=True)
    assert data2 == data
    assert c.req("GET", "/api/frame?t=abc")[0] == 400


def test_render_endpoint_draft(srv):
    c, d = srv
    st, res = c.req("POST", "/api/render", {"draft": True})
    assert st == 202
    assert c.req("POST", "/api/render", {"draft": True})[0] == 409
    for _ in range(300):
        st, s = c.req("GET", "/api/render")
        if s["status"] != "running":
            break
        time.sleep(0.5)
    assert s["status"] == "done" and s["progress"] == 1 and s["output"].startswith("/media?path=")
    assert os.path.exists(os.path.join(d, "output_draft.mp4"))
    assert c.req("GET", s["output"], headers={"Range": "bytes=0-99"}, raw=True)[0].status == 206


def test_static_web_and_unknown(srv):
    c, _ = srv
    r, data = c.req("GET", "/", raw=True)
    assert r.status in (200, 404)           # index.html is owned by the frontend worker
    assert c.req("GET", "/nope")[0] == 404
    assert c.req("GET", "/web/../server.py")[0] == 404
    assert c.req("GET", "/api/nope")[0] == 404


def test_startup_migrates_legacy_project(tmp_path):
    (tmp_path / "proje.json").write_text(open(os.path.join(ROOT, "tests", "fixtures", "v1_project.json"),
                                              encoding="utf-8").read(), encoding="utf-8")
    before = (tmp_path / "proje.json").read_bytes()
    s = server.make_server(str(tmp_path), port=0)
    s.server_close()
    assert json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))["version"] == 2
    assert (tmp_path / "proje.json").read_bytes() == before


def test_startup_writes_template_for_empty_dir(tmp_path):
    s = server.make_server(str(tmp_path), port=0)
    s.server_close()
    p = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert (p["width"], p["height"], p["fps"], p["duration"], p["layers"]) == (1920, 1080, 30, 10, [])


def test_port_fallback(tmp_path):
    a = server.make_server(str(tmp_path), port=0)
    busy = a.server_address[1]
    b = server.make_server(str(tmp_path), port=busy, tries=20)
    assert b.server_address[1] != busy
    a.server_close()
    b.server_close()


# ---------------------------------------------------------------- request gate (CSRF / cross-site)
def test_foreign_origin_is_refused_everywhere(srv):
    c, d = srv
    evil = {"Origin": "http://evil.example"}
    before = set(os.listdir(os.path.join(d, "media")))
    for method, path, body in [("POST", "/api/import?name=planted.wav", b"RIFF"), ("POST", "/api/render", {}),
                               ("POST", "/api/ai", {"prompt": "x"}), ("PUT", "/api/project", {"project": {}}),
                               ("GET", "/media?path=media/none.mp4", None), ("GET", "/api/frame?t=0", None),
                               ("GET", "/font-file?id=x", None)]:
        st, data = c.req(method, path, body, headers=evil)
        assert st == 403, (method, path)
    assert set(os.listdir(os.path.join(d, "media"))) == before
    # another localhost port is a foreign origin too
    assert c.req("GET", "/api/version", headers={"Origin": f"http://127.0.0.1:{c.port + 1}"})[0] == 403
    assert c.req("GET", "/api/version", headers={"Origin": "null"})[0] == 403


def test_sec_fetch_site_gate(srv):
    c, _ = srv
    for v in ("cross-site", "same-site"):
        assert c.req("GET", "/api/version", headers={"Sec-Fetch-Site": v})[0] == 403
        assert c.req("GET", "/media?path=x", headers={"Sec-Fetch-Site": v})[0] == 403
    for v in ("same-origin", "none"):
        assert c.req("GET", "/api/version", headers={"Sec-Fetch-Site": v})[0] == 200


def test_own_origin_and_headerless_allowed(srv):
    c, _ = srv
    assert c.req("GET", "/api/version")[0] == 200
    for host in ("127.0.0.1", "localhost"):
        h = {"Origin": f"http://{host}:{c.port}", "Sec-Fetch-Site": "same-origin"}
        assert c.req("GET", "/api/version", headers=h)[0] == 200


def test_path_traversal_names(srv):
    c, d = srv
    st, r = c.req("POST", "/api/import?name=" + "..%2F..%2Fescape.png", b"\x89PNG")
    assert st == 200 and r["path"].startswith("media/") and ".." not in r["path"]
    assert not os.path.exists(os.path.join(os.path.dirname(d), "escape.png"))
    assert c.req("GET", "/media?path=" + "../../../etc/passwd")[0] == 403
    assert c.req("POST", "/api/exports/reveal", {"path": "../../etc/passwd"})[0] == 404


# ---------------------------------------------------------------- nothing the server writes goes through a symlink the project ships (security review N1, N3)
import pytest as _pytest
import agent_notes


@_pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_changes_md_brief_md_and_note_signatures_are_never_written_through_symlinks(project_dir, tmp_path):
    import server
    victim = tmp_path / "zshrc"
    victim.write_text("# my profile\n")
    d = os.path.realpath(project_dir)
    os.symlink(str(victim), os.path.join(d, "changes.md"))
    os.symlink(str(victim), os.path.join(d, "brief.md"))
    app = server.App(d)
    raw = json.load(open(os.path.join(d, "project.json")))
    raw["layers"][0]["name"] = 'a"; touch /tmp/kurgu-pwned; "b\nsecond line'
    body = {"project": raw, "summary": ['renamed a"; touch /tmp/kurgu-pwned; "b\nsecond line']}
    try:
        app.save_project(body)
    except Exception:
        pass
    assert victim.read_text() == "# my profile\n"                                    # changes.md -> victim: refused, not appended
    try:
        app.save_brief("hello brief")
    except Exception:
        pass
    assert victim.read_text() == "# my profile\n"
    os.remove(os.path.join(d, "changes.md"))
    app.write_changes(['x\ny"; z'])
    lines = open(os.path.join(d, "changes.md")).read().splitlines()
    assert any(l.endswith('x y"; z') for l in lines)                                  # one line per entry
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    os.symlink(str(other), os.path.join(d, ".kurgu", "notes"))
    with _pytest.raises(Exception):
        agent_notes.sig_path(d, "n_1")
    assert list(other.iterdir()) == []


@_pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_dot_kurgu_subfolder_is_refused_by_the_server(project_dir, tmp_path):
    import server
    d = os.path.realpath(project_dir)
    other = tmp_path / "elsewhere"
    other.mkdir()
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    os.symlink(str(other), os.path.join(d, ".kurgu", "backups"))
    app = server.App(d)
    with _pytest.raises(agent_notes.KurguRefusal):
        app.sub("backups")
    assert list(other.iterdir()) == []


def test_project_output_outside_the_project_is_refused(project_dir, tmp_path):
    import subprocess
    import sys
    d = os.path.realpath(project_dir)
    p = json.load(open(os.path.join(d, "project.json")))
    p["output"] = "../vo-escaped.mp4"
    json.dump(p, open(os.path.join(d, "project.json"), "w"))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), d, "--draft"], capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "outside the project" in (r.stdout + r.stderr)
    assert not os.path.exists(os.path.join(os.path.dirname(d), "vo-escaped_draft.mp4")) and not os.path.exists(os.path.join(os.path.dirname(d), "vo-escaped.mp4"))
    import server
    assert os.path.realpath(os.path.join(os.path.dirname(d), "vo-escaped.mp4")) not in server.App(d).allowed_paths()



def test_the_listen_backlog_takes_a_burst_of_module_loads():
    """Windows CI: the editor's parallel module loads overflowed the default backlog (5) and some got ERR_CONNECTION_REFUSED."""
    import server
    assert server.Server.request_queue_size >= 64


@_pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_versions_folder_never_receives_media(project_dir, tmp_path):
    import versions
    d = os.path.realpath(project_dir)
    other = tmp_path / "elsewhere"
    other.mkdir()
    clip = os.path.join(d, "media", "a.mp4")
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    os.symlink(str(other), os.path.join(d, ".kurgu", "versions"))
    assert versions.snapshot(d, clip, sync=True) is None and list(other.iterdir()) == []
    assert versions.list_versions(d, clip) == []
    os.remove(os.path.join(d, ".kurgu", "versions"))
    moved = tmp_path / "kurgu-elsewhere"
    shutil.move(os.path.join(d, ".kurgu"), str(moved))
    os.symlink(str(moved), os.path.join(d, ".kurgu"))
    assert versions.snapshot(d, clip, sync=True) is None and not (moved / "versions").exists()
    with _pytest.raises(agent_notes.KurguRefusal):
        versions.versions_dir(d)


@_pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_shipped_changes_md_symlink_is_a_409_before_anything_is_written(project_dir, tmp_path):
    import threading
    d = os.path.realpath(project_dir)
    victim = tmp_path / "profile"
    victim.write_text("keep\n")
    p = json.load(open(os.path.join(d, "project.json")))
    p["notes"] = [{"id": "n_1", "t": 1.0, "text": "x", "created": "2026-11-07T10:00:00"}]
    json.dump(p, open(os.path.join(d, "project.json"), "w"))
    os.symlink(str(victim), os.path.join(d, "changes.md"))
    before = open(os.path.join(d, "project.json"), "rb").read()
    srv = server.make_server(d, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Client(srv.server_address[1])
        st, got = c.req("GET", "/api/project")
        body = {"project": dict(got["project"], name="renamed"), "summary": ["renamed"], "base_version": got["version"]}
        st, res = c.req("PUT", "/api/project", body)
        assert st == 409 and res.get("reason") == "symlink", (st, res)
        assert open(os.path.join(d, "project.json"), "rb").read() == before and victim.read_text() == "keep\n"      # nothing written, nothing appended
        st, res = c.req("POST", "/api/notes/send", {"ids": ["n_1"]})
        assert st == 409 and res.get("reason") == "symlink"
        assert open(os.path.join(d, "project.json"), "rb").read() == before                                         # the note was not stamped as sent
        os.remove(os.path.join(d, "changes.md"))
        assert c.req("PUT", "/api/project", body)[0] == 200                                                         # without the symlink the same save works
    finally:
        srv.shutdown()
        srv.server_close()


@_pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_changes_md_symlink_also_stops_presets_and_the_brief_before_they_write(srv, tmp_path):
    c, d = srv
    victim = tmp_path / "profile"
    victim.write_text("keep\n")
    os.symlink(str(victim), os.path.join(d, "changes.md"))
    before = open(os.path.join(d, "project.json"), "rb").read()
    st, res = c.req("POST", "/api/presets/apply", {"layer_id": "k_logo", "preset": "in.fade-up", "at": "in"})
    assert st == 409 and res.get("reason") == "symlink", (st, res)
    assert open(os.path.join(d, "project.json"), "rb").read() == before                     # the preset was not baked in
    st, res = c.req("PUT", "/api/brief", {"text": "new brief"})
    assert st == 409 and res.get("reason") == "symlink", (st, res)
    assert not os.path.exists(os.path.join(d, "brief.md")) and victim.read_text() == "keep\n"


@_pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on Windows")
def test_a_fifo_changes_md_is_a_409_before_anything_is_written(srv):
    c, d = srv
    os.mkfifo(os.path.join(d, "changes.md"))
    before = open(os.path.join(d, "project.json"), "rb").read()
    st, got = c.req("GET", "/api/project")
    st, res = c.req("PUT", "/api/project", {"project": dict(got["project"], name="renamed"), "summary": ["renamed"], "base_version": got["version"]})
    assert st == 409 and res.get("reason") == "special_file", (st, res)
    assert open(os.path.join(d, "project.json"), "rb").read() == before
    os.remove(os.path.join(d, "changes.md"))
    assert c.req("PUT", "/api/project", {"project": dict(got["project"], name="renamed"), "summary": ["renamed"], "base_version": got["version"]})[0] == 200
