"""templates/: five ready-to-edit projects + index.json + thumbnails. Run via generate_all.py (projects / thumbs)."""
import json
import os
import subprocess
import sys

from PIL import Image

from _common import ROOT
from _proj import *      # noqa: F401,F403
import shapes

TPL = os.path.join(ROOT, "templates")
V, A, S = "../_media/video/", "../_media/audio/", "../_media/sfx/"
ACCENT = "#7ee7ff"
YELLOW = "#ffe14d"


def _dir(id):
    return os.path.join(TPL, id)


def _save(id, P):
    finish(P, os.path.join(_dir(id), "project.json"))


# ====================================================================== (a) captions, vertical 9:16
CAP_WORDS = [["YOUR", "WORDS", "APPEAR"], ["HERE,", "ONE", "AT"], ["A", "TIME."], ["THE", "ACTIVE", "WORD"],
             ["POPS", "IN", "COLOUR."], ["SWAP", "THEM", "FOR"], ["YOUR", "TRANSCRIPT."]]


def captions_vertical():
    id, W, H, D = "captions-vertical", 1080, 1920, 12.0
    d = _dir(id)
    shapes.vgradient(d + "/media/shade.png", 1080, 1000, (0, 0, 0, 0), (0, 0, 0, 220), gamma=1.5)
    shapes.rrect(d + "/media/track.png", 960, 8, 4, (255, 255, 255, 70))
    shapes.rrect(d + "/media/fill.png", 960, 8, 4, (255, 255, 255, 255))
    shapes.rrect(d + "/media/pill.png", 470, 96, 48, (255, 225, 77, 255))
    P = base_project("Captions (vertical 9:16)", W, H, D, vignette=0.3, grain=1.0, fade_out=0.6)
    words = [w for c in CAP_WORDS for w in c]
    t0, step = 2.8, 0.45
    caps, hl = [], []
    size, y, font = 100, 0.66 * H, "inter-bold"
    n = 0
    for ci, chunk in enumerate(CAP_WORDS):
        first = t0 + n * step
        last_end = t0 + (n + len(chunk)) * step
        line = " ".join(chunk)
        base = text(f"cap_{ci + 1}", f"Caption {ci + 1}", line, first, last_end + (0.5 if ci == len(CAP_WORDS) - 1 else 0), W / 2, y, size,
                    font, WHITE, "Captions", shadow=0.6, lh=1.1)
        add_keys(base, "scale", [K(0, 0.9, "out-back"), K(0.25, 1)])
        add_keys(base, "opacity", [K(0, 0, "out-quad"), K(0.12, 1)])
        caps.append(base)
        total = text_width(line, font, size)
        for wi, w in enumerate(chunk):
            ws = t0 + (n + wi) * step
            we = ws + step if wi < len(chunk) - 1 or ci == len(CAP_WORDS) - 1 else last_end
            we = min(we, base["end"])
            pre = " ".join(chunk[:wi]) + (" " if wi else "")
            wx = W / 2 - total / 2 + text_width(pre, font, size) + text_width(w, font, size) / 2 if wi else W / 2 - total / 2 + text_width(w, font, size) / 2
            h_ = text(f"cap_hl_{n + wi + 1}", f"Highlight {n + wi + 1}: {w}", w, ws, max(we, ws + 0.2), wx, y, size, font, YELLOW, "Caption highlight", shadow=0.6, lh=1.1)
            add_keys(h_, "scale", [K(0, 0.9, "out-back"), K(0.16, 1.1, "in-out-sine"), K(0.34, 1.0)])
            hl.append(h_)
        n += len(chunk)
    hook = text("hook", "Hook", "YOUR HOOK\nGOES HERE", 0.3, 2.7, W / 2, 430, 210, "bebas-neue-regular", WHITE, "Titles", lh=0.95, shadow=0.5)
    add_keys(hook, "scale", [K(0, 1.25, "out-expo"), K(0.7, 1)])
    add_keys(hook, "opacity", [K(0, 0, "out-quad"), K(0.25, 1), K(2.1, 1, "in-quad"), K(2.4, 0)])
    cta = text("cta_text", "CTA text", "FOLLOW FOR MORE", 10.3, D, W / 2, 1640, 36, "inter-bold", "#1d1d1f", "Titles", ls=0.06, shadow=0)
    enter(cta, 24, 0.7, blur=0)
    cta_pill = image("cta_pill", "CTA pill", "media/pill.png", 10.3, D, W / 2, 1640, "Titles")
    add_keys(cta_pill, "scale", [K(0, 0.6, "out-back"), K(0.6, 1)])
    add_keys(cta_pill, "opacity", [K(0, 0, "out-quad"), K(0.25, 1)])
    fill = image("progress", "Progress", "media/fill.png", 0, D, 60, 96, "UI", anchor_x=0, keys={"scale_x": [K(0, 0.001), K(D, 1, "linear")]})
    track = image("progress_track", "Progress track", "media/track.png", 0, D, W / 2, 96, "UI")
    shade = image("shade", "Bottom shade", "media/shade.png", 0, D, W / 2, H - 500, "Picture")
    head = video("talking_head", "Talking head (replace me)", V + "flow_dusk.mp4", 0, D,
                 use=[{"preset": "cam.ken-burns", "at": "in", "params": {"from": {"scale": 1.0}, "to": {"scale": 1.08}}}])
    music = audio("music", "Music bed", A + "lofi_keys.m4a", 0, D, "Sound", volume_db=-14, fade_in=0.6, fade_out=1.0)
    P["layers"] = [cta, cta_pill] + hl[::-1] + caps[::-1] + [hook, fill, track, shade, head, music]
    _save(id, P)
    return dict(id=id, thumb_t=4.3, W=W, H=H, D=D)


