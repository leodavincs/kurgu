"""ffmpeg discovery, streamed audio mix (sample-exact), playlist-source refusal, proxy progress, hardware encoders."""
import os
import stat
import subprocess
import sys
import time
import urllib.parse

import numpy as np
import pytest

import media_tools
import render
import schema
import server
import transcribe


@pytest.fixture(autouse=True)
def fresh_tools():
    media_tools.reset()
    yield
    media_tools.reset()


def sh(*a):
    subprocess.run([media_tools.ffmpeg(), "-v", "error", "-y", *a], check=True)


# ------------------------------------------------------------ discovery
def test_env_override_and_actionable_error(tmp_path, monkeypatch):
    fake = tmp_path / "myffmpeg"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("KURGU_FFMPEG", str(fake))
    media_tools.reset()
    assert media_tools.find("ffmpeg") == str(fake)
    monkeypatch.setenv("KURGU_FFMPEG", str(tmp_path))          # a folder holding the tool also works
    exe = tmp_path / media_tools._exe("ffmpeg")                # ffmpeg.exe on Windows
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    media_tools.reset()
    assert media_tools.find("ffmpeg") == str(exe)
    monkeypatch.delenv("KURGU_FFMPEG")
    monkeypatch.setattr(media_tools, "_extra_dirs", lambda: [])
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    media_tools.reset()
    with pytest.raises(media_tools.MediaToolError) as e:
        media_tools.ffmpeg()
    assert "KURGU_FFMPEG" in str(e.value) and "not found" in str(e.value)
    media_tools.reset()


def test_render_reports_missing_ffmpeg(project_dir, monkeypatch, capsys):
    monkeypatch.setattr(media_tools, "find", lambda tool: None)
    with pytest.raises(SystemExit):
        render.main([project_dir, "--frame", "0.5", "--draft"])
    o = capsys.readouterr().out
    assert o.startswith("ERROR ") and "KURGU_FFPROBE" in o


