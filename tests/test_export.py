"""Export options (render.py flags), /api/render presets, cancellation, /api/exports. Small sizes keep it fast."""
import json
import os
import subprocess
import sys
import threading
import time

import pytest
import media_tools

import exports
import server
from test_server import Client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER = os.path.join(ROOT, "render.py")
SMALL = ["--size", "320x180"]


def run(*args):
    return subprocess.run([sys.executable, RENDER, *args], capture_output=True, text=True, timeout=110)


def probe(path):
    r = subprocess.run([media_tools.ffprobe(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                       capture_output=True, text=True)
    info = json.loads(r.stdout)
    vs = [s for s in info["streams"] if s["codec_type"] == "video"]
    au = [s for s in info["streams"] if s["codec_type"] == "audio"]
    return info, (vs[0] if vs else None), (au[0] if au else None)


# codec flag, extension, expected video codec, expected audio codec
CASES = [
    ("h264", "mp4", "h264", "aac"),
    ("h265", "mp4", "hevc", "aac"),
    ("prores", "mov", "prores", "pcm_s16le"),
    ("vp9", "webm", "vp9", "opus"),
    ("gif", "gif", "gif", None),
    ("wav", "wav", None, "pcm_s16le"),
    ("mp3", "mp3", None, "mp3"),
]


@pytest.mark.parametrize("codec,ext,vcodec,acodec", CASES)
def test_each_codec_and_format(project_dir, tmp_path, codec, ext, vcodec, acodec):
    dst = str(tmp_path / f"o.{ext}")
    r = run(project_dir, "--codec", codec, "--from", "1", "--to", "3", *SMALL, "--output", dst)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1] == f"DONE {dst}"
    info, v, a = probe(dst)
    assert (v["codec_name"] if v else None) == vcodec
    assert (a["codec_name"] if a else None) == acodec
    if v:
        assert (v["width"], v["height"]) == (320, 180)
    if codec != "gif":       # gif container durations are frame-rounded
        assert abs(float(info["format"]["duration"]) - 2.0) < 0.2
    assert not os.path.exists(os.path.join(project_dir, ".kurgu", "render_tmp"))


def test_gif_defaults_to_15_fps(project_dir, tmp_path):
    dst = str(tmp_path / "o.gif")
    assert run(project_dir, "--codec", "gif", "--from", "1", "--to", "3", *SMALL, "--output", dst).returncode == 0
    _, v, _ = probe(dst)
    num, den = v["r_frame_rate"].split("/")
    assert float(num) / float(den) <= 15.01


def test_no_audio_and_range_duration_and_scale(project_dir, tmp_path):
    dst = str(tmp_path / "o.mp4")
    r = run(project_dir, "--from", "2", "--to", "5", "--no-audio", "--scale", "0.25", "--crf", "30", "--output", dst)
    assert r.returncode == 0, r.stdout + r.stderr
    info, v, a = probe(dst)
    assert a is None and (v["width"], v["height"]) == (480, 270)
    assert abs(float(info["format"]["duration"]) - 3.0) < 0.1


def test_range_with_audio_has_matching_sound_length(project_dir, tmp_path):
    dst = str(tmp_path / "o.mp4")
    r = run(project_dir, "--from", "4", "--to", "7", "--bitrate", "500k", "--audio-bitrate", "96k", *SMALL, "--output", dst)
    assert r.returncode == 0, r.stdout + r.stderr
    info, v, a = probe(dst)
    assert a is not None
    assert abs(float(v["duration"]) - 3.0) < 0.1 and abs(float(a["duration"]) - 3.0) < 0.2


def test_bad_combinations_report_errors(project_dir, tmp_path):
    r = run(project_dir, "--codec", "prores", "--output", str(tmp_path / "o.mp4"))
    assert r.returncode != 0 and r.stdout.strip().splitlines()[-1].startswith("ERROR codec prores cannot be written")
    r = run(project_dir, "--from", "5", "--to", "2", "--output", str(tmp_path / "o.mp4"))
    assert r.returncode != 0 and "ERROR --to must be greater" in r.stdout


def test_draft_flag_still_a_preset(project_dir):
    r = run(project_dir, "--draft", "--from", "0", "--to", "1")
    assert r.returncode == 0, r.stdout + r.stderr
    out = os.path.join(project_dir, "output_draft.mp4")
    _, v, a = probe(out)
    assert (v["width"], v["height"]) == (960, 540) and a["codec_name"] == "aac"


# ---------------------------------------------------------------- options -> args
def test_resolve_options_and_paths(project_dir):
    proj = {"name": "My Film", "width": 1080, "height": 1920}
    o = exports.resolve_options({"preset": "youtube"}, proj)
    assert exports.output_size(o, proj) == (1080, 1920)                    # long side 1920 on a vertical sequence
    o = exports.resolve_options({"preset": "youtube", "options": {"scale": 0.5, "crf": 23}}, proj)
    assert exports.output_size(o, proj) == (540, 960) and "fit_long" not in o
    o = exports.resolve_options({"options": {"codec": "mp3"}}, proj)
    assert o["format"] == "mp3" and o["audio"] is True and exports.output_size(o, proj) is None
    with pytest.raises(exports.OptionError):
        exports.resolve_options({"options": {"codec": "vp9", "format": "mp4"}}, proj)
    with pytest.raises(exports.OptionError):
        exports.resolve_options({"options": {"range": {"from": 5, "to": 2}}}, proj)
    path, existed = exports.plan_output(exports.resolve_options({"preset": "web"}, proj), proj, project_dir)
    assert path == os.path.join(os.path.realpath(project_dir), "exports", "My Film_web.webm") and not existed
    with pytest.raises(exports.OptionError):
        exports.plan_output(exports.resolve_options({"options": {"folder": "../elsewhere"}}, proj), proj, project_dir)


# ---------------------------------------------------------------- server
@pytest.fixture()
def srv(project_dir):
    s = server.make_server(project_dir, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield Client(s.server_address[1]), project_dir
    s.shutdown()
    s.server_close()


def wait_status(c, want, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, s = c.req("GET", "/api/render")
        if s["status"] in want:
            return s
        time.sleep(0.15)
    raise AssertionError("timeout waiting for " + str(want))


def test_preset_export_lists_and_renames_existing(srv):
    c, d = srv
    body = {"options": {"codec": "h264", "crf": 30, "width": 320, "range": {"from": 0, "to": 1.5}, "name": "clip", "audio": False}}
    st, plan = c.req("POST", "/api/render/plan", body)
    assert st == 200 and plan["output"] == "exports/clip.mp4" and plan["exists"] is False and plan["size"] == [320, 180]
    assert c.req("POST", "/api/render", body)[0] == 202
    s = wait_status(c, ("done", "error"))
    assert s["status"] == "done", s
    assert s["output_path"] == "exports/clip.mp4" and s["output_size"] > 0 and s["elapsed"] is not None
    _, v, a = probe(os.path.join(d, "exports", "clip.mp4"))
    assert a is None and v["width"] == 320
    st, plan = c.req("POST", "/api/render/plan", body)
    assert plan["exists"] is True and plan["output"] == "exports/clip (2).mp4"
    st, plan = c.req("POST", "/api/render/plan", {"options": dict(body["options"], exists="overwrite")})
    assert plan["output"] == "exports/clip.mp4"
    st, lst = c.req("GET", "/api/exports")
    assert [x["path"] for x in lst] == ["exports/clip.mp4"] and lst[0]["size"] > 0 and lst[0]["url"].startswith("/media?path=")
    r, data = c.req("GET", lst[0]["url"], raw=True)
    assert r.status == 200 and len(data) == lst[0]["size"]
    assert c.req("POST", "/api/render", {"preset": "nope"})[0] == 400
    assert c.req("POST", "/api/render", {"options": {"folder": "../x"}})[0] == 400
    assert c.req("POST", "/api/render/cancel")[0] == 409


def test_cancel_mid_render_leaves_nothing(srv):
    c, d = srv
    # the 4K master of the whole 12 s example takes far longer than this test waits
    assert c.req("POST", "/api/render", {"preset": "master4k", "options": {"name": "big"}})[0] == 202
    t0 = time.time()
    while time.time() - t0 < 60:
        s = c.req("GET", "/api/render")[1]
        if s["progress"] > 0.06:
            break
        time.sleep(0.1)
    assert s["status"] == "running"
    st, res = c.req("POST", "/api/render/cancel")
    assert st == 200 and res["status"] == "cancelled"
    s = c.req("GET", "/api/render")[1]
    assert s["status"] == "cancelled" and s["error"] is None and s["output"] is None
    assert not os.path.exists(os.path.join(d, "exports", "big.mp4"))
    assert not os.path.exists(os.path.join(d, ".kurgu", "render_tmp"))
    time.sleep(0.3)
    procs = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    assert d not in procs                     # no render.py / ffmpeg left behind
    # a new render can start afterwards
    assert c.req("POST", "/api/render", {"options": {"width": 160, "range": {"to": 0.5}, "audio": False}})[0] == 202
    assert wait_status(c, ("done", "error"))["status"] == "done"


def test_server_close_cancels_render(project_dir):
    s = server.make_server(project_dir, port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    c = Client(s.server_address[1])
    assert c.req("POST", "/api/render", {"preset": "master4k"})[0] == 202
    time.sleep(1.5)
    s.shutdown()
    s.server_close()
    assert s.app.render["status"] == "cancelled"
    assert not os.path.exists(os.path.join(project_dir, ".kurgu", "render_tmp"))


def test_i18n_key_sets_identical_and_export_keys_present():
    cat = {l: json.load(open(os.path.join(ROOT, "web", "i18n", f"{l}.json"), encoding="utf-8")) for l in ("en", "tr")}
    assert set(cat["en"]) == set(cat["tr"])
    assert all(f"export.preset.{p}" in cat["en"] for p in exports.PRESETS)
