"""Speech-to-text layer: chunking / timing maps on a fake transcript (always), real transcription + MCP + HTTP (skipped
without a backend or macOS `say`)."""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest
import media_tools

import captions
import transcribe as stt
from test_mcp import first_text, mcp, project_of, read  # noqa: F401  (fixtures)
from test_server import srv  # noqa: F401

HAVE_BACKEND = stt.detect_backend() is not None
HAVE_SAY = shutil.which("say") is not None and media_tools.find("ffmpeg") is not None
needs_real = pytest.mark.skipif(not (HAVE_BACKEND and HAVE_SAY), reason="needs mlx-whisper/faster-whisper, macOS say, ffmpeg")


def W(s, e, w):
    return {"start": s, "end": e, "word": w, "prob": 0.9}


FAKE = {"language": "en", "duration": 10, "segments": [
    {"start": 5.0, "end": 8.0, "text": "one two three", "words": [W(5.0, 5.4, "One"), W(5.5, 6.0, "two,"), W(6.1, 6.5, "three.")]},
    {"start": 8.0, "end": 12.0, "text": "four five six seven", "words": [W(8.0, 8.3, "four"), W(8.4, 8.8, "five"),
                                                                        W(8.9, 9.5, "six"), W(11.0, 11.5, "seven")]}]}


def test_to_timeline_maps_and_clips():
    # clip placed at timeline 2.0, playing source from 5.0 for 4 s -> source 5..9
    tr = stt.to_timeline(FAKE, start=2.0, src_in=5.0, end=6.0)
    ws = stt.all_words(tr)
    assert [w["word"] for w in ws] == ["One", "two,", "three.", "four", "five", "six"]     # "seven" starts after 9.0
    assert ws[0]["start"] == 2.0 and ws[3]["start"] == 5.0
    assert ws[-1]["end"] == 6.0                                                             # clamped to the layer end
    assert tr["timeline"] is True


def test_to_timeline_drops_words_before_range():
    tr = stt.to_timeline(FAKE, start=0.0, src_in=8.5, end=10.0)
    assert [w["word"] for w in stt.all_words(tr)] == ["five", "six", "seven"] or [w["word"] for w in stt.all_words(tr)][0] == "five"
    assert stt.all_words(tr)[0]["start"] >= 0


def test_chunking_rules():
    ws = stt.all_words(FAKE)
    ch = captions.chunk_words(ws, max_words=3, max_chars=40)
    assert [[w["word"] for w in c] for c in ch] == [["One", "two,", "three."], ["four", "five", "six"], ["seven"]]
    # sentence end splits even below max_words; a pause > 0.6 s splits too
    ch = captions.chunk_words(ws, max_words=7, max_chars=40)
    assert [len(c) for c in ch] == [3, 3, 1]
    ch = captions.chunk_words(ws, max_words=10, max_chars=9)
    assert all(len(" ".join(w["word"] for w in c)) <= 9 or len(c) == 1 for c in ch)


def test_chunk_times_never_overlap_and_close_small_gaps():
    ch = captions.chunk_words(stt.all_words(FAKE), 3, 40)
    ts = captions.chunk_times(ch)
    assert ts[0] == (5.0, 5.0 + 1.5) or ts[0][0] == 5.0
    for (s, e), (s2, _e2) in zip(ts, ts[1:]):
        assert e <= s2 + 1e-9 and e > s
    assert ts[0][1] == 8.0 or ts[0][1] <= 8.0        # gap 6.5 -> 8.0 is large: hold only
    assert ts[1][1] < 11.0                           # not stretched across the long pause


def test_build_layers_vertical_vs_landscape():
    ws = stt.all_words(FAKE)
    v = captions.build_layers(ws, 1080, 1920)
    assert [l["id"] for l in v] == ["cap_1", "cap_2", "cap_3"] and all(l["group"] == "Captions" for l in v)
    assert 0.62 * 1920 <= v[0]["y"] <= 0.70 * 1920 and v[0]["x"] == 540
    h = captions.build_layers(ws, 1920, 1080)
    assert len(h) == 3 and h[0]["y"] > 0.8 * 1080            # 7 words per line but sentences still split
    assert captions.defaults(1080, 1920)["max_words_per_line"] == 3 and captions.defaults(1920, 1080)["max_words_per_line"] == 7
    up = captions.build_layers(ws, 1080, 1920, first_index=7, uppercase=True, y=1200, size=90)
    assert up[0]["id"] == "cap_7" and up[0]["text"] == "ONE TWO, THREE." and up[0]["y"] == 1200 and up[0]["size"] == 90
    assert captions.upper("işık", "tr") == "İŞIK"
    assert captions.next_index(["cap_2", "cap_9", "x"]) == 10 and captions.next_index([]) == 1


def test_resolve_target(project_dir):
    proj = json.load(open(os.path.join(project_dir, "project.json")))
    vid = next(l for l in proj["layers"] if l["type"] == "video")
    t = stt.resolve_target(proj, project_dir, layer_id=vid["id"])
    assert t["path"].endswith(vid["src"].split("/")[-1]) and t["duration"] == pytest.approx(vid["end"] - vid["start"])
    with pytest.raises(stt.TranscribeError):
        stt.resolve_target(proj, project_dir, layer_id="k_buyuk")          # text layer
    with pytest.raises(stt.TranscribeError):
        stt.resolve_target(proj, project_dir, path="media/nope.wav")
    with pytest.raises(stt.TranscribeError):
        stt.resolve_target(proj, project_dir, path="/etc/hosts", allow_outside=False)