# ====================================================================== (b) YouTube intro 16:9
def youtube_intro():
    id, W, H, D = "youtube-intro", 1920, 1080, 8.0
    d = _dir(id)
    shapes.rrect(d + "/media/rule.png", 220, 4, 2, (126, 231, 255, 255))
    P = base_project("YouTube intro (16:9)", W, H, D, vignette=0.3, grain=1.0, fade_out=0.8)
    CX = W / 2
    eb = text("eyebrow", "Episode tag", "E P I S O D E   0 1   ·   C H A N N E L   N A M E", 0.7, 7.2, CX, 330, 28, "inter-semibold", ACCENT, ls=0.3)
    enter(eb, 14, 0.9, blur=6)
    leave(eb, 0.45)
    t1 = text("title1", "Title line 1", "Your Video Title", 1.0, 7.2, CX, 470, 168, "space-grotesk-bold", WHITE, ls=-0.02)
    t2 = text("title2", "Title line 2", "Goes Here", 1.25, 7.2, CX, 640, 168, "space-grotesk-bold", "#d2c4ff", ls=-0.02)
    for t in (t1, t2):
        enter(t, 70, 1.1, blur=18)
        leave(t, 0.5)
    rule = image("rule", "Underline", "media/rule.png", 1.9, 7.2, CX, 760, "Titles", anchor_x=0.5)
    add_keys(rule, "scale_x", [K(0, 0, "out-expo"), K(0.9, 1)])
    add_keys(rule, "opacity", [K(7.2 - 1.9 - 0.4, 1), K(7.2 - 1.9, 0)])
    sub = text("subtitle", "Subtitle", "One line about what this video is about.", 2.1, 7.2, CX, 850, 38, "inter-regular", "#c7c7cc", shadow=0.8)
    enter(sub, 18, 0.9, blur=6)
    leave(sub, 0.4)
    bg = video("bg", "Neon grid", V + "neon_grid.mp4", 0, D, effects=[{"type": "brightness", "enabled": True, "value": -0.12}],
               use=[{"preset": "cam.ken-burns", "at": "in", "params": {"from": {"scale": 1.0}, "to": {"scale": 1.1}}},
                    {"preset": "cam.punch", "at": 1.0, "params": {"amount": 1.05, "settle": 0.6}}])
    music = audio("music", "Music bed", A + "upbeat_pluck.m4a", 0, D, "Sound", volume_db=-2, fade_in=0.3, fade_out=1.5)
    fx = [sfx("sfx_whoosh", "Whoosh", "whoosh", 0.8, -9, S), sfx("sfx_hit", "Hit", "hit", 1.0, -8, S),
          sfx("sfx_click", "Click", "click", 2.0, -14, S)]
    P["layers"] = [sub, rule, t2, t1, eb, bg] + fx + [music]
    _save(id, P)
    return dict(id=id, thumb_t=4.2, W=W, H=H, D=D)