# ------------------------------------------------------------ streamed audio mix
def reference_mix(ctx, project):
    """The former whole-timeline mixer, kept here as the oracle."""
    N = int(round(ctx.duration * render.SR))
    mix = np.zeros((N, 2), np.float32)
    for d in render.audio_layers(project):
        p = render.resolve(ctx.pdir, d["src"])
        t0, t1 = float(d.get("start", 0)), float(d.get("end", 0))
        src_in = float(d.get("src_in", 0))
        if t0 < 0:
            src_in -= t0
            t0 = 0.0
        dur = t1 - t0
        r = subprocess.run([media_tools.ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{src_in:.4f}", "-t", f"{dur:.4f}", "-i", p, "-vn",
                            "-ac", "2", "-ar", str(render.SR), "-f", "f32le", "pipe:1"], capture_output=True)
        pcm = np.frombuffer(r.stdout[:len(r.stdout) // 8 * 8], np.float32).reshape(-1, 2)
        s0 = int(round(t0 * render.SR))
        n = min(len(pcm), int(round(dur * render.SR)), N - s0)
        tt = (s0 + np.arange(n)) / render.SR
        db = float(d.get("volume_db", 0))
        if render.anim.is_animated(d, ("volume_db",)):
            lt0 = float(d.get("start", 0))
            grid = np.arange(0.0, (t1 - lt0) + 0.005, 0.005)
            vals = np.array([render.anim.value(d, "volume_db", float(g)) for g in grid])
            db = np.interp(tt - lt0, grid, vals)
        if d.get("envelope"):
            db = db + render.envelope_db(d["envelope"], tt)
        gain = np.power(10.0, db / 20.0) * render.fade_factor(d.get("fade_in"), d.get("fade_out"), float(d.get("start", 0)), t1, tt)
        gain = np.asarray(gain, np.float32)
        mix[s0:s0 + n] += pcm[:n] * (gain.reshape(-1, 1) if gain.ndim else gain)
    return np.clip(mix, -8, 8)


def test_audio_mix_is_sample_exact_across_blocks(tmp_path, monkeypatch):
    (tmp_path / "media").mkdir()
    sh("-f", "lavfi", "-i", "sine=frequency=440:duration=2.2", "-ac", "2", str(tmp_path / "media" / "a.wav"))
    sh("-f", "lavfi", "-i", "sine=frequency=220:duration=1.0", str(tmp_path / "media" / "short.wav"))
    layers = [
        {"id": "a", "type": "audio", "src": "media/a.wav", "start": 0.3, "end": 2.5, "volume_db": -3, "fade_in": 0.4, "fade_out": 0.5},
        {"id": "b", "type": "audio", "src": "media/a.wav", "start": 0.9, "end": 3.1, "src_in": 0.25, "envelope": [[0, 0], [1.5, -8], [3, 2]]},
        {"id": "c", "type": "audio", "src": "media/short.wav", "start": 1.0, "end": 3.0},          # source shorter than the clip
        {"id": "d", "type": "audio", "src": "media/a.wav", "start": 0.0, "end": 3.4,
         "keys": {"volume_db": [{"t": 0, "v": -12}, {"t": 2, "v": 0}]}},
    ]
    p = {"version": 2, "name": "m", "width": 320, "height": 180, "fps": 30, "duration": 3.4, "layers": layers, "output": "o.wav"}
    (tmp_path / "project.json").write_text(__import__("json").dumps(p))
    project = render.load(str(tmp_path))
    ctx = render.setup(str(tmp_path), project, False)
    ref = reference_mix(ctx, project)
    monkeypatch.setattr(render, "AUDIO_BLOCK", 7001)        # many odd-sized blocks
    tmp = tmp_path / "t"
    tmp.mkdir()
    wav = render.build_audio(ctx, project, str(tmp), lambda x: None)
    got = np.fromfile(str(tmp / "mix.f32"), np.float32).reshape(-1, 2)          # the mixer's own output, before the ffmpeg pass
    assert os.path.exists(wav)
    assert got.shape == ref.shape
    assert np.array_equal(got, ref)


# ------------------------------------------------------------ untrusted playlists
def playlist(tmp_path):
    (tmp_path / "media").mkdir(exist_ok=True)
    sh("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(tmp_path / "media" / "real.wav"))
    (tmp_path / "media" / "evil.m3u8").write_text("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nreal.wav\n#EXT-X-ENDLIST\n")
    (tmp_path / "media" / "evil.txt").write_text("ffconcat version 1.0\nfile real.wav\n")
    return [str(tmp_path / "media" / "evil.m3u8"), str(tmp_path / "media" / "evil.txt")]


def test_playlist_sources_are_refused(tmp_path):
    for evil in playlist(tmp_path):
        render._probe_cache.clear()
        with pytest.raises(render.RenderError) as e:
            render.probe(evil)
        assert "playlist" in str(e.value)
        with pytest.raises(server.HttpError):
            server.App.ffprobe(evil)
        with pytest.raises(transcribe.TranscribeError):
            transcribe.extract_audio(evil, str(tmp_path / "x.wav"))


def test_playlist_layer_fails_cleanly_in_render(tmp_path):
    evil = playlist(tmp_path)[0]
    p = {"version": 2, "name": "m", "width": 320, "height": 180, "fps": 30, "duration": 1, "output": "o.mp4",
         "layers": [{"id": "a", "type": "audio", "src": "media/evil.m3u8", "start": 0, "end": 1}]}
    (tmp_path / "project.json").write_text(__import__("json").dumps(p))
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "render.py"),
                        str(tmp_path), "--output", str(tmp_path / "o.mp4")], capture_output=True, text=True)
    assert r.returncode == 1 and "ERROR" in r.stdout and "playlist" in r.stdout and not (tmp_path / "o.mp4").exists()


def test_input_args_whitelist():
    a = media_tools.input_args("/x/y.mp4")
    assert a[:2] == ["-protocol_whitelist", "file,pipe"] and a[-2:] == ["-i", "/x/y.mp4"]


# ------------------------------------------------------------ proxy progress + hardware
def test_media_info_proxy_progress(tmp_path):
    (tmp_path / "media").mkdir()
    sh("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=3", "-pix_fmt", "yuv420p", str(tmp_path / "media" / "v.mp4"))
    (tmp_path / "project.json").write_text('{"version":2,"name":"p","width":1920,"height":1080,"fps":30,"duration":3,"layers":[]}')
    app = server.App(str(tmp_path))
    path = os.path.realpath(str(tmp_path / "media" / "v.mp4"))
    seen = []
    t0 = time.time()
    while time.time() - t0 < 60:
        i = app.media_info(path)
        seen.append(i["proxy_progress"])
        assert i["proxy_progress"] is None or 0 <= i["proxy_progress"] <= 1
        if i["proxy_ready"]:
            break
        time.sleep(0.05)
    assert i["proxy_ready"] and i["proxy_progress"] == 1.0 and i["proxy_eta"] is None
    small = tmp_path / "media" / "s.mp4"
    sh("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=1", "-pix_fmt", "yuv420p", str(small))
    j = app.media_info(os.path.realpath(str(small)))
    assert j["proxy"] is None and j["proxy_progress"] is None


