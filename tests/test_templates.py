import json
import os
import subprocess
import sys

import pytest
from PIL import Image

import fonts
import schema
from conftest import ROOT

TPL = os.path.join(ROOT, "templates")
SCRIPTS = os.path.join(ROOT, "scripts", "examples")
INDEX = json.load(open(os.path.join(TPL, "index.json"), encoding="utf-8"))["templates"]
DIRS = [os.path.join(TPL, t["id"]) for t in INDEX] + [os.path.join(ROOT, "examples", "showcase")]


def test_index_complete():
    assert len(INDEX) == 5
    for t in INDEX:
        for k in ("id", "name", "aspect", "duration", "thumbnail", "description"):
            assert t[k]
        assert t["name"]["en"] and t["name"]["tr"] and t["description"]["en"] and t["description"]["tr"]
        assert os.path.isfile(os.path.join(TPL, t["thumbnail"]))
        assert os.path.isfile(os.path.join(TPL, t["id"], "README.md"))


@pytest.mark.parametrize("d", DIRS, ids=os.path.basename)
def test_validates_and_media_exists(d):
    p = json.load(open(os.path.join(d, "project.json"), encoding="utf-8"))
    assert schema.validate(p, font_ids=[f["id"] for f in fonts.bundled_faces()]) == []
    for l in p["layers"]:
        if l.get("src"):
            assert os.path.isfile(os.path.normpath(os.path.join(d, l["src"]))), l["src"]
        assert p["duration"] >= l["end"] - 1e-6 or l["type"] == "audio"


@pytest.mark.parametrize("d", DIRS, ids=os.path.basename)
def test_renders_draft_frame(d, tmp_path):
    p = json.load(open(os.path.join(d, "project.json"), encoding="utf-8"))
    out = str(tmp_path / "f.png")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), d, "--frame", str(p["duration"] * 0.4), "--draft", "--output", out],
                       capture_output=True, text=True, timeout=110)
    assert r.returncode == 0, r.stdout + r.stderr
    w, h = Image.open(out).size
    assert abs(w - p["width"] / 2) <= 1 and abs(h - p["height"] / 2) <= 1


def test_new_project_is_self_contained(tmp_path):
    sys.path.insert(0, SCRIPTS)
    import new_project
    dest = str(tmp_path / "x")
    new_project.new_project("product-promo", dest)
    p = json.load(open(os.path.join(dest, "project.json"), encoding="utf-8"))
    for l in p["layers"]:
        if l.get("src"):
            assert not l["src"].startswith("..") and os.path.isfile(os.path.join(dest, l["src"]))