# ====================================================================== (c) product promo 1:1, 15 s
def product_promo():
    id, W, H, D = "product-promo", 1080, 1080, 15.0
    d = _dir(id)
    shapes.product_placeholder(d + "/media/product.png", 520, 720)
    shapes.rrect(d + "/media/pill.png", 400, 90, 45, (29, 29, 31, 255))
    shapes.circle(d + "/media/dot.png", 22, (29, 29, 31, 255))
    P = base_project("Product promo (1:1, 15 s)", W, H, D, vignette=0.0, grain=0.6, fade_out=0.8)
    CX = W / 2
    ink, grey = INK, "#6e6e73"
    # hook (0-3.7)
    h1 = text("hook1", "Hook 1", "Meet the all-new", 0.3, 3.7, CX, 250, 60, "inter-medium", grey, shadow=0)
    h2 = text("hook2", "Hook 2", "Product Name.", 0.55, 3.7, CX, 350, 130, "inter-bold", ink, ls=-0.03, shadow=0)
    for t in (h1, h2):
        enter(t, 50, 1.0, blur=12)
        leave(t, 0.4)
    # product: rises in, floats (wiggle), slides left for the features, returns
    prod = image("product", "Product (replace me)", "media/product.png", 1.0, D, CX, 640, "Picture", scale=0.95,
                 mods={"y": [{"type": "wiggle", "amp": 9, "freq": 0.35, "octaves": 2, "seed": 4}],
                       "rotation": [{"type": "wiggle", "amp": 1.2, "freq": 0.3, "octaves": 2, "seed": 9}]})
    add_keys(prod, "y", [K(0, 640 + 160, "out-expo"), K(1.3, 640), K(3.6 - 1.0, 640, "in-out-cubic"), K(4.5 - 1.0, 560)])
    add_keys(prod, "x", [K(0, CX, "linear"), K(3.6 - 1.0, CX, "in-out-cubic"), K(4.6 - 1.0, 300, "in-out-cubic"), K(10.8 - 1.0, 300, "in-out-cubic"), K(11.8 - 1.0, 300)])
    add_keys(prod, "scale", [K(0, 0.8, "out-expo"), K(1.3, 0.95), K(3.6 - 1.0, 0.95, "in-out-cubic"), K(4.6 - 1.0, 0.72)])
    add_keys(prod, "opacity", [K(0, 0, "out-quad"), K(0.5, 1)])
    # features (4.6-10.8)
    feats = [("Feature one", "One short line."), ("Feature two", "Keep it to a sentence."),
             ("Feature three", "Three is the sweet spot.")]
    fl, dots = [], []
    for i, (a, b) in enumerate(feats):
        t0 = 4.9 + i * 1.1
        y0 = 400 + i * 150
        dot = image(f"feat{i + 1}_dot", f"Feature {i + 1} dot", "media/dot.png", t0, 10.9, 610, y0 + 14, "Features")
        add_keys(dot, "scale", [K(0, 0, "out-back"), K(0.5, 1)])
        add_keys(dot, "opacity", [K(10.9 - t0 - 0.35, 1), K(10.9 - t0, 0)])
        ta = text(f"feat{i + 1}_title", f"Feature {i + 1}", a, t0, 10.9, 650 + text_width(a, "inter-bold", 50) / 2, y0, 50, "inter-bold", ink, "Features", align="left", shadow=0)
        tb = text(f"feat{i + 1}_text", f"Feature {i + 1} text", b, t0 + 0.12, 10.9, 650 + text_width(b, "inter-regular", 30) / 2, y0 + 52, 30, "inter-regular", grey, "Features", align="left", shadow=0)
        for t in (ta, tb):
            enter(t, 0, 0.8, blur=8)
            slide(t, 40, 0.9)
            leave(t, 0.4, 0, 0)
        fl += [tb, ta]
        dots.append(dot)
    # CTA (11.2-15)
    c1 = text("cta1", "CTA line 1", "Available", 11.3, D, 690, 400, 110, "inter-bold", ink, "CTA", ls=-0.03, shadow=0)
    c2 = text("cta2", "CTA line 2", "now.", 11.45, D, 690, 510, 110, "inter-bold", "#7c5cff", "CTA", ls=-0.03, shadow=0)
    for t in (c1, c2):
        enter(t, 40, 0.9, blur=10)
    pill = image("cta_pill", "Button", "media/pill.png", 11.9, D, 690, 690, "CTA")
    add_keys(pill, "scale", [K(0, 0.7, "out-back"), K(0.6, 1)])
    add_keys(pill, "opacity", [K(0, 0, "out-quad"), K(0.3, 1)])
    ptxt = text("cta_button", "Button text", "Get yours today", 11.9, D, 690, 690, 34, "inter-semibold", WHITE, "CTA", shadow=0)
    enter(ptxt, 10, 0.6, blur=0)
    url = text("cta_url", "URL", "yourwebsite.com", 12.2, D, 690, 790, 32, "inter-regular", grey, "CTA", shadow=0)
    enter(url, 10, 0.7, blur=4)
    bg = video("bg", "Pearl gradient", V + "flow_pearl.mp4", 0, D, use=[
        {"preset": "cam.ken-burns", "at": "in", "params": {"from": {"scale": 1.0}, "to": {"scale": 1.06}}}])
    music = audio("music", "Music bed", A + "upbeat_pluck.m4a", 0, D, "Sound", volume_db=-3, fade_in=0.4, fade_out=1.5)
    fx = [sfx("sfx_whoosh1", "Whoosh (intro)", "whoosh", 0.7, -10, S), sfx("sfx_pop1", "Pop 1", "pop", 4.9, -10, S),
          sfx("sfx_pop2", "Pop 2", "pop", 6.0, -10, S), sfx("sfx_pop3", "Pop 3", "pop", 7.1, -10, S),
          sfx("sfx_whoosh2", "Whoosh (CTA)", "whoosh", 10.9, -11, S), sfx("sfx_click", "Click (button)", "click", 12.3, -12, S)]
    P["layers"] = [url, ptxt, pill, c2, c1] + fl + dots + [h2, h1, prod, bg] + fx + [music]
    _save(id, P)
    return dict(id=id, thumb_t=7.6, W=W, H=H, D=D)