def test_big_sources_are_prewarmed(tmp_path, monkeypatch):
    (tmp_path / "media").mkdir()
    sh("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=1", "-pix_fmt", "yuv420p", str(tmp_path / "media" / "v.mp4"))
    (tmp_path / "project.json").write_text('{"version":2,"name":"p","width":1920,"height":1080,"fps":30,"duration":1,"layers":[]}')
    monkeypatch.setattr(server, "BIG_SOURCE", 1000)
    app = server.App(str(tmp_path))
    app.list_files()
    proxy = app.proxy_path(os.path.realpath(str(tmp_path / "media" / "v.mp4")))
    t0 = time.time()
    while not os.path.exists(proxy) and time.time() - t0 < 30:
        time.sleep(0.1)
    assert os.path.exists(proxy)


def test_hw_video_args_shapes():
    for enc in ("h264_videotoolbox", "hevc_videotoolbox", "h264_nvenc", "hevc_nvenc", "h264_qsv", "h264_amf", "hevc_amf"):
        a = media_tools.hw_video_args(enc, crf=20)
        assert a[:2] == ["-c:v", enc]
        b = media_tools.hw_video_args(enc, bitrate="8000k")
        assert "8000k" in b
    assert media_tools.hw_encoders().keys() == {"h264", "h265"}


# ------------------------------------------------------------ threaded compositor == serial compositor
def test_threaded_frames_equal_serial_frames(project_dir):
    project = render.load(project_dir)
    ctx = render.setup(project_dir, project, True)                 # draft size keeps it quick
    n0, N = 20, 40
    vg = float(ctx.glob.get("vignette", 0) or 0)
    vig = render.vignette_mask(ctx, vg) if vg > 0 else None
    fade = float(ctx.glob.get("fade_out", 0) or 0)
    L = render.visual_layers(ctx, project)
    try:
        buf = np.zeros((ctx.H, ctx.W, 3), np.float32)
        serial = [render.draw_frame(ctx, L, buf, (n0 + i) / ctx.fps, False, vig, fade).tobytes() for i in range(N)]
    finally:
        for k in L:
            k.release()
    L = render.visual_layers(ctx, project)
    got = []
    try:
        render.render_frames(ctx, L, N, n0, vig, fade, got.append, lambda x: None, threads=5)
    finally:
        for k in L:
            k.release()
    assert len(got) == N and got == serial


# ------------------------------------------------------------ encoder option
def test_encoder_option_resolution():
    import exports
    p = {"width": 1920, "height": 1080}
    assert exports.resolve_options({"preset": "youtube"}, p)["encoder"] == "software"
    o = exports.resolve_options({"preset": "fast"}, p)
    assert o["encoder"] == "auto" and "--encoder" in exports.cli_args(o, p, "/x.mp4")
    assert "--encoder" not in exports.cli_args(exports.resolve_options({"preset": "youtube"}, p), p, "/x.mp4")
    assert exports.resolve_options({"preset": "prores", "options": {"encoder": "hardware"}}, p)["encoder"] == "software"
    with pytest.raises(exports.OptionError):
        exports.resolve_options({"options": {"encoder": "gpu"}}, p)
    pre = exports.presets_payload()
    assert "hw_encoder" in pre["fast"] and "hw_encoder" not in pre["youtube"]


def test_hardware_export_decodes(project_dir, tmp_path):
    if not media_tools.hw_encoders()["h264"]:
        pytest.skip("no hardware H.264 encoder here")
    out = str(tmp_path / "hw.mp4")
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "render.py"),
                        project_dir, "--draft", "--encoder", "hardware", "--from", "0", "--to", "2", "--output", out],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    j = media_tools.probe_json(out, "-show_streams")
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p" and abs(float(j["format"]["duration"]) - 2) < 0.2
