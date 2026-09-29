"""Procedural background / b-roll-like clips. 8 s, 30 fps, 1920x1080, seamless loops (frame 240 == frame 0).

Every motion is a function of the loop phase `th` (0..2pi) using integer multiples only, so the loop closes.
Computed at 960x540 and upscaled by ffmpeg (all content is smooth, so nothing is lost).
"""
import numpy as np

from _common import RW, RH, grid, hex_rgb, smoothstep, tonemap, write_video, VIDEO_DIR
import os


def _rng(seed):
    return np.random.default_rng(seed)


def _mix(c0, c1, t):
    return c0 * (1 - t[..., None]) + c1 * t[..., None]


# ------------------------------------------------------------------ aurora
def aurora(path):
    X, Y = grid()
    r = _rng(11)
    sky_top, sky_bot = hex_rgb("#03050d"), hex_rgb("#0b1636")
    base = _mix(sky_top, sky_bot, Y ** 1.4)
    # stars, twinkle with integer frequency
    n = 260
    sx, sy = r.random(n), r.random(n) * 0.75
    sb, sph, sfq = r.random(n) * 0.8 + 0.2, r.random(n) * 6.283, r.integers(1, 4, n)
    ridge = 0.90 + 0.02 * np.sin(X * 9 + 1) + 0.012 * np.sin(X * 23 + 2) + 0.03 * np.sin(X * 3.1 + 0.4)
    curtains = [(0.58, 0.10, hex_rgb("#33ffa8"), hex_rgb("#7a5cff"), 0.9, 1.0),
                (0.66, 0.11, hex_rgb("#2fe0d0"), hex_rgb("#c46bff"), 0.55, 2.0),
                (0.47, 0.09, hex_rgb("#7dffb0"), hex_rgb("#3a7bff"), 0.45, 3.0)]

    def frame(i, th):
        img = base.copy()
        # stars
        for k in range(n):
            xi, yi = int(sx[k] * (RW - 1)), int(sy[k] * (RH - 1))
            v = sb[k] * (0.6 + 0.4 * np.sin(th * sfq[k] + sph[k]))
            img[yi, xi] += v * 0.9
            if sb[k] > 0.85:
                img[yi, max(xi - 1, 0)] += v * 0.25
                img[yi, min(xi + 1, RW - 1)] += v * 0.25
        glow = np.zeros_like(img)
        for j, (c, w, ca, cb, amp, ph) in enumerate(curtains):
            ctr = (c + 0.055 * np.sin(2 * np.pi * (0.9 + 0.3 * j) * X + 1.7 * j + th)
                   + 0.035 * np.sin(2 * np.pi * (2.3 + 0.4 * j) * X + 0.6 * j - 2 * th)
                   + 0.018 * np.sin(2 * np.pi * (5.1 + j) * X + 3 * th + j))
            d = Y - ctr
            up = np.exp(np.minimum(d, 0) / (0.2 + 0.05 * j))                    # soft fade upward
            down = np.exp(-(np.maximum(d, 0) / w) ** 2)                          # sharp lower edge
            rays = 0.62 + 0.38 * np.sin(2 * np.pi * (19 + 5 * j) * X + 2.2 * np.sin(2 * np.pi * (1.5 + j) * X + th) + j)
            rays = rays * (0.65 + 0.35 * np.sin(2 * np.pi * (2 + j) * X + 1.3 * j - th))
            rays = 0.55 * rays + 0.45
            band = up * down * np.where(d < 0, rays, 1.0) * amp
            col = _mix(ca, cb, np.clip(-d / 0.33, 0, 1))
            glow += band[..., None] * col
        img = img + tonemap(glow, 1.15) * 0.95
        # mirror-lake haze near the horizon and dark ridge in front
        img += (smoothstep(0.6, 0.9, Y) * 0.06)[..., None] * hex_rgb("#4fd6c8")
        m = smoothstep(-0.004, 0.004, Y - ridge)
        img = img * (1 - m[..., None]) + hex_rgb("#02040a") * m[..., None]
        return img

    write_video(path, frame)