# ====================================================================== (d) quote / stat card 4:5
def stat_card():
    id, W, H, D = "quote-stat-card", 1080, 1350, 9.0
    d = _dir(id)
    shapes.rrect(d + "/media/track.png", 760, 14, 7, (255, 255, 255, 60))
    shapes.rrect(d + "/media/fill.png", 760, 14, 7, (126, 231, 255, 255))
    shapes.rrect(d + "/media/rule.png", 96, 3, 1, (255, 255, 255, 170))
    P = base_project("Quote and stat card (4:5)", W, H, D, vignette=0.3, grain=1.0, fade_out=0.7)
    CX = W / 2
    # act 1: quote 0.4-4.7
    mark = text("q_mark", "Quote mark", "“", 0.3, 4.9, CX, 330, 460, "cormorant-garamond-bold", WHITE, "Quote", shadow=0)
    add_keys(mark, "opacity", [K(0, 0, "out-quad"), K(0.9, 0.16), K(4.6 - 0.4, 0.16, "in-quad"), K(4.6, 0)])
    lines = ["Write the quote", "here, in two or", "three short lines."]
    ql = []
    for i, ln in enumerate(lines):
        q = text(f"q_line{i + 1}", f"Quote line {i + 1}", ln, 0.7 + i * 0.4, 4.9, CX, 560 + i * 118, 100, "cormorant-garamond-italic", WHITE if i < 2 else "#ffd9c7", "Quote", lh=1.1)
        enter(q, 34, 1.0, blur=12, ease="out-quart")
        leave(q, 0.45, -18, 8)
        ql.append(q)
    rule = image("q_rule", "Rule", "media/rule.png", 2.3, 4.9, CX, 960, "Quote")
    add_keys(rule, "scale_x", [K(0, 0, "out-expo"), K(0.7, 1)])
    add_keys(rule, "opacity", [K(4.9 - 2.3 - 0.4, 1), K(4.9 - 2.3, 0)])
    who = text("q_who", "Attribution", "N A M E   S U R N A M E   ·   R O L E", 2.4, 4.9, CX, 1020, 26, "inter-medium", GREY, "Quote", ls=0.2)
    enter(who, 10, 0.8, blur=4)
    leave(who, 0.4, -10, 0)
    # act 2: stat 4.9-9
    t0 = 4.8
    eb = text("s_eyebrow", "Stat eyebrow", "T H E   N U M B E R", t0 + 0.2, D, CX, 330, 28, "inter-semibold", ACCENT, "Stat", ls=0.3)
    enter(eb, 12, 0.8, blur=4)
    num = text("s_number", "Stat number", "87%", t0 + 0.4, D, CX, 560, 400, "space-grotesk-bold", WHITE, "Stat", ls=-0.03, shadow=0.35)
    add_keys(num, "scale", [K(0, 1.15, "out-expo"), K(0.9, 1)])
    add_keys(num, "opacity", [K(0, 0, "out-quad"), K(0.35, 1)])
    add_blur(num)
    add_keys(num, "fx.bl.radius", [K(0, 24, "out-cubic"), K(0.7, 0)])
    lab = text("s_label", "Stat label", "Say what this number means\nin one clear sentence.", t0 + 1.0, D, CX, 830, 46, "inter-medium", "#e5e5ea", "Stat", lh=1.3, shadow=0.4)
    enter(lab, 24, 0.9, blur=6)
    trk = image("s_track", "Meter track", "media/track.png", t0 + 1.2, D, CX, 990, "Stat")
    add_keys(trk, "opacity", [K(0, 0, "out-quad"), K(0.5, 1)])
    fil = image("s_fill", "Meter fill", "media/fill.png", t0 + 1.2, D, CX - 380, 990, "Stat", anchor_x=0,
                keys={"scale_x": [K(0, 0.001), K(1.3, 0.87, "out-expo")]})
    src = text("s_source", "Source", "Source: your source, 2026", t0 + 1.6, D, CX, 1180, 26, "inter-regular", GREY, "Stat")
    enter(src, 8, 0.7, blur=0)
    bg = video("bg", "Dusk gradient", V + "flow_dusk.mp4", 0, D, src_in=2.0, use=[
        {"preset": "cam.ken-burns", "at": "in", "params": {"from": {"scale": 1.0}, "to": {"scale": 1.1}}}])
    music = audio("music", "Music bed", A + "cinematic_pad.m4a", 0, D, "Sound", volume_db=-2, fade_in=0.8, fade_out=1.5)
    fx = [sfx("sfx_whoosh", "Whoosh", "whoosh", 4.35, -11, S), sfx("sfx_hit", "Hit", "hit", 4.8, -9, S),
          sfx("sfx_pop", "Pop", "pop", 5.9, -12, S)]
    P["layers"] = [src, fil, trk, lab, num, eb] + [who, rule] + ql[::-1] + [mark, bg] + fx + [music]
    _save(id, P)
    return dict(id=id, thumb_t=7.6, W=W, H=H, D=D)


