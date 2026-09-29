"""Motion in schema / presets / render / MCP (the maths itself is in test_anim.py, browser parity in test_parity.py)."""
import copy
import json
import os
import subprocess
import sys

import numpy as np
import media_tools
import pytest
from PIL import Image

import anim
import presets
import schema
from test_mcp import Mcp, first_text, project_of      # noqa: F401  (fixtures below reuse the client)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def layer(**kw):
    d = {"id": "a", "type": "image", "src": "media/x.png", "start": 1, "end": 5}
    d.update(kw)
    return d


def proj(*layers):
    return {"version": 2, "width": 1920, "height": 1080, "duration": 10, "layers": list(layers)}


# ------------------------------------------------------------------ schema
def test_existing_layers_normalise_unchanged():
    n = schema.normalize(proj(layer()))
    l = n["layers"][0]
    assert "keys" not in l and "mods" not in l and "rotation" not in l and "requires" not in n


def test_keys_are_sorted_coerced_and_requires_stamped():
    n = schema.normalize(proj(layer(rotation="12,5", keys={"opacity": [{"t": 1, "v": 1}, {"t": "0", "v": "0", "ease": "out-quad"}, {"t": "x", "v": 1}],
                                                            "y": []},
                                    mods={"x": [{"type": "wiggle", "amp": "3"}]})))
    l = n["layers"][0]
    assert l["rotation"] == 12.5
    assert l["keys"] == {"opacity": [{"t": 0, "v": 0, "ease": "out-quad"}, {"t": 1, "v": 1}]}
    assert l["mods"]["x"][0]["amp"] == 3
    assert n["requires"] == ["keys", "mods", "transform"]


def problems(*layers, **top):
    p = proj(*layers)
    p.update(top)
    return schema.validate(p)


def test_validation_messages():
    assert problems(layer(keys={"opacity": [{"t": 0, "v": 0}, {"t": 1, "v": 1, "ease": "out-cubic"}]}, rotation=5)) == []
    probs = problems(layer(keys={"bogus": [{"t": 0, "v": 1}]}))
    assert any('keys "bogus" cannot be animated' in p and "opacity" in p for p in probs)
    assert any("times must not decrease" in p for p in problems(layer(keys={"x": [{"t": 2, "v": 1}, {"t": 1, "v": 1}]})))
    assert any("unknown ease" in p for p in problems(layer(keys={"x": [{"t": 0, "v": 1, "ease": "wobble"}, {"t": 1, "v": 2}]})))
    assert any("opacity keys must be between" in p for p in problems(layer(keys={"opacity": [{"t": 0, "v": 2}]})))
    assert any("needs numeric t" in p for p in problems(layer(keys={"x": [{"t": 0}]})))
    assert any("cannot be animated on a audio" in p for p in problems({"id": "s", "type": "audio", "src": "a.wav", "start": 0, "end": 2,
                                                                       "keys": {"x": [{"t": 0, "v": 1}]}}))
    assert problems({"id": "s", "type": "audio", "src": "a.wav", "start": 0, "end": 2, "keys": {"volume_db": [{"t": 0, "v": -20}, {"t": 1, "v": 0}]}}) == []
    # effect params need an effect id and a numeric param
    fx = {"type": "blur", "id": "b1", "radius": 4}
    assert problems(layer(effects=[fx], keys={"fx.b1.radius": [{"t": 0, "v": 9}, {"t": 1, "v": 0}]})) == []
    assert any('no effect with id "zz"' in p for p in problems(layer(effects=[fx], keys={"fx.zz.radius": [{"t": 0, "v": 1}]})))
    assert any('no numeric param "nope"' in p for p in problems(layer(effects=[fx], keys={"fx.b1.nope": [{"t": 0, "v": 1}]})))
    assert any("used twice" in p for p in problems(layer(effects=[fx, dict(fx)])))
    # mods
    assert any("type must be one of wiggle, noise" in p for p in problems(layer(mods={"x": [{"type": "shake"}]})))
    assert any("freq must be greater than 0" in p for p in problems(layer(mods={"x": [{"type": "wiggle", "freq": 0}]})))
    assert any("does not know feature" in p for p in problems(layer(), requires=["holograms"]))
    assert problems(layer(), requires=["keys"]) == []


def test_render_refuses_unknown_requires(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"version": 2, "layers": [], "requires": ["holograms"]}))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), str(tmp_path), "--frame", "0"], capture_output=True, text=True)
    assert r.returncode != 0 and "requires features this Kurgu build does not have: holograms" in r.stdout