# ------------------------------------------------------------------ ink in water
def ink(path):
    X, Y = grid()
    x, y = X * 3.6, Y * 2.0 * 1.0
    deep, mid, hi, glow = hex_rgb("#03030c"), hex_rgb("#1b1f6e"), hex_rgb("#14a3c4"), hex_rgb("#ffd6c2")
    magenta = hex_rgb("#8a2fb8")

    def frame(i, th):
        w1 = np.sin(1.9 * y + 1.4 * np.cos(1.1 * x + th))
        w2 = np.sin(2.1 * x + 1.5 * np.sin(1.7 * y - th))
        x2, y2 = x + 0.95 * w1, y + 0.95 * w2
        w3 = np.sin(1.3 * x2 + 1.2 * np.cos(2.0 * y2 + 2 * th))
        w4 = np.cos(1.6 * y2 - 1.1 * np.sin(1.9 * x2 - th))
        x3, y3 = x2 + 0.6 * w3, y2 + 0.6 * w4
        f = np.sin(2.4 * x3 + th) * np.cos(2.7 * y3 - th) + 0.5 * np.sin(4.1 * (x3 + y3) + 2 * th)
        f = f / 1.5
        ridged = 1.0 - np.abs(f)                                   # thin filaments where f crosses 0
        fil = smoothstep(0.55, 1.0, ridged) ** 1.6
        body = smoothstep(-0.6, 0.9, f) * 0.85
        c = deep + (mid - deep) * body[..., None] * 0.9
        c = c + (magenta * (smoothstep(0.1, 0.9, np.sin(1.5 * x3 - 1.2 * y3 - th)) * body)[..., None]) * 0.28
        c = c + hi[..., None, :].reshape(1, 1, 3) * (fil * 0.55)[..., None]
        c = c + glow * (fil ** 3 * 0.45)[..., None]
        # gentle vignette
        r2 = (X - 0.5) ** 2 * 1.4 + (Y - 0.5) ** 2 * 1.8
        c *= (1.0 - 0.55 * smoothstep(0.05, 0.6, r2))[..., None]
        return c

    write_video(path, frame)


# ------------------------------------------------------------------ city-lights bokeh
def bokeh_city(path):
    X, Y = grid()
    r = _rng(23)
    palette = [hex_rgb(h) for h in ("#ffb347", "#ff6f91", "#4fb3ff", "#9a7bff", "#ffe3b3", "#3fe0c5")]
    wts = np.array([0.30, 0.16, 0.20, 0.14, 0.12, 0.08])
    n = 150
    dots = []
    for _ in range(n):
        rad = float(np.clip(r.gamma(2.0, 0.013), 0.007, 0.11)) * RH
        yy0 = 1.3 * float(r.beta(1.6, 1.0))                # denser toward the bottom, like a skyline
        dots.append(dict(x=r.random(), y=yy0, rad=rad, col=palette[r.choice(6, p=wts)],
                         a=float(r.uniform(0.10, 0.34)) * (0.55 if rad > 0.05 * RH else 1.0),
                         k=1 if rad > 0.04 * RH else 2, sway=r.uniform(0.004, 0.02), ph=r.random() * 6.283,
                         rim=float(r.uniform(0.2, 0.55))))
    bg = _mix(hex_rgb("#070a1c"), hex_rgb("#1a1030"), Y ** 1.2)
    # warm glow of the "city" at the bottom
    bg = bg + (np.exp(-((Y - 1.05) / 0.28) ** 2) * (0.5 + 0.2 * np.sin(X * 7)))[..., None] * hex_rgb("#7a3348") * 0.7

    def frame(i, th):
        img = bg.copy()
        add = np.zeros_like(img)
        for d in dots:
            yy = (d["y"] - d["k"] * (th / (2 * np.pi)) * 1.3) % 1.3 - 0.15
            xx = d["x"] + d["sway"] * np.sin(th * d["k"] + d["ph"])
            cx, cy, R = xx * RW, yy * RH, d["rad"]
            x0, x1 = int(max(cx - R * 1.4 - 4, 0)), int(min(cx + R * 1.4 + 5, RW))
            y0, y1 = int(max(cy - R * 1.4 - 4, 0)), int(min(cy + R * 1.4 + 5, RH))
            if x1 <= x0 or y1 <= y0:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
            dist = np.sqrt((gx - cx) ** 2 + (gy - cy) ** 2)
            soft = 0.5 + 0.28 * R
            edge = 1 - smoothstep(R - soft, R + soft * 0.4, dist)
            rim = 1 + d["rim"] * 0.7 * smoothstep(R * 0.6, R * 0.95, dist) * (1 - smoothstep(R * 0.95, R + soft, dist))
            fade = smoothstep(-0.05, 0.12, yy) * (1 - smoothstep(1.05, 1.15, yy))
            add[y0:y1, x0:x1] += (edge * rim * d["a"] * fade)[..., None] * d["col"]
        return img + tonemap(add, 1.4)

    write_video(path, frame)


