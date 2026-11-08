"""Audio block of a note (audio_facts) and the fallback layer box (agent_notes._box_of)."""
import json
import os
import subprocess
import time

import pytest

import agent_notes
import audio_facts
import media_tools
import schema

pytestmark = pytest.mark.skipif(not (media_tools.find("ffmpeg") and media_tools.find("ffprobe")), reason="ffmpeg not available")


def ff(*args):
    subprocess.run([media_tools.ffmpeg(), "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("proj")
    # 6 s: 0-2 tone, 2-3 silence, 3-6 tone
    ff("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1",
       "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
       "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1[a]", "-map", "[a]", str(d / "tone.wav"))
    ff("-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=6", "-i", str(d / "tone.wav"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-shortest", str(d / "clip.mp4"))
    ff("-f", "lavfi", "-i", "color=c=red:s=400x400:d=1", "-frames:v", "1", str(d / "logo.png"))
    (d / "bad.wav").write_bytes(b"not audio at all")
    return str(d)


def proj(layers):
    return {"width": 1920, "height": 1080, "layers": layers}


def test_silence_maps_to_project_time(media):
    # audio layer placed at 10 s: source 0-6 s plays at 10-16 s; the gap 2-3 s must show as 12-13
    p = proj([{"id": "a1", "type": "audio", "src": "tone.wav", "start": 10, "end": 16, "src_in": 0}])
    blk = audio_facts.audio_block(media, p, 12.5)
    assert len(blk) == 1 and "error" not in blk[0]
    e = blk[0]
    assert e["window"] == [11.0, 14.0]
    (a, b), = e["silences"]
    assert a == pytest.approx(12.0, abs=0.1) and b == pytest.approx(13.0, abs=0.1)
    assert -20 < e["peak_db"] < -16     # ffmpeg sine is 1/8 full scale = -18 dBFS
    assert 0 < 1 and e["rms_db"] < e["peak_db"]


def test_src_in_offset(media):
    # video starts at 5 s, src_in 1: source time 2-3 (the gap) plays at 6-7
    p = proj([{"id": "v1", "type": "video", "src": "clip.mp4", "start": 5, "end": 10, "src_in": 1}])
    e, = audio_facts.audio_block(media, p, 6.5)
    (a, b), = e["silences"]
    assert a == pytest.approx(6.0, abs=0.15) and b == pytest.approx(7.0, abs=0.15)


def test_gain_and_muted(media):
    p = proj([{"id": "a1", "type": "audio", "src": "tone.wav", "start": 0, "end": 6, "volume_db": -6, "envelope": [[0, 0], [6, -6]]},
              {"id": "a2", "type": "audio", "src": "tone.wav", "start": 0, "end": 6, "muted": True}])
    base = audio_facts.audio_block(media, proj([{"id": "a", "type": "audio", "src": "tone.wav", "start": 0, "end": 6}]), 4)[0]
    blk = audio_facts.audio_block(media, p, 4)
    assert len(blk) == 1 and blk[0]["gain_db"] == pytest.approx(-10, abs=0.1)
    assert blk[0]["peak_db"] == pytest.approx(base["peak_db"] - 10, abs=0.2)


def test_missing_and_corrupt_degrade(media):
    p = proj([{"id": "m", "type": "audio", "src": "nope.wav", "start": 0, "end": 6},
              {"id": "b", "type": "audio", "src": "bad.wav", "start": 0, "end": 6},
              {"id": "ok", "type": "audio", "src": "tone.wav", "start": 0, "end": 6}])
    blk = {e["id"]: e for e in audio_facts.audio_block(media, p, 3)}
    assert blk["m"]["error"] and blk["b"]["error"] and "peak_db" in blk["ok"]


def test_cache_and_speed(media):
    p = proj([{"id": "v", "type": "video", "src": "clip.mp4", "start": 0, "end": 6}])
    t0 = time.time()
    audio_facts.audio_block(media, p, 1.7)
    first = time.time() - t0
    t0 = time.time()
    audio_facts.audio_block(media, p, 1.7)
    assert time.time() - t0 < 0.05 and first < 2.0           # second call is served from the cache, no ffmpeg


def test_words_from_existing_transcript(media):
    import transcribe
    path = os.path.realpath(os.path.join(media, "tone.wav"))
    cf = transcribe.cache_file(media, transcribe.cache_key(path, "small", None, 0.0, None))
    os.makedirs(os.path.dirname(cf), exist_ok=True)
    with open(cf, "w") as f:
        json.dump({"segments": [{"start": 1.0, "end": 2.0, "text": "hello there", "words": [
            {"start": 1.0, "end": 1.4, "word": "hello"}, {"start": 1.5, "end": 2.0, "word": "there"},
            {"start": 5.5, "end": 5.9, "word": "far"}]}]}, f)
    p = proj([{"id": "a1", "type": "audio", "src": "tone.wav", "start": 10, "end": 16}])
    e, = audio_facts.audio_block(media, p, 11.5)
    assert e["words"] == [[11.0, 11.4, "hello"], [11.5, 12.0, "there"]]


def test_notes_md_line(media):
    p = {"name": "t", "width": 1920, "height": 1080, "layers": [{"id": "a1", "type": "audio", "src": "tone.wav", "start": 0, "end": 6, "name": "Music"}],
         "notes": [{"id": "n1", "t": 2.5, "text": "gap here", "sent": "2026-11-07T10:00:00"}]}
    with open(os.path.join(media, "project.json"), "w") as f:
        json.dump(p, f)
    norm = schema.normalize(p)
    assert "audio" not in agent_notes.note_view(media, norm["notes"][0], norm)       # nothing measured before a send
    agent_notes.append_inbox(media, norm, norm["notes"], "2026-11-07T10:00:00")
    md = open(agent_notes.write_notes_md(media, norm), encoding="utf-8").read()
    assert "- audio: Music (audio)" in md and "silent" in md
    assert agent_notes.note_view(media, norm["notes"][0], norm)["audio"][0]["id"] == "a1"


def test_box_of_native_size(media):
    logo = {"id": "l", "type": "image", "src": "logo.png", "fit": "native", "scale": 0.4, "x": 300, "y": 200}
    x0, y0, x1, y1 = agent_notes._box_of(logo, 1920, 1080, 1.0, media)
    assert (x1 - x0, y1 - y0) == pytest.approx((160, 160)) and (x0, y0) == pytest.approx((220, 120))
    e = agent_notes.estimate_bounds(logo, 1920, 1080, 1.0, media)
    assert "approx" not in e and e["w"] == 160 and "measured by Kurgu" in e["anchor"]                 # the file's real size: measured, not an estimate
    assert agent_notes.estimate_bounds(logo, 1920, 1080, 1.0, None)["approx"] is True                  # no project folder, no native size: a real estimate
    # contain (the default for images) fits it into the frame first, like the renderer
    x0, y0, x1, y1 = agent_notes._box_of(dict(logo, fit="contain"), 1920, 1080, 1.0, media)
    assert x1 - x0 == pytest.approx(432)


def test_box_of_keyframes(media):
    logo = {"id": "l", "type": "image", "src": "logo.png", "fit": "native", "start": 2, "x": 300, "y": 200,
            "keys": {"scale": [{"t": 0, "v": 0.2}, {"t": 2, "v": 0.6}]}}
    x0, y0, x1, y1 = agent_notes._box_of(logo, 1920, 1080, 3.0, media)      # layer-local 1 s -> scale 0.4
    assert x1 - x0 == pytest.approx(160)
    rot = dict(logo, keys={"rotation": [{"t": 0, "v": 45}], "scale": [{"t": 0, "v": 0.5}]})
    x0, y0, x1, y1 = agent_notes._box_of(rot, 1920, 1080, 2.0, media)
    assert x1 - x0 == pytest.approx(200 * 2 ** 0.5, abs=0.01)


def test_text_box_uses_font_metrics():
    t = {"id": "t", "type": "text", "text": "Hello\nWorld wide", "size": 60, "x": 960, "y": 540}
    x0, y0, x1, y1 = agent_notes._box_of(t, 1920, 1080)
    assert y1 - y0 == pytest.approx(2 * 60 * 1.25) and 100 < x1 - x0 < 700