# ------------------------------------------------------------------ presets
def test_punch_ken_burns_and_shake_bake_to_keys():
    l = layer(type="video", start=2, end=8, use=[
        {"preset": "cam.punch", "at": [3, 5], "params": {"amount": 1.1, "settle": 0.2}},
        {"preset": "cam.ken-burns", "params": {"to": {"scale": 1.2, "x": -60, "y": 0}}},
        {"preset": "cam.shake", "at": 6, "dur": 0.5, "params": {"decay": 5, "seed": 4}}])
    assert problems(l) == []
    b = presets.bake_layer(l)
    assert "use" not in b
    sc = {k["t"]: k["v"] for k in b["keys"]["scale"]}
    assert sc[0] == 1
    assert sc[1.18] == pytest.approx(sc[1] * 1.1, rel=1e-4)                       # punch 1.1x over 0.18 s at local t=1
    assert sc[3.18] == pytest.approx(sc[3] * 1.1, rel=1e-4) and sc[6] > sc[3]     # both punches sit on the drift
    assert b["keys"]["x"][0]["v"] == 960 and b["keys"]["x"][-1]["v"] == 900       # Ken Burns x offsets from the layer position
    assert "y" not in b["keys"]
    m = b["mods"]
    assert set(m) == {"x", "y", "rotation"} and m["x"][0]["t0"] == 4 and m["x"][0]["t1"] == 4.5 and m["x"][0]["seed"] == 4
    ov = [k for k in b["keys"]["scale"] if 4 <= k["t"] <= 4.5]                     # overscan ramp exists for video
    assert ov and max(k["v"] for k in ov) > 1.05
    # order of `use` does not matter
    l2 = copy.deepcopy(l)
    l2["use"].reverse()
    assert presets.bake_layer(l2) == b


def test_shake_on_text_has_no_overscan_and_is_deterministic():
    l = layer(type="text", text="HI", use=[{"preset": "cam.shake", "at": 2, "params": {"amp": 20}}])
    b = presets.bake_layer(l)
    assert "keys" not in b
    a1 = [anim.value(b, "x", t / 30, 960) for t in range(30, 60)]
    assert a1 == [anim.value(presets.bake_layer(l), "x", t / 30, 960) for t in range(30, 60)]
    assert max(a1) - min(a1) > 5 and anim.value(b, "x", 0.5, 960) == 960          # calm outside the window


def test_use_validation_and_normalize_bakes():
    assert any("unknown preset" in p and "cam.punch" in p for p in problems(layer(use=[{"preset": "cam.nope"}])))
    assert any('no param "zzz"' in p for p in problems(layer(use=[{"preset": "cam.punch", "params": {"zzz": 1}}])))
    assert any("at must be" in p for p in problems(layer(use=[{"preset": "cam.punch", "at": "soon"}])))
    n = schema.normalize(proj(layer(use=[{"preset": "cam.punch", "at": 2}])))
    assert "use" not in n["layers"][0] and n["layers"][0]["keys"]["scale"][0]["t"] == 1 and n["requires"] == ["keys"]


# ------------------------------------------------------------------ render.py
def make_project(tmp_path, layers, **top):
    pdir = tmp_path / "p"
    (pdir / "media").mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (200, 100), (255, 255, 255, 255)).save(pdir / "media" / "x.png")
    p = {"version": 2, "width": 320, "height": 180, "fps": 10, "duration": 4, "background": "#000000", "layers": layers}
    p.update(top)
    (pdir / "project.json").write_text(json.dumps(p))
    return str(pdir)


def frame(pdir, t, tmp_path):
    out = str(tmp_path / f"f{t}.png")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), pdir, "--frame", str(t), "--output", out], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def test_opacity_keys_and_static_equivalence(tmp_path):
    keyed = make_project(tmp_path, [layer(fit="native", scale=0.5, start=0, end=4, x=160, y=90,
                                          keys={"opacity": [{"t": 0, "v": 0}, {"t": 2, "v": 1}]})])
    assert frame(keyed, 0, tmp_path).max() == 0                                     # opacity 0 at t=0
    mid = frame(keyed, 1.0, tmp_path)
    assert mid[90, 160].tolist() == [128, 128, 128]                                  # half way, linear
    static = make_project(tmp_path, [layer(fit="native", scale=0.5, start=0, end=4, x=160, y=90, opacity=0.5)])
    assert np.array_equal(mid, frame(static, 1.0, tmp_path))                          # keyed == static pixel for pixel (same value, same path)


def test_transform_rotation_flip_anchor(tmp_path):
    def cover(pdir, t=0.5):
        f = frame(pdir, t, tmp_path)
        m = f.sum(axis=2) > 0
        ys, xs = np.nonzero(m)
        return m.sum(), xs.mean(), ys.mean(), xs.min(), xs.max(), ys.min(), ys.max()
    base = dict(fit="native", start=0, end=4, x=160, y=90)                         # 200x100 white rectangle
    a0 = cover(make_project(tmp_path, [layer(**base)]))
    assert a0[0] == 200 * 100 and abs(a0[1] - 160) < 0.6
    rot90 = cover(make_project(tmp_path, [layer(rotation=90, scale=0.8, **base)]))  # 80 wide x 160 tall, same centre
    assert rot90[0] == pytest.approx(80 * 160, rel=0.03) and (rot90[4] - rot90[3], rot90[6] - rot90[5]) == pytest.approx((79, 159), abs=2)
    assert abs(rot90[1] - 160) < 0.7 and abs(rot90[2] - 90) < 0.7
    flip = cover(make_project(tmp_path, [layer(scale_x=-1, **base)]))
    assert flip[0] == 200 * 100 and abs(flip[1] - 160) < 0.6
    stretch = cover(make_project(tmp_path, [layer(scale_x=0.5, scale_y=1.5, **base)]))
    assert stretch[0] == pytest.approx(100 * 150, rel=0.02)
    anchor = cover(make_project(tmp_path, [layer(anchor_x=0, anchor_y=0, **base)]))  # the top-left corner sits on (x, y)
    assert (anchor[3], anchor[5]) == (160, 90)
    assert anchor[4] == 319                                                        # clipped by the 320 px frame
    spin = make_project(tmp_path, [layer(anchor_x=0, anchor_y=0, keys={"rotation": [{"t": 0, "v": 0}, {"t": 4, "v": 90}]}, **base)])
    c = cover(spin, 2.0)                                                            # 45 deg about the corner: the shape leaves the original box
    assert c[0] > 0 and c[5] >= 90 - 1 and c[6] > 90 + 100 * 0.7 and c[3] >= 160 - 75


