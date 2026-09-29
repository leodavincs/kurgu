"""Presets API (GET /api/presets, POST /api/presets/apply) and the `requires` stamp on PUT /api/project."""
import json
import os

import pytest

import anim
import presets
from test_server import Client, srv  # noqa: F401  (fixture)


def _layer(d, lid):
    proj = json.load(open(os.path.join(d, "project.json"), encoding="utf-8"))
    return [l for l in proj["layers"] if l["id"] == lid][0], proj


def test_presets_manifest_lists_groups_and_turkish_names(srv):
    c, _ = srv
    st, lst = c.req("GET", "/api/presets")
    assert st == 200
    ids = {p["id"]: p for p in lst}
    for i in ("cam.punch", "cam.ken-burns", "cam.shake", "in.fade-up", "in.slide-left", "in.slide-right", "in.scale-pop", "in.spin", "out.fade-down"):
        assert i in ids, i
    assert {p["group"] for p in lst} == {"in", "out", "emphasis", "camera"}
    assert all(p["name"] and p["name_tr"] for p in lst)


@pytest.mark.parametrize("pid", [p for p in presets.PRESETS])
def test_apply_every_preset_bakes_valid_keys_and_stamps_requires(srv, pid):
    c, d = srv
    lid = "k_logo"                                           # image layer 0.5 - 11.5 s (every preset applies to images)
    st, res = c.req("POST", "/api/presets/apply", {"layer_id": lid, "preset": pid, "at": 2.0 if pid.startswith(("cam.punch", "cam.shake", "emph")) else ("out" if pid.startswith("out") else "in")})
    assert st == 200 and res["project"] and len(res["version"]) == 40, res
    l, proj = _layer(d, lid)
    assert l.get("keys") or l.get("mods"), pid
    assert "use" not in l
    assert "keys" in proj.get("requires", []) or "mods" in proj.get("requires", [])
    # the baked layer evaluates without errors over its whole life
    for i in range(0, 21):
        for prop in (l.get("keys") or {}):
            anim.value(l, prop, l["start"] + (l["end"] - l["start"]) * i / 20 - l["start"])


def test_apply_preset_errors(srv):
    c, d = srv
    assert c.req("POST", "/api/presets/apply", {"layer_id": "k_buyuk", "preset": "nope"})[0] == 400
    assert c.req("POST", "/api/presets/apply", {"layer_id": "ghost", "preset": "in.fade-up"})[0] == 404
    assert c.req("POST", "/api/presets/apply", {"layer_id": "k_muzik", "preset": "in.fade-up"})[0] == 400      # audio layer: preset does not apply


def test_fade_up_in_layer_local_time(srv):
    c, d = srv
    l0, _ = _layer(d, "k_buyuk")
    c.req("POST", "/api/presets/apply", {"layer_id": "k_buyuk", "preset": "in.fade-up"})
    l, _ = _layer(d, "k_buyuk")
    assert l["keys"]["opacity"][0] == {"t": 0, "v": 0, "ease": "out-cubic"} and l["keys"]["opacity"][-1]["t"] == 0.5
    assert l["keys"]["y"][0]["v"] == l0["y"] + 60 and l["keys"]["y"][-1]["v"] == l0["y"]


def test_put_stamps_requires_from_the_keys_saved_by_the_browser(srv):
    c, d = srv
    st, data = c.req("GET", "/api/project")
    proj = data["project"]
    proj.pop("requires", None)
    proj["layers"][0]["keys"] = {"rotation": [{"t": 0, "v": 0}, {"t": 1, "v": 30, "ease": "ease-out"}]}
    st, res = c.req("PUT", "/api/project", {"project": proj, "summary": ["keys"], "base_version": data["version"]})
    assert st == 200
    _, saved = _layer(d, "k_buyuk")
    assert set(saved["requires"]) >= {"keys", "transform"}
    # and removes the stamp again when the motion is gone
    st, data = c.req("GET", "/api/project")
    proj = data["project"]
    del proj["layers"][0]["keys"]
    proj["layers"][0].pop("rotation", None)
    c.req("PUT", "/api/project", {"project": proj, "summary": ["keys"], "base_version": data["version"]})
    assert "requires" not in _layer(d, "k_buyuk")[1]