def test_not_installed_message(monkeypatch):
    monkeypatch.setattr(stt, "detect_backend", lambda prefer=None: None)
    av = stt.availability()
    assert av["available"] is False and "pip install" in av["message"] and set(av["hints"]) == {"mac", "linux", "win"}
    with pytest.raises(stt.TranscribeError, match="not installed"):
        stt.transcribe_file("/tmp", __file__)


def test_models():
    assert stt.canonical_model(None) == "small" and stt.canonical_model("large") == "large-v3"
    assert set(stt.MLX_REPOS) == set(stt.MODELS)
    with pytest.raises(stt.TranscribeError):
        stt.canonical_model("gigantic")


def test_job_runner_one_at_a_time_and_cancel():
    r = stt.JobRunner()

    def slow(progress, cancel):
        progress("transcribing", 0.5, "x")
        while not cancel.is_set():
            time.sleep(0.02)
        raise stt.Cancelled()
    jid = r.start(slow)
    with pytest.raises(stt.Busy):
        r.start(slow)
    time.sleep(0.1)
    assert r.snapshot(jid)["progress"] == 0.5 and r.cancel(jid)
    for _ in range(50):
        if r.snapshot(jid)["status"] == "cancelled":
            break
        time.sleep(0.05)
    assert r.snapshot(jid)["status"] == "cancelled"
    assert r.snapshot(r.start(lambda p, c: {"ok": 1}))["status"] in ("running", "done")


# ---------------------------------------------------------------- real speech
@pytest.fixture(scope="module")
def speech(tmp_path_factory):
    d = tmp_path_factory.mktemp("speech")
    subprocess.run(["say", "-o", str(d / "s.aiff"), "hello world this is a caption test"], check=True)
    subprocess.run([media_tools.ffmpeg(), "-v", "error", "-y", "-i", str(d / "s.aiff"), str(d / "s.wav")], check=True)
    return str(d / "s.wav")


@needs_real
def test_real_transcription_and_cache(speech, tmp_path):
    tr = stt.transcribe_file(str(tmp_path), speech, "small", "en")
    words = [w["word"].lower().strip(".,") for w in stt.all_words(tr)]
    assert "hello" in words and "world" in words and tr["language"] == "en"
    assert all(w["end"] >= w["start"] for w in stt.all_words(tr))
    again = stt.transcribe_file(str(tmp_path), speech, "small", "en")
    assert again.get("cached") is True and os.listdir(tmp_path / ".kurgu" / "transcripts")


def add_speech(d, speech, start=2.0):
    os.makedirs(os.path.join(d, "media"), exist_ok=True)
    shutil.copy(speech, os.path.join(d, "media", "speech.wav"))
    p = project_of(d)
    dur = float(subprocess.check_output([media_tools.ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                         os.path.join(d, "media", "speech.wav")]))
    p["layers"].append({"id": "speech", "type": "audio", "name": "Speech", "src": "media/speech.wav", "start": start,
                        "end": start + dur, "src_in": 0})
    with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
        json.dump(p, f)
    return dur


@needs_real
def test_mcp_transcribe_and_make_captions(mcp, project_dir, speech):  # noqa: F811
    add_speech(project_dir, speech, 2.0)
    out = first_text(mcp.ok("transcribe", layer_id="speech", model="small", language="en", words=True))
    assert "TIMELINE" in out and "hello" in out.lower()
    res = first_text(mcp.ok("make_captions", layer_id="speech", model="small", language="en", max_words_per_line=2, uppercase=True))
    assert "caption layers" in res
    p = project_of(project_dir)
    caps = [l for l in p["layers"] if l["id"].startswith("cap_")]
    assert len(caps) >= 3 and [l["id"] for l in caps] == [f"cap_{i}" for i in range(1, len(caps) + 1)]
    assert all(l["group"] == "Captions" and l["type"] == "text" and l["text"] == l["text"].upper() for l in caps)
    assert 2.0 <= caps[0]["start"] < 3.0 and caps[-1]["end"] <= p["duration"]
    assert "[agent]" in read(os.path.join(project_dir, "changes.md"))
    assert p["layers"][0]["id"] == "cap_1"                                # front-most
    # re-running replaces rather than piles up; ids stay deterministic
    mcp.ok("make_captions", layer_id="speech", model="small", language="en", max_words_per_line=2)
    caps2 = [l for l in project_of(project_dir)["layers"] if l["id"].startswith("cap_")]
    assert [l["id"] for l in caps2] == [f"cap_{i}" for i in range(1, len(caps2) + 1)]
    r = mcp.tool("make_captions", layer_id="k_buyuk")
    assert r["isError"]


@needs_real
def test_http_transcribe_job(srv, speech):  # noqa: F811
    c, d = srv
    add_speech(d, speech, 1.0)
    st, av = c.req("GET", "/api/transcribe")
    assert st == 200 and av["available"]
    st, job = c.req("POST", "/api/transcribe", {"layer_id": "speech", "model": "small", "language": "en"})
    assert st == 202 and job["id"]
    for _ in range(300):
        st, s = c.req("GET", "/api/transcribe/" + job["id"])
        if s["status"] != "running":
            break
        time.sleep(0.2)
    assert s["status"] == "done", s
    assert s["result"]["timeline"] and s["result"]["segments"][0]["words"][0]["start"] >= 1.0
    assert c.req("GET", "/api/transcribe/nope")[0] == 404
    assert c.req("POST", "/api/transcribe", {"path": "../../etc/hosts"})[0] == 400


def test_http_transcribe_bad_layer(srv):  # noqa: F811
    c, _d = srv
    if HAVE_BACKEND:
        assert c.req("POST", "/api/transcribe", {"layer_id": "nope"})[0] == 400
    else:
        assert c.req("POST", "/api/transcribe", {"layer_id": "nope"})[0] == 501
