"""Caching, compression and cheap polling of the local server (ETag / 304, gzip, immutable derived files, stat-based /api/version)."""
import gzip
import http.client
import json
import os
import threading
import time

import pytest

import server


@pytest.fixture()
def srv(project_dir):
    s = server.make_server(project_dir, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, project_dir
    s.shutdown()
    s.server_close()


def get(s, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=60)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def test_static_files_revalidate_with_etag_and_are_gzipped(srv):
    s, _ = srv
    r, body = get(s, "/web/js/main.js")
    etag = r.getheader("ETag")
    assert r.status == 200 and etag and r.getheader("Cache-Control") == "no-cache" and len(body) > 1000
    r2, b2 = get(s, "/web/js/main.js", {"If-None-Match": etag})
    assert r2.status == 304 and b2 == b"" and r2.getheader("ETag") == etag                       # no body, the file is not even read
    r3, b3 = get(s, "/web/js/main.js", {"Accept-Encoding": "gzip"})
    assert r3.getheader("Content-Encoding") == "gzip" and gzip.decompress(b3) == body and len(b3) < len(body) / 2
    r4, _ = get(s, "/web/js/main.js", {"Accept-Encoding": "gzip", "If-None-Match": etag})
    assert r4.status == 304


def test_project_and_state_stay_no_store(srv):
    s, _ = srv
    for path in ("/api/project", "/api/version", "/api/state"):
        r, _b = get(s, path)
        assert r.status == 200 and r.getheader("Cache-Control") == "no-store" and not r.getheader("ETag"), path


def test_waveform_and_thumbs_answer_304_and_derived_files_are_immutable(srv):
    s, d = srv
    r, body = get(s, "/api/waveform?path=media/muzik.wav")
    etag = r.getheader("ETag")
    assert r.status == 200 and etag and json.loads(body)["peaks"]
    assert get(s, "/api/waveform?path=media/muzik.wav", {"If-None-Match": etag})[0].status == 304
    r, body = get(s, "/api/thumbs?path=media/a.mp4&n=3")
    assert r.status == 200 and r.getheader("ETag")
    assert get(s, "/api/thumbs?path=media/a.mp4&n=3", {"If-None-Match": r.getheader("ETag")})[0].status == 304
    jpg = json.loads(body)[0]
    r, data = get(s, jpg)
    assert r.status == 200 and data[:2] == b"\xff\xd8" and "immutable" in r.getheader("Cache-Control")
    r, _b = get(s, "/media?path=media/a.mp4", {"Range": "bytes=0-9"})
    assert r.status == 206 and r.getheader("Cache-Control") == "no-cache"                        # the user's own media is revalidated


def test_version_is_not_recomputed_while_the_file_is_unchanged(srv, monkeypatch):
    s, d = srv
    app = s.app
    reads = []
    real = app.project_bytes
    monkeypatch.setattr(app, "project_bytes", lambda: (reads.append(1), real())[1])
    v = app.version()
    assert app.version() == v == app.version() and len(reads) == 1            # stat only, the file is read once
    p = os.path.join(d, "project.json")
    data = json.load(open(p, encoding="utf-8"))
    data["name"] = "changed name"
    time.sleep(0.01)
    json.dump(data, open(p, "w", encoding="utf-8"))
    assert app.version() != v and len(reads) == 2
