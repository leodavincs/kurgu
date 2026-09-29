"""Helpers to write project.json files from code: layer factories, animation key helpers, text measuring, saving."""
import json
import os
import sys

from PIL import ImageFont

from _common import ROOT

sys.path.insert(0, ROOT)
import fonts   # noqa: E402
import schema  # noqa: E402

SFX_LEN = {"whoosh": 1.0, "riser": 2.6, "hit": 1.6, "click": 0.09, "pop": 0.3, "tick": 0.06, "shimmer": 2.6}
WHITE, GREY, DIM, CYAN, VIOLET, INK = "#f5f5f7", "#a1a1a6", "#6e6e73", "#7ee7ff", "#a78bfa", "#1d1d1f"


def r(x, n=4):
    x = round(float(x), n)
    return int(x) if x == int(x) else x


def text_width(text, font_id, size, letter_spacing=0.0):
    face = fonts.get_face(font_id)
    f = ImageFont.truetype(face["file"], int(size), index=face.get("index", 0))
    lines = text.split("\n")
    if letter_spacing:
        w = [sum(f.getlength(c) for c in l) + size * letter_spacing * max(len(l) - 1, 0) for l in lines]
    else:
        w = [f.getlength(l) for l in lines]
    return max(w)


class Ctx:
    """Canvas size + a running list of layers (front-most first, as in project.json)."""

    def __init__(self, W, H, fps=30):
        self.W, self.H, self.fps = W, H, fps
        self.layers = []

    def add(self, layer):
        self.layers.append(layer)
        return layer

    def ordered(self):
        return self.layers


def K(t, v, ease=None):
    d = {"t": r(t), "v": r(v)}
    if ease:
        d["ease"] = ease
    return d


def add_keys(layer, prop, ks):
    cur = layer.setdefault("keys", {}).setdefault(prop, [])
    cur.extend(ks)
    cur.sort(key=lambda k: k["t"])


def add_blur(layer):
    for e in layer.setdefault("effects", []):
        if e.get("id") == "bl":
            return
    layer["effects"].append({"type": "blur", "id": "bl", "radius": 0})


def text(id, name, txt, start, end, x, y, size, font="inter-regular", color=WHITE, group="Titles", ls=0.0, lh=1.15,
         align="center", shadow=0.35, opacity=None, **extra):
    l = {"id": id, "name": name, "type": "text", "group": group, "start": r(start), "end": r(end), "x": r(x), "y": r(y),
         "text": txt, "font": font, "size": size, "color": color, "line_height": lh, "align": align,
         "shadow": {"enabled": shadow > 0, "blur": 22, "opacity": shadow}}
    if ls:
        l["letter_spacing"] = ls
    if opacity is not None:
        l["opacity"] = opacity
    l.update(extra)
    return l


def image(id, name, src, start, end, x, y, group="Shapes", scale=1.0, fit="native", **extra):
    l = {"id": id, "name": name, "type": "image", "group": group, "src": src, "fit": fit, "start": r(start), "end": r(end),
         "x": r(x), "y": r(y)}
    if scale != 1.0:
        l["scale"] = scale
    l.update(extra)
    return l


def video(id, name, src, start, end, group="Picture", src_in=0, fit="cover", **extra):
    l = {"id": id, "name": name, "type": "video", "group": group, "src": src, "src_in": src_in, "start": r(start),
         "end": r(end), "fit": fit, "muted": True}
    l.update(extra)
    return l


def audio(id, name, src, start, end, group="Sound", volume_db=0, src_in=0, **extra):
    l = {"id": id, "name": name, "type": "audio", "group": group, "src": src, "src_in": src_in, "start": r(start),
         "end": r(end), "volume_db": volume_db}
    l.update(extra)
    return l


# ------------------------------------------------------------------ animation recipes (times are layer-local)
def enter(layer, dist=44, dur=0.9, delay=0.0, blur=14, ease="out-expo", opacity_dur=0.55):
    """Rise + fade + blur-in."""
    y = layer.get("y")
    add_keys(layer, "y", [K(delay, y + dist, ease), K(delay + dur, y)])
    add_keys(layer, "opacity", [K(delay, 0, "out-quad"), K(delay + opacity_dur, 1)])
    if blur:
        add_blur(layer)
        add_keys(layer, "fx.bl.radius", [K(delay, blur, "out-cubic"), K(delay + dur * 0.8, 0)])
    return layer


def pop(layer, frm=0.86, dur=0.7, delay=0.0, ease="out-back", opacity_dur=0.3):
    add_keys(layer, "scale", [K(delay, frm, ease), K(delay + dur, 1)])
    add_keys(layer, "opacity", [K(delay, 0, "out-quad"), K(delay + opacity_dur, 1)])
    return layer


def leave(layer, dur=0.4, dist=-26, blur=10, ease="in-cubic", keep_y=True):
    d = layer["end"] - layer["start"]
    t0 = d - dur
    y = layer.get("y")
    if keep_y and y is not None:
        add_keys(layer, "y", [K(t0, y, ease), K(d, y + dist)])
    add_keys(layer, "opacity", [K(t0, 1, "in-quad"), K(d, 0)])
    if blur:
        add_blur(layer)
        add_keys(layer, "fx.bl.radius", [K(t0, 0, "in-cubic"), K(d, blur)])
    return layer


def dim(layer, at, to=0.3, dur=0.5):
    add_keys(layer, "opacity", [K(at, 1, "in-out-sine"), K(at + dur, to)])
    return layer


def base_project(name, W, H, duration, fps=30, vignette=0.25, grain=1.0, lufs=-14, fade_out=1.0, output="output.mp4", bg="#000000"):
    return {"version": 2, "name": name, "width": W, "height": H, "fps": fps, "duration": duration, "background": bg,
            "output": output,
            "global": {"grain": grain, "vignette": vignette, "loudness_lufs": lufs, "fade_out": fade_out},
            "layers": [], "notes": []}


def finish(project, path):
    """Bake presets, stamp `requires`, validate, write."""
    import presets
    project["layers"] = [presets.bake_layer(l, project["width"], project["height"]) if "use" in l else l for l in project["layers"]]
    schema.stamp_requires(project)
    probs = schema.validate(project, font_ids=[f["id"] for f in fonts.bundled_faces()])
    if probs:
        raise SystemExit("invalid project " + path + ":\n  " + "\n  ".join(probs))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(project, indent=2, ensure_ascii=False) + "\n")
    return path


def sfx(id, name, kind, t, db, prefix="../_media/sfx/"):
    return audio(id, name, f"{prefix}{kind}.wav", t, t + SFX_LEN[kind], "SFX", volume_db=db)


def slide(layer, dx, dur=0.8, delay=0.0, ease="out-expo"):
    x = layer["x"]
    add_keys(layer, "x", [K(delay, x - dx, ease), K(delay + dur, x)])
    return layer