# ------------------------------------------------------------------ neon grid horizon
def neon_grid(path):
    X, Y = grid()
    r = _rng(31)
    hz = 0.56
    sky = _mix(hex_rgb("#06030f"), hex_rgb("#2a0f4a"), np.clip((Y / hz), 0, 1) ** 2.2)
    sky = sky + (np.exp(-((Y - hz) / 0.09) ** 2))[..., None] * hex_rgb("#ff3d8b") * 0.4
    n = 150
    sx, sy, sb = r.random(n), r.random(n) * hz * 0.9, r.random(n) * 0.6 + 0.2
    below = Y > hz
    d = np.where(below, Y - hz, 1.0)
    z = 1.0 / np.maximum(d, 1e-3)                                   # depth
    wx = (X - 0.5) * z * 1.9
    pix = z * 0.0009 * 3.0 + 0.004
    fade = np.where(below, smoothstep(0.02, 0.22, d), 0)

    def frame(i, th):
        img = sky.copy()
        for k in range(n):
            if sb[k] > 0.3:
                img[int(sy[k] * (RH - 1)), int(sx[k] * (RW - 1))] += sb[k] * 0.7
        sc = th / (2 * np.pi)                                       # scroll one full cell per loop
        wz = z * 0.55 + sc
        gx = np.abs((wx % 1.0) - 0.5)
        gz = np.abs((wz % 1.0) - 0.5)
        lx = np.exp(-(((0.5 - gx)) / (pix * 1.8)) ** 2)
        lz = np.exp(-(((0.5 - gz)) / (pix * 0.9)) ** 2)
        line = np.maximum(lx, lz) * fade
        col = _mix(hex_rgb("#22d3ee"), hex_rgb("#ff4fa3"), np.clip((1 - d * 2.2), 0, 1))
        floor = _mix(hex_rgb("#0a0518"), hex_rgb("#1d0a36"), np.clip(1 - d * 2, 0, 1))
        img = np.where(below[..., None], floor, img)
        img = img + (tonemap(line * 1.4, 1.0) * 0.85)[..., None] * col
        # horizon glow over the grid
        img = img + (np.exp(-((Y - hz) / 0.02) ** 2))[..., None] * hex_rgb("#ff7ac0") * 0.28
        return img

    write_video(path, frame)


# ------------------------------------------------------------------ gradient flow (mesh gradient)
def _flow(path, colors, seed, base_col, soft=0.16, veil=None):
    X, Y = grid()
    r = _rng(seed)
    blobs = []
    for k, c in enumerate(colors):
        blobs.append(dict(cx=r.uniform(0.15, 0.85), cy=r.uniform(0.2, 0.8), ax=r.uniform(0.08, 0.2), ay=r.uniform(0.08, 0.2),
                          fx=int(r.integers(1, 3)), fy=int(r.integers(1, 3)), ph=r.random() * 6.283, col=hex_rgb(c),
                          s=r.uniform(0.75, 1.25) * soft))

    def frame(i, th):
        acc = np.zeros((RH, RW, 3), np.float32)
        wsum = np.full((RH, RW), 1e-4, np.float32)
        for b in blobs:
            cx = b["cx"] + b["ax"] * np.sin(b["fx"] * th + b["ph"])
            cy = b["cy"] + b["ay"] * np.cos(b["fy"] * th + b["ph"] * 1.3)
            d2 = ((X - cx) * 16 / 9) ** 2 + (Y - cy) ** 2
            w = np.exp(-d2 / (2 * b["s"] ** 2))
            acc += w[..., None] * b["col"]
            wsum += w
        img = acc / wsum[..., None]
        cover = 1 - np.exp(-wsum * 2.2)
        img = hex_rgb(base_col) * (1 - cover[..., None]) + img * cover[..., None]
        if veil is not None:
            r2 = (X - 0.5) ** 2 * 1.2 + (Y - 0.5) ** 2 * 1.6
            img = img * (1 - veil * smoothstep(0.05, 0.6, r2))[..., None]
        return img

    write_video(path, frame)


def flow_dusk(path):
    _flow(path, ["#2b1b6b", "#7a2a86", "#e8643c", "#1a2a6c", "#c44c7a"], 41, "#0c0a1f", 0.27, veil=0.5)


def flow_pearl(path):
    _flow(path, ["#f7e9dc", "#ffd9c7", "#dcd3ff", "#cfe6ff", "#ffe8f0"], 43, "#f6f1ea", 0.2, veil=0.0)


