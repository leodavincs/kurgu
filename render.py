#!/usr/bin/env python3
"""Kurgu renderer: project.json -> mp4 / single png (SPEC.md §3).

    python3 render.py <project_dir> [--draft] [--output path.mp4]
    python3 render.py <project_dir> --frame 12.3 [--output frame.png] [--draft]
    export options: --codec h264|h265|prores|vp9|gif|wav|mp3  --format mp4|mov|mkv|webm|gif|wav|mp3
                    --crf N | --bitrate 8000k  --preset veryfast..slow  --scale 0.5 | --size WxH  --fps N
                    --from S --to S  --no-audio  --audio-bitrate 192k

stdout protocol (one flushed line each): `PROGRESS 0.42`, `WARN font <id> not found`, `DONE <abs path>`,
`ERROR <message>` (+ non-zero exit).

Notes / decisions where the spec is silent:
- Sound: every sound-carrying layer is decoded to f32 PCM, gain (dB + envelope + fades) is applied per sample in
  numpy, layers are summed (like amix normalize=0), then ONE ffmpeg pass does loudnorm -> fade_out -> trim.
- `blur` is done in numpy/Pillow at effect time (the layer edge softens into transparency like CSS blur).
- `freeze` does not mute the layer (only the picture freezes); mute the layer if the sound is unwanted.
- Text: letter_spacing == 0 draws a line as one run per font (kerning kept); otherwise character by character.
  Glyph coverage is checked per character with the font's cmap; missing glyphs use the bundled inter-regular face.
- Shadow opacity is multiplied by the glyph alpha; its colour is black.
- --draft scales all geometry (x, y, sizes, blur sigmas) by 0.5; the frame size is rounded to even numbers.
- The encoder writes `.kurgu/render_tmp/out.<ext>`; only a finished file is moved to the final path, so a killed or
  cancelled render (SIGTERM is handled: temp dir removed) never leaves a partial file and never clobbers an old export.
- --from/--to render only that span (video frames and sound); global fade_out still refers to the project end.
- gif: frames go to a lossless temp file, then palettegen + paletteuse (two pass); fps defaults to min(project, 15).
- wav/mp3: no picture is composed at all.
- --frame: only layers active at t are set up (fast); a frame at t = n/fps equals the same frame of a full render.
- Layers are active when start <= t < end (1e-6 tolerance). If a source is shorter than needed, its last frame is held.
- Colour layers take colour effects; blur is ignored for them.
"""
import argparse
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import anim
import media_tools
import fonts
import schema

HERE = os.path.dirname(os.path.abspath(__file__))
EPS = 1e-6
SR = 48000


class RenderError(Exception):
    pass


def out(s):
    print(s, flush=True)


# ------------------------------------------------------------------ helpers
def clamp(v, a=0.0, b=1.0):
    return max(a, min(b, v))


def parse_color(s, default=(0, 0, 0)):
    try:
        s = str(s).strip().lstrip('#')
        if len(s) == 3:
            s = ''.join(c * 2 for c in s)
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return default


def resolve(pdir, path):
    path = os.path.expanduser(str(path))
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(pdir, path))


_probe_cache = {}


def probe(path):
    """-> dict(w, h, dur, audio)"""
    if path in _probe_cache:
        return _probe_cache[path]
    try:
        j = media_tools.probe_json(path, '-show_streams')
    except media_tools.UnsafeSourceError as e:
        raise RenderError(str(e))
    except media_tools.MediaToolError as e:
        if 'was not found' in str(e):
            raise
        raise RenderError(f'ffprobe could not read: {path}')
    except Exception:
        raise RenderError(f'ffprobe could not read: {path}')
    w = h = 0
    audio = False
    for s in j.get('streams', []):
        if s.get('codec_type') == 'video' and not w:
            w, h = int(s.get('width', 0)), int(s.get('height', 0))
            rot = 0
            try:
                rot = int((s.get('tags') or {}).get('rotate', 0))
            except Exception:
                pass
            for sd in s.get('side_data_list', []) or []:
                if 'rotation' in sd:
                    rot = int(sd['rotation'])
            if abs(rot) % 180 == 90:
                w, h = h, w
        if s.get('codec_type') == 'audio':
            audio = True
    try:
        dur = float((j.get('format') or {}).get('duration', 0) or 0)
    except Exception:
        dur = 0.0
    d = {'w': w, 'h': h, 'dur': dur, 'audio': audio}
    _probe_cache[path] = d
    return d


def fade_factor(fade_in, fade_out, t0, t1, t):
    """Linear ramp factor f(t); t may be a scalar or a numpy array."""
    f = 1.0
    if fade_in and fade_in > 0:
        f = f * np.clip((t - t0) / float(fade_in), 0, 1)
    if fade_out and fade_out > 0:
        f = f * np.clip((t1 - t) / float(fade_out), 0, 1)
    return f


# ------------------------------------------------------------------ layer base
class Layer:
    def __init__(self, d, ctx):
        self.d = d
        self.ctx = ctx
        self.id = d.get('id', '?')
        self.name = d.get('name') or self.id
        self.type = d.get('type')
        self.t0 = float(d.get('start', 0))
        self.t1 = float(d.get('end', 0))
        self.fade_in = float(d.get('fade_in', 0) or 0)
        self.fade_out = float(d.get('fade_out', 0) or 0)
        self.opacity = float(d.get('opacity', 1))
        self.x = float(d.get('x', ctx.W0 / 2))
        self.y = float(d.get('y', ctx.H0 / 2))
        self.scale = float(d.get('scale', 1))
        self.zoom = None
        self.freeze = False
        self.fx = []
        for e in d.get('effects') or []:
            if e.get('enabled', True) is False:
                continue
            tp = e.get('type')
            if tp == 'zoom':
                if self.type in ('video', 'image') and self.zoom is None:
                    self.zoom = (float(e.get('from', 1)), float(e.get('to', 1.06)))
            elif tp == 'freeze':
                if self.type in ('video', 'image'):
                    self.freeze = True
            else:
                self.fx.append(e)
        self.animated = anim.is_animated(d)
        # affine path (rotation / flip / anchor / non-uniform scale / anything moving): sub-pixel placement like the canvas.
        # Fully static, untransformed layers keep the original pixel-snapped `place` path (byte-identical to before).
        self.affine = anim.is_animated(d, anim.TRANSFORM_PROPS) or any(
            float(d.get(k, anim.DEFAULTS[k])) != anim.DEFAULTS[k] for k in anim.TRANSFORM_PROPS if k in anim.DEFAULTS)
        self.R = anim.raster_scale(d) if self.type != 'color' else 1.0
        if not self.affine:
            self.R = self.scale
        self.has_blur = self.blur_at(None)

    def val(self, prop, t):
        """Animated value of a layer property at timeline time t."""
        dflt = self.ctx.W0 / 2 if prop == 'x' else self.ctx.H0 / 2 if prop == 'y' else None
        return anim.value(self.d, prop, t - self.t0, dflt)

    def fx_at(self, t):
        """Enabled colour/blur effects with keyed params resolved at timeline time t."""
        if not self.animated:
            return self.fx
        return [anim.effect_params(self.d, e, t - self.t0) for e in self.fx]

    def blur_at(self, t):
        fx = self.fx if t is None else self.fx_at(t)
        return any(e.get('type') == 'blur' and float(e.get('radius', 0)) > 0 for e in fx)

    def transform_at(self, t):
        v = self.val
        return {'x': v('x', t), 'y': v('y', t), 'sx': v('scale', t) * v('scale_x', t), 'sy': v('scale', t) * v('scale_y', t),
                'rot': v('rotation', t), 'ax': v('anchor_x', t), 'ay': v('anchor_y', t)}

    def active(self, t):
        return t >= self.t0 - EPS and t < self.t1 - EPS

    def factor(self, t):
        return fade_factor(self.fade_in, self.fade_out, self.t0, self.t1, t)

    def source_path(self):
        src = self.d.get('src')
        if not src:
            raise RenderError(f'layer "{self.name}" ({self.id}): no src set')
        p = resolve(self.ctx.pdir, src)
        if not os.path.isfile(p):
            raise RenderError(f'layer "{self.name}" ({self.id}): file not found: {p}')
        return p

    def release(self):
        pass