# ====================================================================== (e) lower-third pack 16:9
def lower_thirds():
    id, W, H, D = "lower-thirds", 1920, 1080, 12.0
    d = _dir(id)
    shapes.rrect(d + "/media/a_bar.png", 6, 100, 3, (126, 231, 255, 255))
    shapes.rrect(d + "/media/b_card.png", 720, 140, 24, (255, 255, 255, 240))
    shapes.circle(d + "/media/b_dot.png", 28, (124, 92, 255, 255))
    shapes.rrect(d + "/media/c_block.png", 760, 104, 6, (126, 231, 255, 255))
    shapes.rrect(d + "/media/c_strip.png", 560, 56, 4, (17, 17, 20, 235))
    P = base_project("Lower-third pack (3 styles)", W, H, D, vignette=0.25, grain=1.0, fade_out=0.6)
    L = []
    # ---- style A (0.5-4.0): minimal line + type
    a0, a1, ax = 0.5, 4.0, 190
    na, ra = "Firstname Lastname", "Role or company"
    bar = image("ltA_bar", "A bar", "media/a_bar.png", a0, a1, ax, 900, "Style A", anchor_y=0.5)
    add_keys(bar, "scale_y", [K(0, 0, "out-expo"), K(0.6, 1)])
    add_keys(bar, "opacity", [K(a1 - a0 - 0.35, 1), K(a1 - a0, 0)])
    n_ = text("ltA_name", "A name", na, a0 + 0.12, a1, ax + 30 + text_width(na, "inter-semibold", 54) / 2, 882, 54, "inter-semibold", WHITE, "Style A", align="left")
    r_ = text("ltA_role", "A role", ra, a0 + 0.25, a1, ax + 30 + text_width(ra, "inter-regular", 32) / 2, 940, 32, "inter-regular", "#c7c7cc", "Style A", align="left")
    for t in (n_, r_):
        enter(t, 0, 0.7, blur=6)
        slide(t, 44, 0.9)
        leave(t, 0.35, 0, 0)
    L += [r_, n_, bar]
    # ---- style B (4.5-8.0): white card
    b0, b1, bx, by = 4.5, 8.0, 190 + 360, 900
    card = image("ltB_card", "B card", "media/b_card.png", b0, b1, bx, by, "Style B")
    add_keys(card, "x", [K(0, bx - 120, "out-expo"), K(0.8, bx)])
    add_keys(card, "opacity", [K(0, 0, "out-quad"), K(0.3, 1), K(b1 - b0 - 0.35, 1), K(b1 - b0, 0)])
    dot = image("ltB_dot", "B dot", "media/b_dot.png", b0, b1, bx - 320, by, "Style B")
    add_keys(dot, "x", [K(0, bx - 320 - 120, "out-expo"), K(0.8, bx - 320)])
    add_keys(dot, "scale", [K(0.15, 0, "out-back"), K(0.7, 1)])
    add_keys(dot, "opacity", [K(b1 - b0 - 0.35, 1), K(b1 - b0, 0)])
    nb = text("ltB_name", "B name", na, b0, b1, bx - 290 + text_width(na, "inter-bold", 48) / 2, by - 22, 48, "inter-bold", INK, "Style B", align="left", shadow=0)
    rb = text("ltB_role", "B role", ra, b0, b1, bx - 290 + text_width(ra, "inter-regular", 30) / 2, by + 32, 30, "inter-regular", "#6e6e73", "Style B", align="left", shadow=0)
    for t in (nb, rb):
        slide(t, 120, 0.8)
        add_keys(t, "opacity", [K(0.1, 0, "out-quad"), K(0.5, 1), K(b1 - b0 - 0.35, 1), K(b1 - b0, 0)])
    L += [rb, nb, dot, card]
    # ---- style C (8.5-12.0): bold block + strip
    c0, c1, cx_, cy = 8.5, 12.0, 190, 880
    nc, rc = "FIRSTNAME LASTNAME", "ROLE OR COMPANY"
    blk = image("ltC_block", "C block", "media/c_block.png", c0, c1, cx_, cy, "Style C", anchor_x=0)
    add_keys(blk, "scale_x", [K(0, 0, "out-expo"), K(0.6, 1), K(c1 - c0 - 0.4, 1, "in-expo"), K(c1 - c0, 0)])
    strip = image("ltC_strip", "C strip", "media/c_strip.png", c0, c1, cx_, cy + 80, "Style C", anchor_x=0)
    add_keys(strip, "scale_x", [K(0.15, 0, "out-expo"), K(0.7, 1), K(c1 - c0 - 0.3, 1, "in-expo"), K(c1 - c0, 0)])
    tn = text("ltC_name", "C name", nc, c0 + 0.25, c1, cx_ + 34 + text_width(nc, "bebas-neue-regular", 78, 0.03) / 2, cy, 78, "bebas-neue-regular", "#0b1220", "Style C", ls=0.03, align="left", shadow=0)
    enter(tn, 0, 0.6, blur=0)
    slide(tn, 60, 0.8)
    leave(tn, 0.3, 0, 0)
    tr = text("ltC_role", "C role", rc, c0 + 0.4, c1, cx_ + 34 + text_width(rc, "inter-semibold", 26, 0.12) / 2, cy + 80, 26, "inter-semibold", WHITE, "Style C", ls=0.12, align="left", shadow=0)
    enter(tr, 0, 0.6, blur=0)
    slide(tr, 60, 0.8)
    leave(tr, 0.3, 0, 0)
    L += [tr, tn, strip, blk]
    bg = video("bg", "Footage (replace me)", V + "bokeh_city.mp4", 0, D, effects=[{"type": "brightness", "enabled": True, "value": -0.2}])
    fx = [sfx("sfx_a", "Whoosh A", "whoosh", a0 + 0.1, -14, S), sfx("sfx_b", "Whoosh B", "whoosh", b0 + 0.1, -14, S),
          sfx("sfx_c", "Whoosh C", "whoosh", c0 + 0.1, -14, S)]
    P["layers"] = L + [bg] + fx
    _save(id, P)
    return dict(id=id, thumb_t=6.2, W=W, H=H, D=D)


