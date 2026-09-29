"""examples/showcase: a 26 s, 16:9 keynote-style piece built from the generated media. Run via generate_all.py."""
import os

from _common import ROOT
from _proj import *      # noqa: F401,F403
import shapes

OUT = os.path.join(ROOT, "examples", "showcase")
VID = "../../templates/_media/video/"
SFX = "../../templates/_media/sfx/"
MUS = "../../templates/_media/audio/"
W, H, DUR = 1920, 1080, 26.0
CX = W / 2


def build():
    shapes.rrect(os.path.join(OUT, "media/bar.png"), 6, 96, 3, (126, 231, 255, 255))
    shapes.rrect(os.path.join(OUT, "media/rule.png"), 120, 2, 1, (255, 255, 255, 150))
    shapes.logo_mark(os.path.join(OUT, "media/logo.png"), 200)
    P = base_project("Kurgu showcase", W, H, DUR, vignette=0.3, grain=1.2, fade_out=1.4)
    L = []                                   # front-most first is applied at the end; we append back-to-front here

    # ---------------------------------------------------------------- picture (back)
    a = video("aurora", "Aurora", VID + "aurora.mp4", 0, 5.6, effects=[{"type": "brightness", "enabled": True, "value": -0.24}], use=[
        {"preset": "cam.ken-burns", "at": "in", "params": {"from": {"scale": 1.02, "x": 24}, "to": {"scale": 1.14, "x": -24}}}])
    b = video("ink", "Ink", VID + "ink.mp4", 5.0, 10.6, fade_in=0.5, src_in=1.0, effects=[{"type": "brightness", "enabled": True, "value": -0.26}],
              use=[{"preset": "cam.punch", "at": 5.0, "params": {"amount": 1.09, "settle": 0.9, "hold": 0.0}}])
    c = video("bokeh", "City bokeh", VID + "bokeh_city.mp4", 10.0, 15.6, fade_in=0.6, effects=[{"type": "brightness", "enabled": True, "value": -0.05}],
              use=[{"preset": "cam.ken-burns", "at": "in", "params": {"from": {"scale": 1.0, "y": 10}, "to": {"scale": 1.16, "y": -30}}}])
    d = video("dusk", "Dusk gradient", VID + "flow_dusk.mp4", 15.0, 20.6, fade_in=0.7, src_in=1.5,
              use=[{"preset": "cam.ken-burns", "at": "in", "params": {"from": {"scale": 1.0}, "to": {"scale": 1.08}}}])
    e = video("rings", "Rings", VID + "geo_rings.mp4", 20.0, DUR, fade_in=0.7, src_in=0.5, effects=[{"type": "brightness", "enabled": True, "value": -0.3}])
    pics = [e, d, c, b, a]                   # later scenes are in front so their fade-in crosses over the previous

    # ---------------------------------------------------------------- scene 1: title
    eyebrow = text("s1_eyebrow", "Eyebrow", "K U R G U", 0.6, 4.9, CX, 372, 30, "inter-semibold", CYAN, ls=0.35)
    enter(eyebrow, dist=14, dur=0.9, blur=6)
    leave(eyebrow, 0.5)
    t1 = text("s1_t1", "Title 1", "Describe it.", 1.0, 4.9, CX, 520, 150, "inter-bold", WHITE, ls=-0.02)
    enter(t1, 60, 1.1, blur=18)
    leave(t1, 0.5)
    t2 = text("s1_t2", "Title 2", "Watch it cut.", 1.8, 4.9, CX, 690, 150, "inter-bold", "#d2c4ff", ls=-0.02)
    enter(t2, 60, 1.1, blur=18)
    leave(t2, 0.5)

    # ---------------------------------------------------------------- scene 2: motion list + lower third
    lines = [("Keyframes.", 5.7), ("Easing.", 6.5), ("Camera moves.", 7.3)]
    s2 = []
    for i, (txt, t0) in enumerate(lines):
        l = text(f"s2_l{i + 1}", f"List {i + 1}", txt, t0, 9.7, CX, 400 + i * 150, 118, "inter-bold", WHITE, ls=-0.02)
        add_keys(l, "scale", [K(0, 0.9, "out-back"), K(0.8, 1)])
        add_keys(l, "opacity", [K(0, 0, "out-quad"), K(0.4, 1)])
        add_blur(l)
        add_keys(l, "fx.bl.radius", [K(0, 16, "out-cubic"), K(0.6, 0)])
        if i < len(lines) - 1:
            dim(l, lines[i + 1][1] - t0 + 0.05, 0.28, 0.5)
        add_keys(l, "opacity", [K(9.7 - t0 - 0.4, l["keys"]["opacity"][-1]["v"], "in-quad"), K(9.7 - t0, 0)])
        s2.append(l)
    # lower third (left aligned)
    lx = 170
    lt1, lt2 = "Made in one prompt", "Type, motion and camera are plain JSON."
    bar = image("s2_bar", "LT bar", "media/bar.png", 8.2, 9.7, lx, 934, "Lower third", scale_y=0.0, anchor_y=0.5)
    add_keys(bar, "scale_y", [K(0, 0.0, "out-expo"), K(0.6, 1)])
    add_keys(bar, "opacity", [K(9.7 - 8.2 - 0.35, 1), K(9.7 - 8.2, 0)])
    lt_a = text("s2_lt1", "LT title", lt1, 8.35, 9.7, lx + 28 + text_width(lt1, "inter-semibold", 46) / 2, 902, 46, "inter-semibold", WHITE, "Lower third", align="left")
    enter(lt_a, 0, 0.7, blur=8)
    lt_a["x"] = r(lt_a["x"])
    add_keys(lt_a, "x", [K(0, lt_a["x"] - 40, "out-expo"), K(0.8, lt_a["x"])])
    leave(lt_a, 0.35, 0, 0)
    lt_b = text("s2_lt2", "LT subtitle", lt2, 8.5, 9.7, lx + 28 + text_width(lt2, "inter-regular", 30) / 2, 964, 30, "inter-regular", GREY, "Lower third", align="left")
    enter(lt_b, 0, 0.7, blur=6)
    add_keys(lt_b, "x", [K(0, lt_b["x"] - 40, "out-expo"), K(0.8, lt_b["x"])])
    leave(lt_b, 0.35, 0, 0)

    # ---------------------------------------------------------------- scene 3: counter beat
    e3 = text("s3_eyebrow", "Eyebrow", "P R I V A T E   B Y   D E S I G N", 10.4, 14.7, CX, 290, 28, "inter-semibold", CYAN, ls=0.28)
    enter(e3, 14, 0.9, blur=6)
    leave(e3, 0.4)
    t_count0, t_count1 = 10.6, 12.2
    steps = list(range(0, 101, 10))
    times = [t_count0 + (t_count1 - t_count0) * (1 - (1 - i / 10) ** (1 / 3)) for i in range(11)]
    counters = []
    for i, v in enumerate(steps):
        ta = times[i] if i else t_count0
        tb = times[i + 1] if i < 10 else 14.7
        c_ = text(f"s3_n{v}", f"Counter {v}", f"{v}%", ta, tb, CX, 560, 300, "jetbrains-mono-bold", WHITE, "Counter", shadow=0.3)
        if v == 0:
            add_keys(c_, "opacity", [K(0, 0, "out-quad"), K(0.25, 1)])
        if v == 100:
            add_keys(c_, "scale", [K(0, 1.10, "out-expo"), K(0.6, 1, "in-out-sine")])
            leave(c_, 0.4)
            c_["color"] = CYAN
        counters.append(c_)
    e3b = text("s3_sub", "Subline", "Runs on 127.0.0.1. Nothing leaves your machine.", t_count1, 14.7, CX, 800, 38, "inter-regular", GREY)
    enter(e3b, 20, 0.9, blur=6)
    leave(e3b, 0.4)

    # ---------------------------------------------------------------- scene 4: quote card
    q_mark = text("s4_mark", "Quote mark", "“", 15.3, 19.6, CX, 350, 520, "cormorant-garamond-bold", WHITE, "Quote", shadow=0, opacity=0.14)
    add_keys(q_mark, "opacity", [K(0, 0, "out-quad"), K(1.0, 0.14), K(19.6 - 15.3 - 0.4, 0.14, "in-quad"), K(19.6 - 15.3, 0)])
    add_keys(q_mark, "y", [K(0, 390, "out-cubic"), K(1.4, 350)])
    quote = [("A video is a list", 15.7, WHITE), ("of decisions.", 16.25, WHITE), ("Write them down.", 17.0, "#ffd9c7")]
    qs = []
    for i, (txt, t0, col) in enumerate(quote):
        q = text(f"s4_q{i + 1}", f"Quote line {i + 1}", txt, t0, 19.6, CX, 445 + i * 128, 108, "cormorant-garamond-italic", col, "Quote", lh=1.1)
        enter(q, 34, 1.1, blur=12, ease="out-quart")
        leave(q, 0.45, -18, 8)
        qs.append(q)
    rule = image("s4_rule", "Rule", "media/rule.png", 17.8, 19.6, CX, 850, "Quote")
    add_keys(rule, "scale_x", [K(0, 0, "out-expo"), K(0.7, 1)])
    add_keys(rule, "opacity", [K(19.6 - 17.8 - 0.4, 1), K(19.6 - 17.8, 0)])
    attr = text("s4_attr", "Attribution", "H O W   K U R G U   T H I N K S", 18.0, 19.6, CX, 902, 24, "inter-medium", GREY, "Quote", ls=0.3)
    enter(attr, 10, 0.8, blur=4)
    leave(attr, 0.4, -10, 0)

    # ---------------------------------------------------------------- scene 5: end card
    logo = image("s5_logo", "Logo mark", "media/logo.png", 20.5, DUR, CX, 400, "End card")
    add_keys(logo, "scale", [K(0, 0.6, "out-back"), K(0.9, 1)])
    add_keys(logo, "opacity", [K(0, 0, "out-quad"), K(0.4, 1)])
    add_keys(logo, "rotation", [K(0, -12, "out-expo"), K(1.2, 0)])
    end = text("s5_title", "End title", "Made with Kurgu", 20.9, DUR, CX, 600, 128, "inter-bold", WHITE, "End card", ls=-0.02)
    enter(end, 40, 1.1, blur=16)
    end2 = text("s5_sub", "End subtitle", "Open source. Runs on your machine. Written by an agent, refined by you.", 21.6, DUR, CX, 745, 34,
                "inter-regular", "#b4b4ba", "End card", shadow=0.7)
    enter(end2, 18, 1.0, blur=6)

    ui = [end2, end, logo, attr, rule] + qs[::-1] + [q_mark] + [e3b] + counters[::-1] + [e3] + [lt_b, lt_a, bar] + s2[::-1] + [t2, t1, eyebrow]
    # audio (back of the list)
    music = audio("music", "Cinematic pad", MUS + "cinematic_pad.m4a", 0, DUR, "Music", volume_db=-2, fade_in=1.6, fade_out=2.0,
                  envelope=[[0, 0], [15.4, 0], [16.2, -5], [19.2, -5], [20.2, 0]])
    fx = [
        ("sfx_whoosh1", "Whoosh (title)", "whoosh", 0.75, -8),
        ("sfx_riser", "Riser", "riser", 2.4, -9),
        ("sfx_hit1", "Hit (cut 1)", "hit", 5.0, -6),
        ("sfx_click1", "Click (list 1)", "click", 5.7, -14),
        ("sfx_click2", "Click (list 2)", "click", 6.5, -14),
        ("sfx_click3", "Click (list 3)", "click", 7.3, -14),
        ("sfx_whoosh2", "Whoosh (lower third)", "whoosh", 8.05, -12),
        ("sfx_whoosh3", "Whoosh (cut 2)", "whoosh", 9.5, -10),
    ] + [(f"sfx_tick{v}", f"Tick {v}", "tick", times[i] if i else t_count0, -17) for i, v in enumerate(steps)] + [
        ("sfx_pop", "Pop (100%)", "pop", times[10], -9),
        ("sfx_whoosh4", "Whoosh (cut 3)", "whoosh", 14.5, -10),
        ("sfx_shimmer", "Shimmer (end card)", "shimmer", 20.5, -10),
        ("sfx_hit2", "Hit (end)", "hit", 20.0, -8),
    ]
    sfx = [audio(i, n, SFX + f + ".wav", t, t + {"whoosh": 1.0, "riser": 2.6, "hit": 1.6, "click": 0.09, "pop": 0.3, "tick": 0.06, "shimmer": 2.6}[f],
                 "SFX", volume_db=db) for i, n, f, t, db in fx]
    P["layers"] = ui + pics + sfx[::-1] + [music]
    finish(P, os.path.join(OUT, "project.json"))


if __name__ == "__main__":
    build()