# ------------------------------------------------------------------ effects (CSS filter matrices)
def m_grayscale(m):
    m = clamp(m)
    i = 1 - m
    return np.array([
        [0.2126 + 0.7874 * i, 0.7152 - 0.7152 * i, 0.0722 - 0.0722 * i],
        [0.2126 - 0.2126 * i, 0.7152 + 0.2848 * i, 0.0722 - 0.0722 * i],
        [0.2126 - 0.2126 * i, 0.7152 - 0.7152 * i, 0.0722 + 0.9278 * i]], np.float32)


def m_saturate(s):
    s = max(0.0, s)
    return np.array([
        [0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
        [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
        [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]], np.float32)


def blur_patch(rgb, a, x0, y0, sigma):
    pad = int(math.ceil(3 * sigma)) + 1
    h, w = rgb.shape[:2]
    if a is None:
        a = np.ones((h, w), np.float32)
    prem = np.zeros((h + 2 * pad, w + 2 * pad, 4), np.float32)
    prem[pad:pad + h, pad:pad + w, :3] = rgb * a[..., None]
    prem[pad:pad + h, pad:pad + w, 3] = a * 255
    im = Image.fromarray(np.clip(prem + 0.5, 0, 255).astype(np.uint8), 'RGBA').filter(ImageFilter.GaussianBlur(sigma))
    arr = np.asarray(im).astype(np.float32)
    A = arr[..., 3] / 255.0
    rgb2 = np.clip(arr[..., :3] / np.maximum(A, 1e-4)[..., None], 0, 255)
    return rgb2, A, x0 - pad, y0 - pad


def apply_effects(rgba, x0, y0, has_alpha, fx, S):
    """rgba uint8 (h,w,4) straight -> (rgb, a|None, x0, y0). rgb is 0-255 (uint8 or float32)."""
    if not fx:
        return rgba[..., :3], (rgba[..., 3].astype(np.float32) / 255.0 if has_alpha else None), x0, y0
    rgb = rgba[..., :3].astype(np.float32)
    a = rgba[..., 3].astype(np.float32) / 255.0 if has_alpha else None
    for e in fx:
        tp = e.get('type')
        if tp in ('grayscale', 'saturation'):
            M = m_grayscale(float(e.get('amount', 1))) if tp == 'grayscale' else m_saturate(1 + float(e.get('value', 0)))
            sh = rgb.shape
            rgb = np.clip((rgb.reshape(-1, 3) @ M.T).reshape(sh), 0, 255)
        elif tp == 'brightness':
            rgb = np.clip(rgb * (1 + float(e.get('value', 0))), 0, 255)
        elif tp == 'contrast':
            rgb = np.clip((rgb - 127.5) * (1 + float(e.get('value', 0))) + 127.5, 0, 255)
        elif tp == 'blur':
            sg = float(e.get('radius', 0)) * S
            if sg > 0.05:
                rgb, a, x0, y0 = blur_patch(rgb, a, x0, y0, sg)
    return rgb, a, x0, y0


def blend(buf, rgb, a, x0, y0, sc):
    H, W = buf.shape[:2]
    h, w = rgb.shape[:2]
    vx0, vy0 = max(x0, 0), max(y0, 0)
    vx1, vy1 = min(x0 + w, W), min(y0 + h, H)
    if vx1 <= vx0 or vy1 <= vy0 or sc <= 0:
        return
    r = rgb[vy0 - y0:vy1 - y0, vx0 - x0:vx1 - x0]
    reg = buf[vy0:vy1, vx0:vx1]
    if a is None:
        if sc >= 0.9999:
            reg[...] = r
        else:
            reg += (r - reg) * np.float32(sc)
    else:
        al = a[vy0 - y0:vy1 - y0, vx0 - x0:vx1 - x0] * np.float32(sc)
        reg += (r - reg) * al[..., None]


# ------------------------------------------------------------------ placement (base frame after crop/fit)
def place(base, cx, cy, z, full, WH):
    """base uint8 (bh,bw,4); centre (cx,cy) in output pixels. -> (rgba, x0, y0) or None"""
    bh, bw = base.shape[:2]
    tw, th = max(1, int(round(bw * z))), max(1, int(round(bh * z)))
    return place_rect(base, int(round(cx - tw / 2)), int(round(cy - th / 2)), tw, th, full, WH)


def place_rect(base, dx0, dy0, tw, th, full, WH, margin=0):
    """Draw `base` stretched onto the integer rectangle (dx0, dy0, tw, th). Clipped to the frame (+ margin px) unless `full`."""
    Wf, Hf = WH
    bh, bw = base.shape[:2]
    if full:
        vx0, vy0, vx1, vy1 = dx0, dy0, dx0 + tw, dy0 + th
    else:
        vx0, vy0, vx1, vy1 = max(dx0, -margin), max(dy0, -margin), min(dx0 + tw, Wf + margin), min(dy0 + th, Hf + margin)
    if vx1 <= vx0 or vy1 <= vy0:
        return None
    if tw == bw and th == bh:
        return base[vy0 - dy0:vy1 - dy0, vx0 - dx0:vx1 - dx0], vx0, vy0
    box = ((vx0 - dx0) * bw / tw, (vy0 - dy0) * bh / th, (vx1 - dx0) * bw / tw, (vy1 - dy0) * bh / th)
    im = Image.fromarray(np.ascontiguousarray(base), 'RGBA').resize((vx1 - vx0, vy1 - vy0), Image.BILINEAR, box=box)
    return np.asarray(im), vx0, vy0


def _snap(v):
    return int(math.floor(v + 0.5))


def affine_patch(rgba, off, ppu, w1, h1, tf, z, S, WH, margin=0):
    """Draw a straight-alpha uint8 patch through the layer transform, sub-pixel exact like canvas translate/rotate/scale.

    rgba (ph,pw,4); off = (x, y) of its top-left relative to the layer box centre, in raster px; ppu = (px, py) raster px
    per unit-box project px (includes the draft factor S); (w1,h1) = unit box size in project px (scale 1);
    tf: x,y (anchor position), sx,sy (signed total scale), rot (deg, clockwise), ax,ay (anchor fraction of the box).
    out = S * (pos + Rot * Scale * (q - anchor)), q = raster offset / ppu.  Returns (rgba, x0, y0) or None."""
    Wf, Hf = WH
    ph, pw = rgba.shape[:2]
    sx, sy = tf['sx'] * z, tf['sy'] * z
    if abs(sx) < 1e-6 or abs(sy) < 1e-6:
        return None
    ppux, ppuy = ppu
    if tf['rot'] == 0.0:
        # Axis-aligned (no rotation): Chrome snaps such image draws to whole pixels (rounded edges), so do we. Flips mirror the patch.
        ax_, ay_ = (tf['ax'] - 0.5) * w1, (tf['ay'] - 0.5) * h1
        qx0, qx1 = off[0] / ppux - ax_, (off[0] + rgba.shape[1]) / ppux - ax_
        qy0, qy1 = off[1] / ppuy - ay_, (off[1] + rgba.shape[0]) / ppuy - ay_
        X0, X1 = S * (tf['x'] + sx * qx0), S * (tf['x'] + sx * qx1)
        Y0, Y1 = S * (tf['y'] + sy * qy0), S * (tf['y'] + sy * qy1)
        left, right, top, bot = _snap(min(X0, X1)), _snap(max(X0, X1)), _snap(min(Y0, Y1)), _snap(max(Y0, Y1))
        if right <= left or bot <= top:
            return None
        src = rgba[:, ::-1] if sx < 0 else rgba
        src = src[::-1] if sy < 0 else src
        return place_rect(src, left, top, right - left, bot - top, False, WH, margin)
    fx_, fy_ = min(1.0, S * abs(sx) / ppux), min(1.0, S * abs(sy) / ppuy)     # prefilter only when shrinking
    tw, th = (max(1, int(round(pw * fx_))), max(1, int(round(ph * fy_))))
    im = Image.fromarray(np.ascontiguousarray(rgba), 'RGBA')
    if (tw, th) != (pw, ph):
        im = im.resize((tw, th), Image.BILINEAR)
    pad = Image.new('RGBA', (tw + 2, th + 2), (0, 0, 0, 0))     # transparent rim: edges fade like canvas coverage
    pad.paste(im, (1, 1))
    kx, ky = pw / tw, ph / th
    th_r = math.radians(tf['rot'])
    c, sn = math.cos(th_r), math.sin(th_r)
    A = S * np.array([[c * sx, -sn * sy], [sn * sx, c * sy]], np.float64)
    lin = A @ np.diag([kx / ppux, ky / ppuy])
    anc = np.array([(tf['ax'] - 0.5) * w1, (tf['ay'] - 0.5) * h1])
    t0 = np.array([tf['x'] * S, tf['y'] * S]) + A @ (np.array([off[0] / ppux, off[1] / ppuy]) - anc)
    t0 = t0 - lin @ np.array([1.0, 1.0])                          # coordinates of the padded image
    corners = np.array([[0, 0], [tw + 2, 0], [0, th + 2], [tw + 2, th + 2]], np.float64)
    pts = corners @ lin.T + t0
    x0 = max(int(math.floor(pts[:, 0].min())), -margin)
    y0 = max(int(math.floor(pts[:, 1].min())), -margin)
    x1 = min(int(math.ceil(pts[:, 0].max())), Wf + margin)
    y1 = min(int(math.ceil(pts[:, 1].max())), Hf + margin)
    if x1 <= x0 or y1 <= y0:
        return None
    inv = np.linalg.inv(lin)
    ct = inv @ (np.array([x0, y0], np.float64) - t0)
    data = (inv[0, 0], inv[0, 1], ct[0], inv[1, 0], inv[1, 1], ct[1])
    prem = pad.convert('RGBa').transform((x1 - x0, y1 - y0), Image.AFFINE, data, Image.BILINEAR)
    return np.asarray(prem.convert('RGBA')), x0, y0


def fit_k(sw, sh, W0, H0, mode):
    if mode == 'native':
        return 1.0
    if mode == 'contain':
        return min(W0 / sw, H0 / sh)
    return max(W0 / sw, H0 / sh)


def fit_size(sw, sh, W0, H0, S, scale, mode):
    k = fit_k(sw, sh, W0, H0, mode)
    return max(1, int(round(sw * k * scale * S))), max(1, int(round(sh * k * scale * S)))


# ------------------------------------------------------------------ video / image
class Decoder:
    def __init__(self, cmd, w, h):
        self.w, self.h = w, h
        self.fs = w * h * 4
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                  bufsize=max(self.fs, 1 << 20))
        self.last = None

    def next(self):
        b = self.p.stdout.read(self.fs)
        if len(b) == self.fs:
            self.last = np.frombuffer(b, np.uint8).reshape(self.h, self.w, 4)
        return self.last

    def close(self):
        try:
            self.p.kill()
            self.p.wait()
        except Exception:
            pass


class MediaLayer(Layer):
    """video and image"""

    def __init__(self, d, ctx):
        super().__init__(d, ctx)
        self.src = self.source_path()
        self.dec = None
        self.cache = None
        self.static_base = None
        self.mode = d.get('fit') or ('cover' if self.type == 'video' else 'contain')
        c = d.get('crop') or {}
        self.crop = (float(c.get('top', 0)), float(c.get('bottom', 0)), float(c.get('left', 0)), float(c.get('right', 0)))
        top, bottom, left, right = self.crop
        if self.type == 'video':
            self.pinfo = probe(self.src)
            sw0, sh0 = self.pinfo['w'], self.pinfo['h']
        else:
            self.pinfo = None
            try:
                with Image.open(self.src) as im:
                    sw0, sh0 = im.size
            except Exception:
                raise RenderError(f'layer "{self.name}" ({self.id}): cannot read image: {self.src}')
        if sw0 <= 0 or sh0 <= 0:
            raise RenderError(f'layer "{self.name}" ({self.id}): no picture stream in: {self.src}')
        self.sw = sw0 * (1 - left - right)
        self.sh = sh0 * (1 - top - bottom)
        self.bw, self.bh = fit_size(self.sw, self.sh, ctx.W0, ctx.H0, ctx.S, self.R, self.mode)
        k = fit_k(self.sw, self.sh, ctx.W0, ctx.H0, self.mode)
        self.w1, self.h1 = self.sw * k, self.sh * k             # unit box (scale 1) in project px
        self.has_alpha = self.type == 'image'
        self.static = self.type == 'image' or self.freeze
        self.src_in = float(d.get('src_in', 0))

    def vf(self):
        top, bottom, left, right = self.crop
        parts = []
        if any(self.crop):
            parts.append(f'crop=iw*{1 - left - right:.6f}:ih*{1 - top - bottom:.6f}:iw*{left:.6f}:ih*{top:.6f}')
        parts.append(f'scale={self.bw}:{self.bh}:flags=bicubic')
        parts.append(f'fps={self.ctx.fps}')
        parts.append('format=rgba')
        return ','.join(parts)

    def _cmd(self, ss, single):
        cmd = [media_tools.ffmpeg(), '-v', 'error', '-nostdin', '-ss', f'{ss:.4f}'] + media_tools.input_args(self.src) + ['-an', '-vf', self.vf()]
        if single:
            cmd += ['-frames:v', '1']
        return cmd + ['-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1']

    def _clamp_ss(self, ss):
        dur = self.pinfo['dur']
        if dur > 0:
            ss = min(ss, max(0.0, dur - 2.0 / self.ctx.fps))
        return max(0.0, ss)

    def decode_single(self, ss):
        for s in (self._clamp_ss(ss), max(0.0, self._clamp_ss(ss) - 0.5), 0.0):
            r = subprocess.run(self._cmd(s, True), capture_output=True)
            fs = self.bw * self.bh * 4
            if len(r.stdout) >= fs:
                return np.frombuffer(r.stdout[:fs], np.uint8).reshape(self.bh, self.bw, 4)
        return None

    def image_base(self):
        if self.static_base is None:
            im = Image.open(self.src).convert('RGBA')
            top, bottom, left, right = self.crop
            w, h = im.size
            if any(self.crop):
                im = im.crop((int(round(w * left)), int(round(h * top)), int(round(w * (1 - right))),
                              int(round(h * (1 - bottom)))))
            if im.size != (self.bw, self.bh):
                im = im.resize((self.bw, self.bh), Image.LANCZOS)
            self.static_base = np.asarray(im).copy()
        return self.static_base

    def base_at(self, t, single):
        if self.type == 'image':
            return self.image_base()
        if self.freeze:
            if self.static_base is None:
                self.static_base = self.decode_single(self.src_in)
            return self.static_base
        if single:
            return self.decode_single(self.src_in + (t - self.t0))
        if self.dec is None:
            self.dec = Decoder(self._cmd(self._clamp_ss(self.src_in + (t - self.t0)), False), self.bw, self.bh)
        return self.dec.next()

    def release(self):
        if self.dec is not None:
            self.dec.close()
            self.dec = None

    def patch(self, t, single):
        """-> (rgb, a, x0, y0) or None"""
        base = self.base_at(t, single)
        if base is None:
            return None
        if self.cache is not None:
            return self.cache
        S = self.ctx.S
        z = 1.0
        if self.zoom:
            z0, z1 = self.zoom
            dur = self.t1 - self.t0
            z = z0 + (z1 - z0) * ((t - self.t0) / dur if dur > 0 else 0)
        fx = self.fx_at(t)
        blur = any(e.get('type') == 'blur' and float(e.get('radius', 0)) > 0 for e in fx)
        res = None
        if self.affine:
            sg = max([float(e.get('radius', 0)) for e in fx if e.get('type') == 'blur'] or [0]) * S
            pl = affine_patch(base, (-self.bw / 2, -self.bh / 2), (self.bw / self.w1, self.bh / self.h1), self.w1, self.h1,
                              self.transform_at(t), z, S, self.ctx.WH, int(math.ceil(3 * sg)) + 2 if blur else 0)
            if pl is not None:
                res = apply_effects(pl[0], pl[1], pl[2], True, fx, S)
        else:
            pl = place(base, self.x * S, self.y * S, z, blur, self.ctx.WH)
            if pl is not None:
                rgba, x0, y0 = pl
                res = apply_effects(rgba, x0, y0, self.has_alpha, fx, S)
        if self.static and not self.zoom and not self.animated:
            self.cache = res
        return res


# ------------------------------------------------------------------ text
class TextLayer(Layer):
    def __init__(self, d, ctx):
        super().__init__(d, ctx)
        self.cache = None
        self.raster_cache = None
        self.w1 = self.h1 = 1.0

    def raster(self):
        """Glyph + shadow bitmap at raster scale R -> (rgba uint8, x0, y0) or None. Untransformed layers are drawn
        around their real position (as always); transformed ones around the box centre (0, 0) and placed by affine_patch."""
        d, ctx = self.d, self.ctx
        S = ctx.S
        size = float(d.get('size', 48)) * self.R * S
        font_id = d.get('font') or schema.DEFAULT_FONT
        face = ctx.face(font_id)
        primary, fb = ctx.pil_font(face, size), ctx.pil_font(ctx.face(fonts.FALLBACK_ID, quiet=True), size)
        primary_cmap = fonts.cmap(face) if face else frozenset()
        spacing = size * float(d.get('letter_spacing', 0))
        lh = size * float(d.get('line_height', 1.25))
        lines = str(d.get('text', 'Text')).split('\n')
        align = d.get('align', 'center')

        def font_for(c):
            if primary is None:
                return fb
            if c.isspace() or ord(c) in primary_cmap:
                return primary
            return fb if fb is not None else primary

        def runs(line):
            r = []
            for c in line:
                f = font_for(c)
                if r and r[-1][1] is f:
                    r[-1][0] += c
                else:
                    r.append([c, f])
            return r

        def line_width(line):
            if spacing == 0:
                return sum(f.getlength(s) for s, f in runs(line))
            return sum(font_for(c).getlength(c) for c in line) + spacing * max(len(line) - 1, 0)

        widths = [line_width(l) for l in lines]
        bw = max(widths) if widths else 0
        bh = len(lines) * lh
        cx, cy = (0.0, 0.0) if self.affine else (self.x * S, self.y * S)
        self.w1, self.h1 = max(bw, 1e-6) / (self.R * S), max(bh, 1e-6) / (self.R * S)
        left, top = cx - bw / 2, cy - bh / 2
        sh = d.get('shadow') or {}
        shadow_on = bool(sh.get('enabled', True))
        sigma = float(sh.get('blur', 9)) * S if shadow_on else 0
        pad = int(math.ceil(3 * sigma)) + int(math.ceil(size * 0.6)) + 4
        ox, oy = int(math.floor(left)) - pad, int(math.floor(top)) - pad
        cw, ch = int(math.ceil(bw)) + 2 * pad + 2, int(math.ceil(bh)) + 2 * pad + 2
        mask = Image.new('L', (cw, ch), 0)
        dr = ImageDraw.Draw(mask)
        for i, line in enumerate(lines):
            off = 0 if align == 'left' else (bw - widths[i]) if align == 'right' else (bw - widths[i]) / 2
            lx = left - ox + off
            ly = top - oy + i * lh + (lh - size) / 2
            if spacing == 0:
                for s, f in runs(line):
                    dr.text((lx, ly), s, font=f, fill=255, anchor='la')
                    lx += f.getlength(s)
            else:
                for c in line:
                    f = font_for(c)
                    dr.text((lx, ly), c, font=f, fill=255, anchor='la')
                    lx += f.getlength(c) + spacing
        At = np.asarray(mask).astype(np.float32) / 255.0
        A = At
        if shadow_on:
            shm = np.asarray(mask.filter(ImageFilter.GaussianBlur(sigma)) if sigma > 0.05 else mask).astype(np.float32) / 255.0
            shm = shm * float(sh.get('opacity', 0.7))
            A = At + shm * (1 - At)
        col = np.array(parse_color(d.get('color', schema.DEFAULT_TEXT_COLOR), (241, 239, 233)), np.float32)
        rgb = col[None, None, :] * (At / np.maximum(A, 1e-6))[..., None]
        ys, xs = np.nonzero(A > 0.002)
        if len(ys) == 0:
            return None
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        rgba = np.empty((y1 - y0, x1 - x0, 4), np.uint8)
        rgba[..., :3] = np.clip(rgb[y0:y1, x0:x1] + 0.5, 0, 255)
        rgba[..., 3] = np.clip(A[y0:y1, x0:x1] * 255 + 0.5, 0, 255)
        return rgba, ox + x0, oy + y0

    def patch(self, t, single):
        if self.cache is not None:
            return self.cache or None
        if self.raster_cache is None:
            with self.ctx._lock:            # FreeType faces are shared between layers and are not thread-safe
                self.raster_cache = self.raster() or False
        if not self.raster_cache:
            return None
        rgba, x0, y0 = self.raster_cache
        S = self.ctx.S
        fx = self.fx_at(t)
        if self.affine:
            blur = [float(e.get('radius', 0)) for e in fx if e.get('type') == 'blur' and float(e.get('radius', 0)) > 0]
            pl = affine_patch(rgba, (x0, y0), (self.R * S, self.R * S), self.w1, self.h1, self.transform_at(t), 1.0, S, self.ctx.WH,
                              int(math.ceil(3 * max(blur) * S)) + 2 if blur else 0)
            res = apply_effects(pl[0], pl[1], pl[2], True, fx, S) if pl is not None else None
        else:
            res = apply_effects(rgba, x0, y0, True, fx, S)
        if not self.animated:
            self.cache = res if res is not None else False
        return res


class ColorLayer(Layer):
    def color(self, t=0.0):
        c = np.array([[list(parse_color(self.d.get('color', '#000000')))]], np.uint8)
        px = np.concatenate([c, np.full((1, 1, 1), 255, np.uint8)], axis=2)
        fx = [e for e in self.fx_at(t) if e.get('type') != 'blur']
        rgb, _, _, _ = apply_effects(px, 0, 0, False, fx, 1)
        return rgb.reshape(3).astype(np.float32)


# ------------------------------------------------------------------ context
class Ctx:
    def face(self, font_id, quiet=False):
        """Registry face for a font id; unknown ids warn once and use inter-regular."""
        with self._lock:
            if font_id in self._faces:
                return self._faces[font_id]
            face = fonts.get_face(font_id, self.pdir)
            if face is None:
                if not quiet:
                    out(f'WARN font {font_id} not found')
                face = None if font_id == fonts.FALLBACK_ID else self.face(fonts.FALLBACK_ID, quiet=True)
            self._faces[font_id] = face
            return face

    def pil_font(self, face, size):
        if face is None:
            return None
        key = (face['file'], face['index'], size)
        with self._lock:
            if key not in self._pil:
                try:
                    self._pil[key] = ImageFont.truetype(face['file'], size, index=face['index'])
                except OSError:
                    self._pil[key] = None
            return self._pil[key]


def load(pdir):
    try:
        p = schema.load_project(pdir)
        unknown = [r for r in p.get('requires', []) if r not in schema.FEATURES]
        if unknown:
            raise RenderError(f'project requires features this Kurgu build does not have: {", ".join(unknown)} (update Kurgu)')
        return p
    except FileNotFoundError:
        raise RenderError(f'project.json not found in {pdir}')
    except schema.SchemaError as e:
        raise RenderError(str(e))


def even(v):
    return max(2, int(round(v / 2)) * 2)


def setup(pdir, project, draft, scale=None, size=None, fps=None):
    ctx = Ctx()
    ctx.pdir = pdir
    ctx._faces, ctx._pil = {}, {}
    ctx._lock = threading.RLock()
    ctx.W0, ctx.H0 = int(project['width']), int(project['height'])
    ctx.fps = float(fps if fps else project['fps'])
    if ctx.fps == int(ctx.fps):
        ctx.fps = int(ctx.fps)
    if size:
        ctx.W, ctx.H = even(size[0]), even(size[1])
        ctx.S = ctx.W / ctx.W0
    else:
        ctx.S = scale if scale else (0.5 if draft else 1.0)
        ctx.W = even(ctx.W0 * ctx.S)
        ctx.H = even(ctx.H0 * ctx.S)
    ctx.WH = (ctx.W, ctx.H)
    ctx.duration = float(project['duration'])
    ctx.bg = np.array(parse_color(project.get('background', '#000000')), np.float32)
    ctx.glob = project.get('global') or {}
    return ctx


def visual_layers(ctx, project, at=None):
    """Bottom-to-top list. With `at`, only layers active at that time are set up (fast single frames)."""
    L = []
    for d in reversed(project['layers']):
        if d.get('hidden'):
            continue
        if at is not None and not (at >= float(d['start']) - EPS and at < float(d['end']) - EPS):
            continue
        tp = d.get('type')
        if tp in ('video', 'image'):
            L.append(MediaLayer(d, ctx))
        elif tp == 'text':
            L.append(TextLayer(d, ctx))
        elif tp == 'color':
            L.append(ColorLayer(d, ctx))
    return L


def vignette_mask(ctx, g):
    W, H = ctx.W, ctx.H
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx, dy = xx + 0.5 - W / 2, yy + 0.5 - H / 2
    r = np.sqrt(dx * dx + dy * dy) / math.sqrt((W / 2) ** 2 + (H / 2) ** 2)
    return (1 - g * np.clip((r - 0.4) / 0.6, 0, 1) ** 2).astype(np.float32)[..., None]


def layer_op(k, t, single):
    """One layer at time t -> ('c', colour, opacity) | ('p', (rgb, a, x0, y0), opacity) | None. Touches only that layer's own state,
    so different layers may be evaluated on different threads (a layer's own frames must still run in order)."""
    if isinstance(k, ColorLayer):
        sc = k.val('opacity', t) * k.factor(t)
        return ('c', k.color(t), sc) if sc > 0 else None
    pt = k.patch(t, single)   # always called so a running decoder stays in step, even at opacity 0
    sc = k.val('opacity', t) * k.factor(t)
    if pt is None or sc <= 0:
        return None
    return ('p', pt, sc)


def frame_ops(ctx, L, t, single):
    """Every layer at t, bottom to top -> the ordered list of things to composite (no pixels are combined yet)."""
    ops = []
    for k in L:
        if not k.active(t):
            if k.t1 - EPS <= t:
                k.release()
            continue
        op = layer_op(k, t, single)
        if op is not None:
            ops.append(op)
    return ops


def compose_band(ctx, ops, buf, out, y0, y1, vig, fscale):
    """Composite rows y0:y1 of the frame. Every operation is per pixel, so bands are bit-identical to one full-frame pass."""
    b = buf[y0:y1]
    b[...] = ctx.bg
    for op in ops:
        if op[0] == 'c':
            c, sc = op[1], op[2]
            b += (c - b) * np.float32(sc) if sc < 0.9999 else (c - b)
        else:
            rgb, a, x0, py0 = op[1]
            blend(b, rgb, a, x0, py0 - y0, op[2])
    if vig is not None:
        b *= vig[y0:y1]
    if fscale is not None:
        b *= np.float32(fscale)
    np.add(b, 0.5, out=b)
    np.clip(b, 0, 255, out=b)
    np.copyto(out[y0:y1], b, casting='unsafe')


def fade_scale(ctx, t, fade_out):
    if fade_out > 0:
        f = clamp((ctx.duration - t) / fade_out)
        if f < 1:
            return f
    return None


def draw_frame(ctx, L, buf, t, single, vig, fade_out):
    ops = frame_ops(ctx, L, t, single)
    out_ = np.empty((ctx.H, ctx.W, 3), np.uint8)
    compose_band(ctx, ops, buf, out_, 0, ctx.H, vig, fade_scale(ctx, t, fade_out))
    return out_


def render_frames(ctx, L, N, n0, vig, fade, write, prog, threads=None):
    """Frames n0 .. n0+N-1 -> write(bytes). Layers are evaluated ahead on one lane (thread) per layer, the frame is composited in
    horizontal bands on a pool, and a writer thread feeds the encoder; the pixels equal the serial `draw_frame` exactly."""
    import queue
    from concurrent.futures import ThreadPoolExecutor
    nthreads = threads or max(1, min(8, os.cpu_count() or 1))
    depth = 3
    lanes = {}
    bands = ThreadPoolExecutor(nthreads)
    buf = np.zeros((ctx.H, ctx.W, 3), np.float32)
    hs = [ctx.H * i // nthreads for i in range(nthreads + 1)]
    wq = queue.Queue(maxsize=4)
    failed = []

    def writer():
        while True:
            fr = wq.get()
            if fr is None:
                return
            if failed:
                continue
            try:
                write(fr.tobytes())
            except BrokenPipeError:
                failed.append(1)

    wt = threading.Thread(target=writer, daemon=True)
    wt.start()

    def lane(k):
        ex = lanes.get(id(k))
        if ex is None:
            ex = lanes[id(k)] = ThreadPoolExecutor(1)
        return ex

    def submit(i):
        t = (n0 + i) / ctx.fps
        fs = []
        for k in L:
            if not k.active(t):
                if k.t1 - EPS <= t:
                    fs.append(lane(k).submit(k.release))
                continue
            fs.append(lane(k).submit(layer_op, k, t, False))
        return t, fs

    pend = []
    try:
        for i in range(min(depth, N)):
            pend.append(submit(i))
        for i in range(N):
            t, fs = pend.pop(0)
            if i + depth < N:
                pend.append(submit(i + depth))
            ops = [r for r in (f.result() for f in fs) if r is not None]
            out_ = np.empty((ctx.H, ctx.W, 3), np.uint8)
            fsc = fade_scale(ctx, t, fade)
            for f in [bands.submit(compose_band, ctx, ops, buf, out_, hs[j], hs[j + 1], vig, fsc) for j in range(nthreads) if hs[j + 1] > hs[j]]:
                f.result()
            wq.put(out_)
            if failed:
                break
            if i % 5 == 0:
                prog(i / N)
    finally:
        for ex in lanes.values():
            ex.shutdown(wait=True, cancel_futures=True)
        bands.shutdown(wait=True)
        wq.put(None)
        wt.join()


# ------------------------------------------------------------------ sound
def envelope_db(points, tt):
    pts = sorted((float(a), float(b)) for a, b in points)
    return np.interp(tt, [a for a, _ in pts], [b for _, b in pts])


def audio_layers(project):
    r = []
    for d in project['layers']:
        if d.get('hidden') or d.get('muted'):
            continue
        if d.get('type') in ('audio', 'video'):
            r.append(d)
    return r


AUDIO_BLOCK = 2 * SR           # samples per mixing block: peak memory stays ~100 MB whatever the timeline length


class _AudioStream:
    """One layer's decoded PCM, read block by block from an ffmpeg pipe (never the whole file in memory)."""

    def __init__(self, d, p, name, src_in, dur, tmp, k):
        self.d, self.p, self.name = d, p, name
        self.t1 = float(d.get('end', 0))
        self.left = 0            # samples still allowed (set by the caller)
        self.got = 0
        self.rest = b''
        self.eof = False
        self.log = open(os.path.join(tmp, f'dec{k}.log'), 'wb')
        self.proc = subprocess.Popen(
            [media_tools.ffmpeg(), '-v', 'error', '-nostdin', '-ss', f'{src_in:.4f}', '-t', f'{dur:.4f}'] + media_tools.input_args(p)
            + ['-vn', '-ac', '2', '-ar', str(SR), '-f', 'f32le', 'pipe:1'],
            stdout=subprocess.PIPE, stderr=self.log, stdin=subprocess.DEVNULL, bufsize=1 << 20)

    def read(self, n):
        """Up to n stereo frames -> float32 (m, 2); shorter (or empty) only at end of the stream."""
        want = n * 8
        while len(self.rest) < want and not self.eof:
            b = self.proc.stdout.read(want - len(self.rest))
            if not b:
                self.eof = True
            else:
                self.rest += b
        take = min(len(self.rest), want) // 8 * 8
        chunk, self.rest = self.rest[:take], self.rest[take:]
        self.got += take
        if self.eof and self.got == 0 and not self.rest:
            rc = self.proc.wait()
            if rc != 0:
                self.log.flush()
                with open(self.log.name, 'rb') as f:
                    err = f.read()[-200:].decode(errors='ignore')
                raise RenderError(f'layer "{self.name}" ({self.d.get("id")}): cannot decode sound: {err}')
        return np.frombuffer(chunk, np.float32).reshape(-1, 2)

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass
        try:
            self.proc.stdout.close()
            self.proc.wait()
        except Exception:
            pass
        self.log.close()


def _finish_stream(L):
    st, L['st'] = L['st'], None
    if st is not None:
        st.close()


def build_audio(ctx, project, tmp, prog, rng=None):
    """Mix in numpy block by block (constant memory), run one ffmpeg pass (loudnorm, fade_out, trim) -> path of a wav file."""
    N = int(round(ctx.duration * SR))
    layers = audio_layers(project)
    plan = []
    for i, d in enumerate(layers):
        name = d.get('name') or d.get('id')
        p = resolve(ctx.pdir, d.get('src', ''))
        if not os.path.isfile(p):
            raise RenderError(f'layer "{name}" ({d.get("id")}): file not found: {p}')
        pi = probe(p)
        if d.get('type') == 'video' and not pi['audio']:
            continue
        t0, t1 = float(d.get('start', 0)), float(d.get('end', 0))
        src_in = float(d.get('src_in', 0))
        if t0 < 0:
            src_in -= t0
            t0 = 0.0
        dur = t1 - t0
        if dur <= 0 or t0 >= ctx.duration:
            continue
        if rng and (t1 <= rng[0] or t0 >= rng[1]):
            continue
        s0 = int(round(t0 * SR))
        limit = min(int(round(dur * SR)), N - s0)
        if limit <= 0:
            continue
        plan.append({'d': d, 'p': p, 'name': name, 't1': t1, 'src_in': src_in, 'dur': dur, 's0': s0, 'limit': limit, 'st': None,
                     'grid': None, 'i': i})
    raw = os.path.join(tmp, 'mix.f32')
    have = False
    try:
        with open(raw, 'wb') as fo:
            for b0 in range(0, N, AUDIO_BLOCK):
                b1 = min(b0 + AUDIO_BLOCK, N)
                mix = np.zeros((b1 - b0, 2), np.float32)
                for L in plan:
                    d = L['d']
                    s0, lim = L['s0'], L['limit']
                    if s0 >= b1 or s0 + lim <= b0 or L.get('done'):
                        if L['st'] is not None and (s0 + lim <= b0 or L.get('done')):
                            _finish_stream(L)
                        continue
                    if L['st'] is None:
                        L['st'] = _AudioStream(d, L['p'], L['name'], L['src_in'], L['dur'], tmp, L['i'])
                    a0 = max(b0, s0)
                    a1 = min(b1, s0 + lim)
                    pcm = L['st'].read(a1 - a0)
                    n = len(pcm)
                    if n < a1 - a0:
                        L['done'] = True        # source shorter than the clip: the rest stays silent
                    if n <= 0:
                        continue
                    idx = a0 + np.arange(n)
                    tt = idx / SR
                    lt0 = float(d.get('start', 0))
                    db = float(d.get('volume_db', 0))
                    if anim.is_animated(d, ('volume_db',)):
                        # keyed / wiggled volume: evaluated on a 200 Hz control grid (layer-local time), linear in between
                        if L['grid'] is None:
                            grid = np.arange(0.0, (L['t1'] - lt0) + 0.005, 0.005)
                            L['grid'] = (grid, np.array([anim.value(d, 'volume_db', float(g)) for g in grid]))
                        db = np.interp(tt - lt0, L['grid'][0], L['grid'][1])
                    if d.get('envelope'):
                        db = db + envelope_db(d['envelope'], tt)
                    gain = np.power(10.0, db / 20.0) * fade_factor(d.get('fade_in'), d.get('fade_out'), lt0, L['t1'], tt)
                    gain = np.asarray(gain, np.float32)
                    mix[a0 - b0:a0 - b0 + n] += pcm * (gain.reshape(-1, 1) if gain.ndim else gain)
                    have = True
                    if a0 + n >= s0 + lim:
                        L['done'] = True
                    if L.get('done'):
                        _finish_stream(L)
                np.clip(mix, -8, 8, out=mix)
                mix.tofile(fo)
                prog(0.05 * b1 / max(N, 1))
    finally:
        for L in plan:
            if L['st'] is not None:
                L['st'].close()
    if N == 0:
        open(raw, 'wb').close()
    wav = os.path.join(tmp, 'sound.wav')
    lufs = ctx.glob.get('loudness_lufs', None)
    fade = float(ctx.glob.get('fade_out', 0) or 0)
    af = []
    if have and lufs is not None:
        af += [f'loudnorm=I={float(lufs)}:TP=-1.5:LRA=16', f'aresample={SR}']
    if fade > 0:
        af.append(f'afade=t=out:st={max(0.0, ctx.duration - fade):.4f}:d={fade:.4f}')
    r0, r1 = rng or (0.0, ctx.duration)
    af.append(f'atrim=start={r0:.4f}:end={r1:.4f}')
    af.append('asetpts=PTS-STARTPTS')
    cmd = [media_tools.ffmpeg(), '-v', 'error', '-nostdin', '-y', '-f', 'f32le', '-ar', str(SR), '-ac', '2', '-i', raw, '-af', ','.join(af),
           '-c:a', 'pcm_f32le', wav]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RenderError('sound pass failed: ' + r.stderr[-300:])
    return wav


# ------------------------------------------------------------------ main flow
CODEC_EXT = {'h264': ('mp4', 'mov', 'mkv'), 'h265': ('mp4', 'mov', 'mkv'), 'prores': ('mov',), 'vp9': ('webm', 'mkv'),
             'gif': ('gif',), 'wav': ('wav',), 'mp3': ('mp3',)}
EXT_CODEC = {'mp4': 'h264', 'mov': 'h264', 'mkv': 'h264', 'webm': 'vp9', 'gif': 'gif', 'wav': 'wav', 'mp3': 'mp3'}
AUDIO_ONLY = ('wav', 'mp3')


def hardware_encoder(o):
    """Name of the hardware encoder to use for this export, or None (software libx264/libx265). `encoder` is software
    (default: bit-exact, reproducible), auto (hardware when a working one exists) or hardware (same, but warns when none)."""
    mode = getattr(o, 'encoder', None) or 'software'
    if mode == 'software' or o.codec not in ('h264', 'h265'):
        return None
    name = media_tools.hw_encoders().get(o.codec)
    if not name and mode == 'hardware':
        out(f'WARN no hardware {o.codec} encoder available, using software')
    return name


def encoder_args(o, dst_ext):
    """(video codec args, audio codec args) for the chosen codec."""
    preset = o.preset
    hw = hardware_encoder(o)
    if hw:
        crf = o.crf if o.crf is not None else (18 if o.codec == 'h264' else 22)
        v = media_tools.hw_video_args(hw, crf=crf, bitrate=o.bitrate) + ['-pix_fmt', 'yuv420p']
    elif o.codec == 'h264':
        v = ['-c:v', 'libx264', '-preset', preset or 'medium', '-pix_fmt', 'yuv420p']
        v += ['-b:v', o.bitrate] if o.bitrate else ['-crf', str(o.crf if o.crf is not None else 18)]
    elif o.codec == 'h265':
        v = ['-c:v', 'libx265', '-preset', preset or 'medium', '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1', '-x265-params', 'log-level=error']
        v += ['-b:v', o.bitrate] if o.bitrate else ['-crf', str(o.crf if o.crf is not None else 22)]
    elif o.codec == 'prores':
        v = ['-c:v', 'prores_ks', '-profile:v', '3', '-vendor', 'apl0', '-pix_fmt', 'yuv422p10le']
    elif o.codec == 'vp9':
        v = ['-c:v', 'libvpx-vp9', '-pix_fmt', 'yuv420p', '-deadline', 'good', '-cpu-used', '5', '-row-mt', '1']
        v += ['-b:v', o.bitrate] if o.bitrate else ['-crf', str(o.crf if o.crf is not None else 32), '-b:v', '0']
    else:
        v = []
    if o.codec == 'prores':
        a = ['-c:a', 'pcm_s16le']
    elif o.codec == 'vp9':
        a = ['-c:a', 'libopus', '-b:a', o.audio_bitrate or '128k']
    else:
        a = ['-c:a', 'aac', '-b:a', o.audio_bitrate or '192k']
    if dst_ext in ('mp4', 'mov') and o.codec in ('h264', 'h265'):
        v += ['-movflags', '+faststart']
    return v, a


def render_full(pdir, project, o, output):
    ext = os.path.splitext(output)[1].lstrip('.').lower()
    audio_only = o.codec in AUDIO_ONLY
    ctx = setup(pdir, project, o.draft, o.scale, o.size, o.fps)
    if o.codec == 'gif' and not o.fps and ctx.fps > 15:
        ctx.fps = 15
    t0 = clamp(o.range_from or 0.0, 0.0, ctx.duration)
    t1 = clamp(o.range_to if o.range_to is not None else ctx.duration, 0.0, ctx.duration)
    if t1 - t0 < 1.0 / ctx.fps:
        raise RenderError('range is empty or shorter than one frame')
    rng = (t0, t1) if (t0 > 0 or t1 < ctx.duration) else None
    want_audio = audio_only or (o.audio and o.codec != 'gif')
    L = [] if audio_only else visual_layers(ctx, project)
    tmp = os.path.join(pdir, '.kurgu', 'render_tmp')
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    last = [-1.0]
    gif = o.codec == 'gif'
    top = 0.9 if (gif or audio_only) else 0.98

    def prog(x):
        x = round(clamp(x), 3)
        if x - last[0] >= 0.005 or x >= 1:
            last[0] = x
            out(f'PROGRESS {x}')

    enc = None
    try:
        prog(0)
        wav = build_audio(ctx, project, tmp, prog, rng) if want_audio else None
        os.makedirs(os.path.dirname(os.path.abspath(output)) or '.', exist_ok=True)
        part = os.path.join(tmp, 'out.' + ext)
        if audio_only:
            cmd = [media_tools.ffmpeg(), '-v', 'error', '-nostdin', '-y'] + media_tools.input_args(wav)
            cmd += ['-c:a', 'libmp3lame', '-b:a', o.audio_bitrate or '192k'] if o.codec == 'mp3' else ['-c:a', 'pcm_s16le']
            r = subprocess.run(cmd + [part], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                raise RenderError('audio encode failed: ' + r.stderr[-300:])
        else:
            grain = float(ctx.glob.get('grain', 0) or 0)
            vf = []
            if grain > 0:
                vf.append(f'noise=alls={grain}:allf=t')
            if not gif:
                vf.append('format=yuv422p10le' if o.codec == 'prores' else 'format=yuv420p')
            elog = open(os.path.join(tmp, 'enc.log'), 'w')
            cmd = [media_tools.ffmpeg(), '-v', 'error', '-nostdin', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{ctx.W}x{ctx.H}',
                   '-framerate', str(ctx.fps), '-i', 'pipe:0']
            dst = os.path.join(tmp, 'gif_src.mkv') if gif else part
            if gif:
                cmd += (['-vf', ','.join(vf)] if vf else []) + ['-c:v', 'ffv1', '-t', f'{t1 - t0:.4f}', dst]
            else:
                vargs, aargs = encoder_args(o, ext)
                cmd += ['-i', wav, '-map', '0:v', '-map', '1:a'] if wav else ['-map', '0:v', '-an']
                cmd += ['-vf', ','.join(vf)] + vargs + ['-r', str(ctx.fps)] + (aargs if wav else []) + ['-t', f'{t1 - t0:.4f}', dst]
            enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=elog)
            n0 = int(round(t0 * ctx.fps))
            N = max(1, int(round(t1 * ctx.fps)) - n0)
            vg = float(ctx.glob.get('vignette', 0) or 0)
            vig = vignette_mask(ctx, vg) if vg > 0 else None
            fade = float(ctx.glob.get('fade_out', 0) or 0)
            try:
                render_frames(ctx, L, N, n0, vig, fade, enc.stdin.write, lambda x: prog(0.05 + (top - 0.05) * x))
                enc.stdin.close()
            except BrokenPipeError:
                pass
            rc = enc.wait()
            elog.close()
            if rc != 0:
                raise RenderError('ffmpeg encoder failed: ' + open(os.path.join(tmp, 'enc.log')).read()[-400:])
            if gif:
                pal = os.path.join(tmp, 'pal.png')
                for step, c in enumerate(([media_tools.ffmpeg(), '-v', 'error', '-nostdin', '-y', '-i', dst, '-vf', 'palettegen=stats_mode=diff', pal],
                                          [media_tools.ffmpeg(), '-v', 'error', '-nostdin', '-y', '-i', dst, '-i', pal, '-lavfi',
                                           'paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle', '-loop', '0', part])):
                    r = subprocess.run(c, capture_output=True, text=True, encoding="utf-8", errors="replace")
                    if r.returncode != 0:
                        raise RenderError('gif encode failed: ' + r.stderr[-300:])
                    prog(0.95 if step == 0 else 0.99)
        shutil.move(part, output)       # same filesystem in practice: a rename; a partial file never sits at `output`
        prog(1)
        out(f'DONE {output}')
    finally:
        if enc is not None and enc.poll() is None:
            try:
                enc.kill()
                enc.wait()
            except Exception:
                pass
        for k in L:
            k.release()
        shutil.rmtree(tmp, ignore_errors=True)


def render_frame(pdir, project, draft, t, output):
    ctx = setup(pdir, project, draft)
    L = visual_layers(ctx, project, at=t)
    try:
        buf = np.zeros((ctx.H, ctx.W, 3), np.float32)
        vg = float(ctx.glob.get('vignette', 0) or 0)
        vig = vignette_mask(ctx, vg) if vg > 0 else None
        fade = float(ctx.glob.get('fade_out', 0) or 0)
        fr = draw_frame(ctx, L, buf, t, True, vig, fade)
    finally:
        for k in L:
            k.release()
    os.makedirs(os.path.dirname(os.path.abspath(output)) or '.', exist_ok=True)
    Image.fromarray(fr, 'RGB').save(output)
    out(f'DONE {os.path.abspath(output)}')


def parse_size(v):
    try:
        w, h = str(v).lower().split('x')
        w, h = int(w), int(h)
        if w < 2 or h < 2:
            raise ValueError
        return (w, h)
    except ValueError:
        raise argparse.ArgumentTypeError('--size must look like 1280x720')


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page; paths may hold emoji / Turkish letters
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description='Render a Kurgu project')
    ap.add_argument('project_dir')
    ap.add_argument('--draft', action='store_true', help='preset: half resolution, crf 26, veryfast, <stem>_draft.mp4')
    ap.add_argument('--output', help='output file (extension picks the container; png with --frame)')
    ap.add_argument('--frame', type=float, help='render one PNG frame at this time (seconds)')
    ap.add_argument('--codec', choices=sorted(CODEC_EXT), help='default: from the output extension, else h264')
    ap.add_argument('--format', help='container / file extension (mp4, mov, mkv, webm, gif, wav, mp3)')
    ap.add_argument('--crf', type=int, help='constant quality (h264/h265/vp9)')
    ap.add_argument('--bitrate', help='target video bitrate, e.g. 8000k or 8M (instead of --crf)')
    ap.add_argument('--preset', help='x264/x265 speed preset')
    ap.add_argument('--encoder', choices=('software', 'auto', 'hardware'), default='software',
                    help='software = libx264/libx265 (default, reproducible); auto/hardware = VideoToolbox / NVENC / QSV / AMF when available')
    ap.add_argument('--scale', type=float, help='scale factor of the sequence size, e.g. 0.5')
    ap.add_argument('--size', type=parse_size, help='exact output size WxH')
    ap.add_argument('--fps', type=float, help='override the sequence frame rate')
    ap.add_argument('--from', dest='range_from', type=float, help='range start (s)')
    ap.add_argument('--to', dest='range_to', type=float, help='range end (s)')
    ap.add_argument('--no-audio', dest='audio', action='store_false', help='no sound track')
    ap.add_argument('--audio-bitrate', help='e.g. 192k')
    a = ap.parse_args(argv)
    pdir = os.path.abspath(a.project_dir)
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))    # cancel: let `finally` clean the temp dir
    except (ValueError, OSError, AttributeError):
        pass
    try:
        project = load(pdir)
        if a.frame is not None:
            output = a.output or os.path.join(pdir, '.kurgu', 'frame.png')
            render_frame(pdir, project, a.draft, a.frame, os.path.abspath(output))
        else:
            fmt = (a.format or '').lstrip('.').lower() or None
            if a.output:
                output = os.path.abspath(a.output)
                if fmt and os.path.splitext(output)[1].lstrip('.').lower() != fmt:
                    output = os.path.splitext(output)[0] + '.' + fmt
            else:
                output = resolve(pdir, project['output'])
                if fmt:
                    output = os.path.splitext(output)[0] + '.' + fmt
                if a.draft:
                    stem, ext = os.path.splitext(output)
                    output = f'{stem}_draft{ext or ".mp4"}'
            ext = os.path.splitext(output)[1].lstrip('.').lower()
            a.codec = a.codec or EXT_CODEC.get(ext, 'h264')
            if ext not in CODEC_EXT[a.codec]:
                raise RenderError(f'codec {a.codec} cannot be written to .{ext or "?"} (use {", ".join("." + e for e in CODEC_EXT[a.codec])})')
            if a.draft and a.crf is None and not a.bitrate:
                a.crf = 26
            if a.draft and not a.preset:
                a.preset = 'veryfast'
            if a.bitrate and a.codec in ('prores', 'gif', 'wav', 'mp3'):
                a.bitrate = None
            if a.bitrate and str(a.bitrate).isdigit():
                a.bitrate += 'k'
            if a.range_from is not None and a.range_to is not None and a.range_to <= a.range_from:
                raise RenderError('--to must be greater than --from')
            render_full(pdir, project, a, output)
    except (RenderError, media_tools.MediaToolError) as e:
        out(f'ERROR {e}')
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        out(f'ERROR unexpected error: {type(e).__name__}: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