# ====================================================================== index, readmes, thumbnails
META = {
    "captions-vertical": dict(
        name={"en": "Vertical captions (talking head)", "tr": "Dikey altyazı (konuşan kafa)"}, aspect="9:16",
        description={"en": "Word-by-word captions with a highlighted active word, a hook, a progress bar and a follow button. Drop your clip in, then let make_captions replace the sample words.",
                     "tr": "Kelime kelime altyazı, aktif kelimede vurgu, giriş cümlesi, ilerleme çubuğu ve takip düğmesi. Kendi klibini koy, örnek kelimeleri make_captions ile değiştir."},
        tags=["vertical", "captions", "shorts", "reels", "tiktok"],
        replace=["Layer `talking_head`: your clip (keep `fit: cover`, set `src`, `src_in`, `end`).",
                 "Caption layers `cap_*` and `cap_hl_*`: either edit the words by hand or run `make_captions` on the clip (it replaces every `cap_*` layer; word-highlight styling is kept as a pattern to copy).",
                 "Layer `hook`: the first 2.4 s of text.", "Layer `music`: pick another bed or delete it.", "`duration` and every layer `end`."],
        prompt="Start from templates/captions-vertical. Copy it to ./my-short, replace talking_head with media/interview.mp4 (use the part from 12 s to 27 s), run make_captions on it with 3 words per line, and write a hook in the `hook` layer that says \"The mistake everyone makes\".",
        extra="The caption pattern: each chunk is one `cap_<n>` text layer (white). Every word of it also has a `cap_hl_<n>` layer in yellow that sits exactly on top for that word's time window and pops (`scale` keys). To restyle, change `color`, `font` or `size` on both kinds together."),
    "youtube-intro": dict(
        name={"en": "YouTube title / intro card", "tr": "YouTube başlık / giriş kartı"}, aspect="16:9",
        description={"en": "An 8 second animated title card on a slow neon-grid horizon: episode tag, two-line title with an underline sweep, one line of subtitle, music and impact sounds.",
                     "tr": "Yavaş bir neon ızgara ufku üzerinde 8 saniyelik animasyonlu başlık kartı: bölüm etiketi, iki satır başlık, alt çizgi, alt başlık, müzik ve efekt sesleri."},
        tags=["youtube", "intro", "title", "16:9"],
        replace=["Layers `title1`, `title2`: your video title (two lines, about 16 characters each fits at size 168).", "Layer `eyebrow`: episode number and channel name.",
                 "Layer `subtitle`: one line.", "Layer `bg`: swap for another clip from templates/_media/video/ or your own footage.", "`music`: another bed."],
        prompt="Use templates/youtube-intro for my video \"How I edit with code\". Episode 04, channel \"Nova Labs\". Subtitle: \"A five minute walkthrough\". Keep everything else, but make the second title line the accent colour #ffb454.",
        extra=""),
    "product-promo": dict(
        name={"en": "Product promo (1:1, 15 s)", "tr": "Ürün tanıtımı (1:1, 15 sn)"}, aspect="1:1",
        description={"en": "A bright 15 second product spot: hook, floating product, three feature call-outs, price-free call to action. The product is a placeholder slab; swap in your own cut-out PNG.",
                     "tr": "Aydınlık, 15 saniyelik ürün filmi: giriş cümlesi, süzülen ürün, üç özellik, harekete geçirici çağrı. Ürün bir yer tutucu; kendi PNG'nle değiştir."},
        tags=["product", "promo", "square", "instagram", "ad"],
        replace=["Layer `product`: a transparent PNG of your product (`media/product.png`, roughly 700 px tall). Position and scale are keyed; adjust `scale` keys if yours is wider.",
                 "`hook1`, `hook2`: the opening line and product name.", "`feat1_*` to `feat3_*`: three features (title and one sentence each).",
                 "`cta1`, `cta2`, `cta_button`, `cta_url`: closing call to action.", "`bg`: `flow_pearl.mp4` is a light gradient; the text colours assume a light background."],
        prompt="Start from templates/product-promo. Product: \"Aero Bottle\", a matte steel water bottle. Hook: \"Meet the all-new\". Features: keeps drinks cold 24 h / fits every cup holder / made from 100% recycled steel. CTA: \"Available now\", button \"Shop the drop\", URL aerobottle.example. I put the cut-out at media/bottle.png.",
        extra=""),
    "quote-stat-card": dict(
        name={"en": "Quote and stat card (4:5)", "tr": "Alıntı ve rakam kartı (4:5)"}, aspect="4:5",
        description={"en": "A 9 second card for feed posts: a serif quote that resolves into a big animated number with a meter. Two acts on one slow gradient.",
                     "tr": "Akış paylaşımları için 9 saniyelik kart: serif bir alıntı, ardından ölçek çubuklu büyük bir rakama dönüşür. Tek bir yavaş gradyan üzerinde iki perde."},
        tags=["quote", "stat", "card", "4:5", "instagram", "linkedin"],
        replace=["`q_line1` to `q_line3`, `q_who`: the quote and who said it. Delete act 1 (layers starting `q_`) to keep only the stat.",
                 "`s_number`: the figure (the meter `s_fill` ends at `scale_x` 0.87, so change the last `scale_x` key to your percentage / 100).",
                 "`s_label`, `s_source`, `s_eyebrow`: the label, source line and tag.", "`bg`: another clip from templates/_media/video/."],
        prompt="Start from templates/quote-stat-card. Quote: \"Speed is a feature.\" / \"Ship the small thing today.\" by Ada Example, Founder. Stat: 64% with the label \"of users open the app within a minute\", source \"Internal survey, 2026\". Meter should stop at 64%.",
        extra=""),
    "lower-thirds": dict(
        name={"en": "Lower-third pack (3 styles)", "tr": "Alt bant paketi (3 stil)"}, aspect="16:9",
        description={"en": "Three name-plate styles on one 12 second timeline: minimal line, white card, bold block. Copy the style you like into your own project and change start, end and text.",
                     "tr": "Tek bir 12 saniyelik zaman çizelgesinde üç isim bandı stili: sade çizgi, beyaz kart, kalın blok. Beğendiğini kendi projene kopyala; başlangıç, bitiş ve metni değiştir."},
        tags=["lower third", "name", "16:9", "pack"],
        replace=["Style A: layers `ltA_*`. Style B: `ltB_*`. Style C: `ltC_*`. Each style is independent; keep one, delete the others.",
                 "Text layers `*_name` and `*_role`: name and role. Text width is measured for left alignment, so after a long name nudge `x` of that style's text layers.",
                 "`start` / `end` of every layer of a style together (or use `edit_project` to shift them by an offset).", "`bg`: your footage (the placeholder is a dimmed bokeh loop)."],
        prompt="Take style B from templates/lower-thirds and put it on my clip media/interview.mp4 at 8 s for 4 seconds with the name \"Dr. Mira Example\" and role \"Marine biologist\". Also add style A at 31 s for the second guest, \"Jon Sample\", \"Host\".",
        extra="Colours: A accent `#7ee7ff`, B card white with a violet dot `#7c5cff`, C block `#7ee7ff` with a dark strip."),
}


