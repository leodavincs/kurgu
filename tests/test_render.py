import json
import os
import subprocess
import sys

from PIL import Image

import media_tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER = os.path.join(ROOT, "render.py")


def run(*args):
    return subprocess.run([sys.executable, RENDER, *args], capture_output=True, text=True, timeout=110)


def probe(path):
    r = subprocess.run([media_tools.ffprobe(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                       capture_output=True, text=True)
    return json.loads(r.stdout)


def test_frame_full_and_draft_sizes(project_dir, tmp_path):
    png = str(tmp_path / "f.png")
    r = run(project_dir, "--frame", "2", "--output", png)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == f"DONE {png}"
    assert Image.open(png).size == (1920, 1080)
    r = run(project_dir, "--frame", "2", "--draft", "--output", png)
    assert r.returncode == 0 and Image.open(png).size == (960, 540)


def test_frame_default_output_and_content(project_dir):
    r = run(project_dir, "--frame", "5", "--draft")
    assert r.returncode == 0
    out = os.path.join(project_dir, ".kurgu", "frame.png")
    assert r.stdout.strip() == f"DONE {out}"
    im = Image.open(out).convert("RGB")
    assert im.getpixel((10, 270)) != (0, 0, 0)     # the colour bars are on screen


def test_unknown_font_warns_and_falls_back(project_dir, tmp_path):
    p = os.path.join(project_dir, "project.json")
    data = json.load(open(p, encoding="utf-8"))
    data["layers"][0]["font"] = "does-not-exist"
    json.dump(data, open(p, "w", encoding="utf-8"))
    r = run(project_dir, "--frame", "2", "--draft", "--output", str(tmp_path / "f.png"))
    assert r.returncode == 0 and "WARN font does-not-exist not found" in r.stdout


def test_missing_source_error(project_dir, tmp_path):
    os.remove(os.path.join(project_dir, "media", "a.mp4"))
    r = run(project_dir, "--frame", "1", "--output", str(tmp_path / "f.png"))
    assert r.returncode != 0
    line = r.stdout.strip().splitlines()[-1]
    assert line.startswith('ERROR layer "A clip" (k_a): file not found: ') and line.endswith("a.mp4")


def test_full_draft_render_has_duration_and_audio(project_dir):
    r = run(project_dir, "--draft")
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.strip().splitlines()
    out = os.path.join(project_dir, "output_draft.mp4")
    assert lines[-1] == f"DONE {out}"
    progress = [float(l.split()[1]) for l in lines if l.startswith("PROGRESS")]
    assert progress[0] == 0 and progress[-1] == 1 and progress == sorted(progress)
    info = probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"]) == (960, 540)
    assert abs(float(info["format"]["duration"]) - 12.0) < 0.15
    assert a["codec_name"] == "aac"
    assert not os.path.exists(os.path.join(project_dir, ".kurgu", "render_tmp"))