def test_animated_x_moves_the_layer(tmp_path):
    pdir = make_project(tmp_path, [layer(fit="native", start=0, end=4, x=100, y=90, keys={"x": [{"t": 0, "v": 100}, {"t": 2, "v": 200}]})])
    def cx(t):
        m = frame(pdir, t, tmp_path).sum(axis=2) > 0
        return np.nonzero(m)[1].mean()
    assert cx(0) == pytest.approx(100, abs=0.6) and cx(1) == pytest.approx(150, abs=0.6) and cx(3) == pytest.approx(200, abs=0.6)


def test_keyed_effect_and_volume_do_not_crash_and_change_output(tmp_path):
    pdir = make_project(tmp_path, [layer(fit="native", start=0, end=4, x=160, y=90, effects=[{"type": "brightness", "id": "br", "value": 0}],
                                         keys={"fx.br.value": [{"t": 0, "v": -1}, {"t": 2, "v": 0}]})])
    assert frame(pdir, 0.0, tmp_path).max() == 0 and frame(pdir, 2.0, tmp_path)[90, 160].tolist() == [255, 255, 255]


def test_keyed_volume_ramps_the_mix(tmp_path):
    pdir = make_project(tmp_path, [])
    subprocess.run([media_tools.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=4", os.path.join(pdir, "media", "s.wav")], check=True)
    p = json.load(open(os.path.join(pdir, "project.json")))
    p["global"] = {"loudness_lufs": None}
    p["layers"] = [{"id": "s", "type": "audio", "src": "media/s.wav", "start": 0, "end": 4,
                    "keys": {"volume_db": [{"t": 0, "v": -60}, {"t": 4, "v": 0}]}}]
    json.dump(p, open(os.path.join(pdir, "project.json"), "w"))
    out = str(tmp_path / "o.wav")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), pdir, "--codec", "wav", "--output", out], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    raw = subprocess.run([media_tools.ffmpeg(), "-v", "error", "-i", out, "-f", "f32le", "-ac", "1", "-ar", "8000", "pipe:1"], capture_output=True).stdout
    a = np.frombuffer(raw, np.float32)
    rms = lambda x: float(np.sqrt((x ** 2).mean()))
    assert rms(a[:4000]) < 0.001 and rms(a[-4000:]) > 0.04 and rms(a[-4000:]) > 100 * rms(a[:4000])   # -60 dB start, ~0 dB end


# ------------------------------------------------------------------ MCP
def test_mcp_accepts_keys_and_bakes_presets(project_dir):
    m = Mcp(project_dir)
    try:
        m.call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        m.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        msg = first_text(m.ok("edit_project", update=[{"id": "k_buyuk", "set": {
            "rotation": -4, "keys": {"opacity": [{"t": 0, "v": 0, "ease": "out-quad"}, {"t": 0.4, "v": 1}]},
            "use": [{"preset": "cam.punch", "at": 2.5}]}}], summary="animate title"))
        assert "Applied" in msg
        l = project_of(project_dir)["layers"][0]
        assert "use" not in l and l["rotation"] == -4 and l["keys"]["opacity"][1] == {"t": 0.4, "v": 1}
        assert l["keys"]["scale"][0]["t"] == 1.5                                    # timeline 2.5 s - layer start 1 s
        assert project_of(project_dir)["requires"] == ["keys", "transform"]
        bad = m.tool("edit_project", update=[{"id": "k_buyuk", "set": {"keys": {"opacity": [{"t": 1, "v": 1}, {"t": 0, "v": 0}]}}}])
        assert bad["isError"] and "times must not decrease" in first_text(bad["content"])
        bad = m.tool("edit_project", update=[{"id": "k_buyuk", "set": {"rotation": "left"}}])
        assert bad["isError"]
        bad = m.tool("edit_project", update=[{"id": "k_buyuk", "set": {"use": [{"preset": "cam.nope"}]}}])
        assert bad["isError"] and "cam.punch" in first_text(bad["content"])
    finally:
        m.close()