def write_readme(id, info, proj):
    m = META[id]
    W, H, D = proj["width"], proj["height"], proj["duration"]
    layers = proj["layers"]
    groups = {}
    for l in layers:
        groups.setdefault(l.get("group", ""), []).append(l["id"])
    rep = "\n".join(f"- {x}" for x in m["replace"])
    grp = "\n".join(f"- **{g}**: " + ", ".join(f"`{i}`" for i in ids[:8]) + (f" (+{len(ids) - 8} more)" if len(ids) > 8 else "") for g, ids in groups.items())
    extra = ("\n" + m["extra"] + "\n") if m["extra"] else ""
    text_ = f"""# {m['name']['en']}

{m['description']['en']}

- Size: {W}x{H} ({m['aspect']}), {proj['fps']} fps, {D:g} s
- Fonts: bundled OFL fonts only. Media: generated by this repo's code (`scripts/examples/`), no third-party material.
- Media lives in `templates/_media/` (shared) and this folder's `media/`.

## Start from it

```
python3 scripts/examples/new_project.py {id} ~/videos/my-video
python3 server.py ~/videos/my-video
```

`new_project.py` copies this folder and every shared clip / sound it needs into `my-video/media/`, so the project is self-contained.
An agent does the same: copy the folder, then edit `project.json` (patch, do not rewrite).

## What to replace

{rep}
{extra}
## Layers

{grp}

## Suggested agent prompt

> {m['prompt']}

## Turkce

{m['description']['tr']}
Kullanmak icin: `python3 scripts/examples/new_project.py {id} ~/videolar/benim-videom`, sonra `python3 server.py ~/videolar/benim-videom`.
"""
    with open(os.path.join(_dir(id), "README.md"), "w", encoding="utf-8") as f:
        f.write(text_)


