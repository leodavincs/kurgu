"""Tiny UI shapes drawn with Pillow (supersampled, RGBA PNG): bars, pills, cards, gradient shades, a logo mark, a
placeholder 'product'. Used by the example / template projects as `image` layers with fit=native."""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SS = 4


def _save(im, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, optimize=True)
    return path


def rrect(path, w, h, r, fill=(255, 255, 255, 255), outline=None, border=0):
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=r * SS, fill=fill, outline=outline, width=border * SS)
    return _save(im.resize((w, h), Image.LANCZOS), path)


def circle(path, d_px, fill=(255, 255, 255, 255)):
    return rrect(path, d_px, d_px, d_px // 2, fill)


def vgradient(path, w, h, top, bottom, gamma=1.0):
    """Vertical RGBA gradient (top -> bottom), e.g. a black shade for caption legibility."""
    t = (np.linspace(0, 1, h, dtype=np.float32) ** gamma)[:, None, None]
    a = np.array(top, np.float32)[None, None, :] * (1 - t) + np.array(bottom, np.float32)[None, None, :] * t
    arr = np.broadcast_to(a, (h, w, 4)).astype(np.uint8)
    return _save(Image.fromarray(np.ascontiguousarray(arr), "RGBA"), path)


def hgradient_bar(path, w, h, left, right, r=None):
    t = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    a = np.array(left, np.float32) * (1 - t) + np.array(right, np.float32) * t
    arr = np.broadcast_to(a, (h, w, 4)).astype(np.uint8).copy()
    im = Image.fromarray(arr, "RGBA")
    m = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=(r if r is not None else h // 2) * SS, fill=255)
    m = m.resize((w, h), Image.LANCZOS)
    im.putalpha(Image.fromarray((np.array(m, np.float32) * arr[..., 3] / 255).astype(np.uint8)))
    return _save(im, path)


def logo_mark(path, size=256):
    """Rounded square, cyan -> violet, with three timeline 'clips' cut into it."""
    s = size * SS
    ys, xs = np.mgrid[0:s, 0:s].astype(np.float32) / s
    t = np.clip((xs * 0.55 + ys * 0.45), 0, 1)[..., None]
    a, b = np.array([126, 231, 255], np.float32), np.array([139, 92, 246], np.float32)
    rgb = (a * (1 - t) + b * t).astype(np.uint8)
    im = Image.fromarray(rgb, "RGB").convert("RGBA")
    m = Image.new("L", (s, s), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.23), fill=255)
    im.putalpha(m)
    d = ImageDraw.Draw(im)
    for (x0, x1, y) in ((0.20, 0.62, 0.30), (0.34, 0.80, 0.47), (0.20, 0.50, 0.64)):
        d.rounded_rectangle([x0 * s, y * s, x1 * s, (y + 0.11) * s], radius=int(0.055 * s), fill=(255, 255, 255, 240))
    return _save(im.resize((size, size), Image.LANCZOS), path)


def product_placeholder(path, w=560, h=780):
    """An abstract device-like slab with a soft shadow. Stand-in for the user's product cut-out (PNG with alpha)."""
    pad = 90
    W, H = w + 2 * pad, h + 2 * pad
    im = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad * SS, (pad + 40) * SS, (pad + w) * SS, (pad + h + 40) * SS], radius=72 * SS, fill=(40, 30, 70, 120))
    sh = sh.filter(ImageFilter.GaussianBlur(38 * SS))
    im.alpha_composite(sh)
    body = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ys, xs = np.mgrid[0:h * SS, 0:w * SS].astype(np.float32)
    u, v = xs / (w * SS), ys / (h * SS)
    top, bot = np.array([44, 46, 70], np.float32), np.array([14, 15, 30], np.float32)
    g = (top * (1 - v[..., None]) + bot * v[..., None])
    glow = np.exp(-(((u - 0.72) / 0.5) ** 2 + ((v - 0.25) / 0.35) ** 2))[..., None] * np.array([90, 70, 200], np.float32) * 0.55
    g = np.clip(g + glow, 0, 255).astype(np.uint8)
    slab = Image.fromarray(g, "RGB").convert("RGBA")
    m = Image.new("L", slab.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, slab.size[0] - 1, slab.size[1] - 1], radius=72 * SS, fill=255)
    slab.putalpha(m)
    body.paste(slab, (pad * SS, pad * SS))
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle([pad * SS, pad * SS, (pad + w) * SS - 1, (pad + h) * SS - 1], radius=72 * SS, outline=(255, 255, 255, 60), width=3 * SS)
    # a "screen" panel and a lens dot
    d.rounded_rectangle([(pad + 34) * SS, (pad + 34) * SS, (pad + w - 34) * SS, (pad + h * 0.62) * SS], radius=44 * SS, fill=(255, 255, 255, 22))
    d.ellipse([(pad + w / 2 - 22) * SS, (pad + h - 96) * SS, (pad + w / 2 + 22) * SS, (pad + h - 52) * SS], fill=(255, 255, 255, 46))
    d.rounded_rectangle([(pad + 60) * SS, (pad + h * 0.66) * SS, (pad + 60 + 220) * SS, (pad + h * 0.66 + 14) * SS], radius=7 * SS, fill=(255, 255, 255, 120))
    d.rounded_rectangle([(pad + 60) * SS, (pad + h * 0.66 + 34) * SS, (pad + 60 + 150) * SS, (pad + h * 0.66 + 46) * SS], radius=6 * SS, fill=(255, 255, 255, 60))
    body.alpha_composite(ov)
    im.alpha_composite(body)
    return _save(im.resize((W, H), Image.LANCZOS), path)