# ------------------------------------------------------------------ dust + light sweep
def dust_sweep(path):
    X, Y = grid()
    r = _rng(53)
    bg = _mix(hex_rgb("#0b0d14"), hex_rgb("#161a26"), (1 - Y) ** 1.3)
    n = 170
    P = dict(x=r.random(n), y=r.random(n), z=r.random(n))
    kx = r.integers(-1, 2, n)
    ky = r.integers(1, 3, n)
    ph = r.random(n) * 6.283
    ang = np.deg2rad(24)
    proj = (X * 16 / 9) * np.cos(ang) + Y * np.sin(ang)           # coordinate across the diagonal band

    def frame(i, th):
        img = bg.copy()
        p = th / (2 * np.pi)
        pos = -0.5 + 2.9 * p                                         # band passes through, is off-screen at the loop seam
        band = np.exp(-((proj - pos) / 0.34) ** 2) * 0.22 + np.exp(-((proj - pos) / 0.05) ** 2) * 0.14
        img = img + band[..., None] * hex_rgb("#ffe9cf")
        add = np.zeros_like(img)
        for k in range(n):
            xx = (P["x"][k] + 0.03 * np.sin(th * 1 + ph[k]) + kx[k] * p * 0.0) % 1
            yy = (P["y"][k] - ky[k] * p * (0.15 + 0.6 * P["z"][k]) * 0.0 - p * 0.0) % 1
            yy = (P["y"][k] - 0.09 * P["z"][k] * (1 - np.cos(th + ph[k])) * 0.5) % 1
            R = 1.2 + 3.6 * P["z"][k] ** 1.5
            cx, cy = xx * RW, yy * RH
            x0, x1 = int(max(cx - R * 3, 0)), int(min(cx + R * 3 + 1, RW))
            y0, y1 = int(max(cy - R * 3, 0)), int(min(cy + R * 3 + 1, RH))
            if x1 <= x0 or y1 <= y0:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
            g = np.exp(-((gx - cx) ** 2 + (gy - cy) ** 2) / (2 * (R * 0.6) ** 2))
            lit = 0.25 + 0.75 * np.exp(-((xx * 16 / 9 * np.cos(ang) + yy * np.sin(ang) - pos) / 0.3) ** 2)
            tw = 0.7 + 0.3 * np.sin(th * 2 + ph[k] * 3)
            add[y0:y1, x0:x1] += (g * (0.10 + 0.7 / (1 + R)) * lit * tw)[..., None]
        img = img + add * hex_rgb("#fff1dd")
        r2 = (X - 0.5) ** 2 * 1.2 + (Y - 0.5) ** 2 * 1.6
        return img * (1 - 0.5 * smoothstep(0.05, 0.6, r2))[..., None]

    write_video(path, frame)


# ------------------------------------------------------------------ geometric rings
def geo_rings(path):
    X, Y = grid()
    u, v = (X - 0.5) * 16 / 9, (Y - 0.5)
    bg = _mix(hex_rgb("#080b17"), hex_rgb("#101733"), np.clip(np.sqrt(u * u + v * v) * 1.3, 0, 1))
    n = 9
    ca, cb = hex_rgb("#7fe3ff"), hex_rgb("#b58cff")

    def frame(i, th):
        img = bg.copy()
        p = th / (2 * np.pi)
        add = np.zeros_like(img)
        for k in range(n):
            s = (k / n + p) % 1.0
            size = 0.02 + 0.95 * s ** 1.6                              # half-size of the rounded square
            ang = (0.9 * s + 0.1 * k / n) * np.pi * 0.5 * 1.0 - 0.3
            ca_, sa_ = np.cos(ang), np.sin(ang)
            ru, rv = u * ca_ + v * sa_, -u * sa_ + v * ca_
            rad = size * 0.28
            qx, qy = np.abs(ru) - (size - rad), np.abs(rv) - (size - rad)
            sdf = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2) + np.minimum(np.maximum(qx, qy), 0) - rad
            w = 0.0016 + 0.0016 * s
            line = np.exp(-(sdf / w) ** 2)
            alpha = np.sin(np.pi * s) ** 1.5
            add += (line * alpha * 0.85)[..., None] * _mix(ca, cb, np.full(u.shape, s, np.float32))
            add += (np.exp(-(sdf / (w * 7)) ** 2) * alpha * 0.10)[..., None] * cb
        return img + tonemap(add, 1.2)

    write_video(path, frame)


CLIPS = {"aurora": aurora, "ink": ink, "bokeh_city": bokeh_city, "neon_grid": neon_grid, "flow_dusk": flow_dusk,
         "flow_pearl": flow_pearl, "dust_sweep": dust_sweep, "geo_rings": geo_rings}


def build_one(name):
    CLIPS[name](os.path.join(VIDEO_DIR, name + ".mp4"))
    return name