BUILDERS = [captions_vertical, youtube_intro, product_promo, stat_card, lower_thirds]


def build_all():
    infos = [b() for b in BUILDERS]
    index = []
    for info in infos:
        id = info["id"]
        proj = json.load(open(os.path.join(_dir(id), "project.json"), encoding="utf-8"))
        write_readme(id, info, proj)
        m = META[id]
        uses = sorted({l["src"] for l in proj["layers"] if l.get("src", "").startswith("../_media/")})
        index.append({"id": id, "name": m["name"], "aspect": m["aspect"], "width": proj["width"], "height": proj["height"],
                      "fps": proj["fps"], "duration": proj["duration"], "folder": id, "project": f"{id}/project.json",
                      "thumbnail": f"{id}/thumbnail.png", "thumbnail_time": info["thumb_t"], "description": m["description"],
                      "tags": m["tags"], "shared_media": uses})
    with open(os.path.join(TPL, "index.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"version": 1, "templates": index}, indent=2, ensure_ascii=False) + "\n")


def thumbnails():
    idx = json.load(open(os.path.join(TPL, "index.json"), encoding="utf-8"))
    for t in idx["templates"]:
        pd = _dir(t["id"])
        tmp = os.path.join(pd, "_thumb_full.png")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), pd, "--frame", str(t["thumbnail_time"]), "--output", tmp],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise SystemExit(r.stdout + r.stderr)
        im = Image.open(tmp).convert("RGB")
        w = 720 if im.width >= im.height else 540
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
        im.save(os.path.join(pd, "thumbnail.png"), optimize=True)
        os.remove(tmp)
        print("  thumbnail", t["id"], os.path.getsize(os.path.join(pd, "thumbnail.png")) // 1024, "KB", flush=True)
