"""Shared helpers for the example-media generators (numpy + Pillow + ffmpeg only).

Everything here is deterministic: fixed seeds, no clocks, no downloads.
"""
import os
import subprocess

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MEDIA = os.path.join(ROOT, "templates", "_media")
VIDEO_DIR = os.path.join(MEDIA, "video")
AUDIO_DIR = os.path.join(MEDIA, "audio")
SFX_DIR = os.path.join(MEDIA, "sfx")

FPS = 30
LOOP_SECONDS = 8
N_FRAMES = FPS * LOOP_SECONDS          # a loop is exactly 240 frames, frame 240 == frame 0
RW, RH = 960, 540                      # internal resolution, upscaled to 1080p by ffmpeg


def ensure(path):
    os.makedirs(path, exist_ok=True)
    return path


def hex_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def grid(w=RW, h=RH):
    """x in 0..1 (columns), y in 0..1 (rows, 0 = top), plus aspect-corrected u,v centred at 0."""
    x = (np.arange(w, dtype=np.float32) + 0.5) / w
    y = (np.arange(h, dtype=np.float32) + 0.5) / h
    X, Y = np.meshgrid(x, y)
    return X, Y


def tonemap(img, k=1.0):
    """Soft shoulder so summed light never clips harshly."""
    return 1.0 - np.exp(-np.maximum(img, 0) * k)


def to_u8(img):
    return (np.clip(img, 0, 1) * 255.0 + 0.5).astype(np.uint8)


def write_video(path, frame_fn, n_frames=N_FRAMES, out_size=(1920, 1080), crf=21, w=RW, h=RH):
    """Pipe frames (float RGB 0..1, shape (h, w, 3)) into ffmpeg: lanczos upscale, gentle static dither, x264."""
    ensure(os.path.dirname(path))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(FPS), "-i", "-",
           "-vf", f"scale={out_size[0]}:{out_size[1]}:flags=lanczos,noise=alls=2:allf=u:all_seed=7,format=yuv420p",
           "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-g", "60", "-an",
           "-movflags", "+faststart", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n_frames):
        p.stdin.write(to_u8(frame_fn(i, 2 * np.pi * i / n_frames)).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError("ffmpeg failed for " + path)
