"""Music beds and sound effects, synthesised in numpy. No samples, no recordings, no third-party material.

Three 32 s beds at -16 LUFS integrated (each loops cleanly: all tails and reverbs wrap around the buffer end):
  lofi_keys      90 bpm, 12 bars  electric-piano chords, soft drums, warm bass, a small melody
  cinematic_pad  60 bpm,  8 bars  slow pad chords, soft pulse arpeggio with echoes, heartbeat kick
  upbeat_pluck  120 bpm, 16 bars  plucked arpeggio, bounce bass, four-on-the-floor, claps, lead in the last half
Effects (mono, 44.1 kHz wav): whoosh, riser, hit, click, pop, tick, shimmer.
"""
import os
import re
import subprocess
import wave

import numpy as np

from _common import AUDIO_DIR, SFX_DIR, ensure

SR = 44100
LOOP_S = 32
N = SR * LOOP_S
TARGET_LUFS = -16.0


# ------------------------------------------------------------------ helpers
def mtof(m):
    return 440.0 * 2.0 ** ((np.asarray(m, dtype=np.float64) - 69.0) / 12.0)


def tt(dur):
    return np.arange(int(dur * SR), dtype=np.float64) / SR


def add_wrap(buf, start_s, sig, gain=1.0, pan=0.0):
    """Mix `sig` into stereo `buf` at `start_s`, wrapping past the end (seamless loops). pan -1..1."""
    n = len(sig)
    idx = (int(round(start_s * SR)) + np.arange(n)) % buf.shape[1]
    l = gain * np.cos((pan + 1) * np.pi / 4)
    r = gain * np.sin((pan + 1) * np.pi / 4)
    buf[0, idx] += sig * l
    buf[1, idx] += sig * r


def fft_lowpass(x, fc, order=4):
    """Zero-phase, circular lowpass (smooth Butterworth-shaped magnitude)."""
    X = np.fft.rfft(x, axis=-1)
    f = np.fft.rfftfreq(x.shape[-1], 1 / SR)
    return np.fft.irfft(X / np.sqrt(1 + (f / fc) ** (2 * order)), n=x.shape[-1], axis=-1)


def fft_highpass(x, fc, order=2):
    X = np.fft.rfft(x, axis=-1)
    f = np.fft.rfftfreq(x.shape[-1], 1 / SR)
    g = 1 / np.sqrt(1 + (fc / np.maximum(f, 1e-3)) ** (2 * order))
    return np.fft.irfft(X * g, n=x.shape[-1], axis=-1)


def reverb_ir(seed, seconds, damp=5500, pre=0.012):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal(n) * np.exp(-t * (6.9 / seconds))
    ir = fft_lowpass(ir, damp, 2)
    ir[: int(pre * SR)] = 0
    ir = np.concatenate([np.zeros(int(pre * SR)), ir])[:n]
    return ir / np.sqrt(np.sum(ir ** 2))


def circ_reverb(buf, seconds, wet, seed=1, damp=5500):
    """Circular convolution on a stereo bus: tails wrap around the loop point."""
    out = np.zeros_like(buf)
    for ch in range(2):
        ir = np.zeros(buf.shape[1])
        r = reverb_ir(seed + ch * 17, seconds, damp)
        ir[: len(r)] = r
        out[ch] = np.fft.irfft(np.fft.rfft(buf[ch]) * np.fft.rfft(ir), n=buf.shape[1])
    return out * wet


def env_ar(n, attack, release, curve=2.0):
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    r = np.clip((n / SR - t) / max(release, 1e-4), 0, 1)
    return a ** curve * r ** curve


def _dbg(**buses):
    if os.environ.get("KURGU_DEBUG"):
        print("  bus rms dB:", {k: round(20 * np.log10(np.sqrt((v ** 2).mean()) + 1e-9), 1) for k, v in buses.items()})


def seconds_per_beat(bpm):
    return 60.0 / bpm


# ------------------------------------------------------------------ instruments
def rhodes(f, dur, vel=1.0, seed=0):
    t = tt(dur)
    idx = 0.4 + 2.2 * np.exp(-t * 7.5)                        # tine brightness dying away
    out = 0
    for det, w in ((1.0, 0.55), (1.0035, 0.45)):
        ph = 2 * np.pi * f * det * t
        out = out + w * np.sin(ph + idx * np.sin(ph * 1.0)) 
    bell = 0.12 * np.sin(2 * np.pi * f * 7.0 * t + 1.0) * np.exp(-t * 26)
    amp = np.minimum(t / 0.004, 1) * np.exp(-t * 1.5) * np.minimum((dur - t) / 0.25, 1)
    return (out + bell) * amp * vel * 0.5


def soft_pluck(f, dur, vel=1.0, bright=1.0):
    """Additive pluck: upper partials die faster, like a string."""
    t = tt(dur)
    out = 0
    for h in range(1, 9):
        if f * h > 9000:
            break
        out = out + (1.0 / h ** 1.15) * np.sin(2 * np.pi * f * h * t + 0.3 * h) * np.exp(-t * (2.2 + (2.6 * h * bright)))
    amp = np.minimum(t / 0.003, 1) * np.minimum((dur - t) / 0.06, 1)
    return out * amp * vel


def pad_voice(f, dur, attack, release, seed=0):
    """Band-limited detuned saw stack, gently rolled off."""
    t = tt(dur)
    out = 0
    rng = np.random.default_rng(seed)
    for det in (-9, -4, 0, 4, 9):                             # cents
        ff = f * 2 ** (det / 1200)
        ph0 = rng.random() * 6.283
        for h in range(1, 40):
            if ff * h > 2600:
                break
            out = out + np.sin(2 * np.pi * ff * h * t + ph0 * h) / h ** 1.35
    return out * env_ar(len(t), attack, release, 1.6) * 0.16


def sine_bass(f, dur, vel=1.0, pluck=0.0):
    t = tt(dur)
    s = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 5) + 0.15 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t * 9)
    amp = np.minimum(t / 0.008, 1) * np.minimum((dur - t) / 0.05, 1) * np.exp(-t * pluck)
    return s * amp * vel


def kick(vel=1.0, depth=48, decay=0.32):
    t = tt(0.5)
    f = depth + 90 * np.exp(-t * 32)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t / decay * 3.2) * np.minimum(t / 0.002, 1) * vel


def _noise(n, seed):
    return np.random.default_rng(seed).standard_normal(n)


def hat(seed, vel=1.0, open_=False):
    t = tt(0.22 if open_ else 0.07)
    nz = np.diff(_noise(len(t) + 1, seed))                     # crude highpass
    nz = fft_highpass(nz, 6500, 2)
    return nz * np.exp(-t * (14 if open_ else 55)) * vel * 0.55


def snare(seed, vel=1.0, tone=185, decay=0.16):
    t = tt(0.4)
    nz = fft_highpass(_noise(len(t), seed), 900, 2)
    nz = fft_lowpass(nz, 7500, 2)
    body = np.sin(2 * np.pi * tone * t * (1 + 0.35 * np.exp(-t * 40)))
    return (nz * 0.7 * np.exp(-t / decay * 3.4) + body * 0.55 * np.exp(-t * 26)) * vel * np.minimum(t / 0.001, 1)


def clap(seed, vel=1.0):
    t = tt(0.32)
    nz = fft_highpass(fft_lowpass(_noise(len(t), seed), 5200, 2), 900, 2)
    bursts = sum(np.exp(-np.maximum(t - d, 0) * 90) * (t >= d) for d in (0.0, 0.011, 0.022)) * 0.6
    tail = np.exp(-t * 16) * (t >= 0.03)
    return nz * (bursts + tail * 0.9) * vel * 0.9


def circ_delay(buf, delay_s, feedback, taps=4, wet=1.0):
    out = np.zeros_like(buf)
    cur = buf
    for k in range(1, taps + 1):
        cur = np.roll(cur, int(delay_s * SR), axis=1) * feedback
        # ping-pong-ish: swap channels each tap
        out += cur[::-1] if k % 2 else cur
    return out * wet


# ------------------------------------------------------------------ loudness / io
def measure_lufs(path):
    r = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)
    return float(m[-1]), (float(tp[-1]) if tp else 0.0)


def write_wav(path, x):
    """x: (channels, n) float in -1..1 -> 16-bit wav"""
    x = np.atleast_2d(x)
    pcm = (np.clip(x, -1, 1).T * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[0])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def master(name, buf):
    """Normalise to TARGET_LUFS, soft-limit, write m4a (AAC 160k)."""
    ensure(AUDIO_DIR)
    tmp = os.path.join(AUDIO_DIR, f".{name}.tmp.wav")
    buf = buf - buf.mean(axis=1, keepdims=True)
    x = buf / (np.abs(buf).max() + 1e-9) * 0.5
    for _ in range(3):
        write_wav(tmp, np.tanh(x * 1.0) / np.tanh(1.0) * 0.98 if False else np.clip(x, -0.99, 0.99))
        lufs, _tp = measure_lufs(tmp)
        x = x * 10 ** ((TARGET_LUFS - lufs) / 20)
        if abs(TARGET_LUFS - lufs) < 0.05:
            break
    peak = np.abs(x).max()
    if peak > 0.89:                                             # -1 dBFS ceiling: soft knee limiter, then re-measure
        x = np.tanh(x / 0.89) * 0.89
        lufs, _ = measure_lufs_from(tmp, x)
        x = x * 10 ** ((TARGET_LUFS - lufs) / 20)
    write_wav(tmp, np.clip(x, -0.98, 0.98))
    out = os.path.join(AUDIO_DIR, name + ".m4a")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-t", str(LOOP_S), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out], check=True)
    lufs, tp = measure_lufs(out)
    os.remove(tmp)
    return out, lufs, tp


def measure_lufs_from(tmp, x):
    write_wav(tmp, np.clip(x, -0.99, 0.99))
    return measure_lufs(tmp)


# ------------------------------------------------------------------ track 1: lo-fi keys
def lofi_keys():
    bpm = 90
    spb = seconds_per_beat(bpm)                                # 0.6667 s; 12 bars * 4 beats = 48 beats = 32 s
    rng = np.random.default_rng(101)
    keys = np.zeros((2, N))
    bass = np.zeros((2, N))
    drums = np.zeros((2, N))
    chords = [([53, 57, 60, 64], 38), ([53, 59, 62, 64], 43), ([55, 59, 62, 64], 36), ([55, 59, 60, 64], 45)]
    mel = [[(0.5, 69, 1.0), (2, 72, 0.5), (2.5, 74, 1.5)],
           [(1, 71, 1.0), (2, 69, 0.5), (2.5, 67, 1.5)],
           [(0.5, 76, 1.5), (2, 74, 0.5), (2.5, 72, 1.0)],
           [(1, 71, 1.0), (2, 72, 0.5), (3, 76, 1.0)]]
    for bar in range(12):
        rep, k = divmod(bar, 4)
        notes, root = chords[k]
        t0 = bar * 4 * spb
        # chord comping: hit on 1 (long), stab on the "and" of 2 (short)
        for stab_beat, dur, vel in ((0.0, 2.2 * spb, 0.85), (2.5, 1.2 * spb, 0.6)):
            for j, m in enumerate(notes):
                add_wrap(keys, t0 + stab_beat * spb + j * 0.011 + rng.uniform(0, 0.006),
                         rhodes(mtof(m), dur, vel * (0.9 + 0.1 * rng.random())), 0.42, pan=-0.25 + 0.16 * j)
        # bass
        for beat, dur, vel in ((0, 1.6, 1.0), (2.5, 1.0, 0.8), (3.5, 0.5, 0.6)):
            m = root + (12 if beat == 3.5 else 0)
            add_wrap(bass, t0 + beat * spb, sine_bass(mtof(m), dur * spb, vel, pluck=0.6), 0.55)
        # melody from the second phrase on (rep 1 and 2), a fourth higher... same line, octave up in rep 2
        if rep >= 1:
            for beat, m, dur in mel[k]:
                m2 = m + (12 if (rep == 2 and beat > 2) else 0)
                add_wrap(keys, t0 + beat * spb, rhodes(mtof(m2), dur * spb + 0.3, 0.7), 0.30, pan=0.25)
        # drums
        for beat, v in ((0, 1.0), (2.5, 0.75)) if rep == 0 else ((0, 1.0), (1.75, 0.5), (2.5, 0.8)):
            add_wrap(drums, t0 + beat * spb, kick(v, 46, 0.30), 0.62)
        for beat in (1, 3):
            add_wrap(drums, t0 + beat * spb + 0.004, snare(int(rng.integers(1e9)), 0.75), 0.32, pan=0.1)
        for e in range(8):
            beat = e * 0.5 + (0.09 if e % 2 else 0.0)            # swung eighths
            v = (0.9 if e % 2 == 0 else 0.55) * (0.8 + 0.2 * rng.random())
            add_wrap(drums, t0 + beat * spb, hat(int(rng.integers(1e9)), v, open_=(e == 7 and bar % 2 == 1)), 0.20, pan=0.3)
    wet = circ_reverb(keys, 1.8, 0.22, seed=3, damp=4200)
    keys = fft_lowpass(keys + wet, 6800, 2)
    bass = fft_lowpass(bass, 700, 3)
    drums = fft_lowpass(drums, 9000, 2)
    _dbg(keys=keys, bass=bass, drums=drums)
    mix = keys * 1.5 + bass * 0.36 + drums * 1.0
    mix = np.tanh(mix * 1.1)                                     # tape warmth
    mix = fft_lowpass(mix, 8500, 2)
    return master("lofi_keys", mix)


# ------------------------------------------------------------------ track 2: cinematic pad + pulse
def cinematic_pad():
    bpm = 60
    spb = seconds_per_beat(bpm)                                # 1 s; bar = 4 s; 8 bars
    rng = np.random.default_rng(202)
    pad = np.zeros((2, N))
    pulse = np.zeros((2, N))
    low = np.zeros((2, N))
    perc = np.zeros((2, N))
    chords = [([57, 60, 64, 69], [33]), ([53, 57, 60, 65], [29]), ([55, 60, 64, 67], [36]), ([55, 59, 62, 67], [31])]
    arp = [0, 2, 1, 2, 3, 2, 1, 2]
    for c, (notes, root) in enumerate(chords):
        t0 = c * 8.0
        for j, m in enumerate(notes):
            add_wrap(pad, t0 - 0.5, pad_voice(mtof(m), 9.6, 2.6, 2.8, seed=c * 7 + j), 1.0, pan=-0.6 + 0.4 * j)
        add_wrap(low, t0 - 0.1, sine_bass(mtof(root[0]), 8.2, 0.9, 0.0) * env_ar(int(8.2 * SR), 1.5, 1.8, 1.0), 0.7)
        # pulse: eighth notes, plucked chord tones
        for step in range(16):
            ts = t0 + step * 0.5
            bar_pos = ts / 32.0
            g = 0.30 + 0.70 * np.clip(np.sin(np.pi * ((ts - 4) / 32.0)), 0, 1) ** 0.7
            note = notes[arp[step % 8]] + (12 if step % 8 in (3, 7) else 0)
            add_wrap(pulse, ts, soft_pluck(mtof(note + 12), 1.4, 0.55 * g, bright=0.7), 0.55, pan=(-0.4 if step % 2 else 0.4))
    # heartbeat kick on each bar (quiet), a deeper hit every 2nd bar
    for bar in range(8):
        add_wrap(perc, bar * 4.0, kick(0.9, 42, 0.5), 0.55 if bar % 2 == 0 else 0.35)
        add_wrap(perc, bar * 4.0 + 2.0, kick(0.5, 45, 0.35), 0.18)
    pad, low, pulse, perc = pad * 0.35, low * 0.42, pulse * 2.4, perc * 2.2
    pulse = pulse + circ_delay(pulse, 0.75, 0.42, 5, 0.6)
    pad = fft_lowpass(pad, 3400, 2)
    wet = circ_reverb(pad * 0.7 + pulse * 0.6, 3.2, 0.5, seed=9, damp=4200)
    _dbg(pad=pad, pulse=pulse, low=low, perc=perc, wet=wet)
    mix = pad + pulse + low + perc + wet
    mix = fft_highpass(mix, 28, 2)
    mix = np.tanh(mix * 0.9)
    return master("cinematic_pad", mix)


# ------------------------------------------------------------------ track 3: upbeat pluck
def upbeat_pluck():
    bpm = 120
    spb = seconds_per_beat(bpm)                                # 0.5 s; bar 2 s; 16 bars
    rng = np.random.default_rng(303)
    arpb = np.zeros((2, N))
    pad = np.zeros((2, N))
    bass = np.zeros((2, N))
    lead = np.zeros((2, N))
    drums = np.zeros((2, N))
    duck_events = []
    chords = [([60, 64, 67], 36), ([59, 62, 67], 43), ([57, 60, 64], 33), ([60, 65, 69], 41)]
    lead_l = [[(0, 76, .5), (.5, 76, .5), (1, 74, .5), (1.5, 72, 1.0), (3, 67, 1)],
              [(0, 74, .5), (.5, 74, .5), (1, 72, .5), (1.5, 71, 1.0), (3, 67, 1)],
              [(0, 72, .5), (.5, 76, .5), (1, 76, .5), (1.5, 79, 1.0), (3, 76, 1)],
              [(0, 77, 1.0), (1, 76, .5), (1.5, 74, .5), (2, 72, 2.0)]]
    for bar in range(16):
        k = bar % 4
        notes, root = chords[k]
        t0 = bar * 4 * spb
        section = bar // 4
        # arpeggio (8ths, up-down over chord tones + octave)
        seq = [notes[0], notes[1], notes[2], notes[0] + 12, notes[2], notes[1], notes[0] + 12, notes[1] + 12]
        for e in range(8):
            v = 0.85 if e % 2 == 0 else 0.6
            add_wrap(arpb, t0 + e * 0.5 * spb, soft_pluck(mtof(seq[e] + 12), 0.5, v * (0.9 + 0.1 * rng.random())), 0.36, pan=-0.35 + 0.1 * e)
        # pad chord (soft sustained)
        for j, m in enumerate(notes):
            add_wrap(pad, t0, pad_voice(mtof(m), 4 * spb + 0.3, 0.05, 0.5, seed=bar * 3 + j), 0.20, pan=-0.4 + 0.4 * j)
        # bounce bass
        for beat in range(4):
            add_wrap(bass, t0 + beat * spb, sine_bass(mtof(root), 0.34, 1.0, 5.0), 0.7)
            add_wrap(bass, t0 + beat * spb + 0.5 * spb, sine_bass(mtof(root + 12), 0.2, 0.55, 8.0), 0.5)
        # drums
        if section >= 0:
            for e in range(8):
                if e % 2 == 1:
                    add_wrap(drums, t0 + e * 0.5 * spb, hat(int(rng.integers(1e9)), 0.8, open_=True), 0.16, pan=0.3)
                else:
                    add_wrap(drums, t0 + e * 0.5 * spb, hat(int(rng.integers(1e9)), 0.35), 0.12, pan=-0.3)
        if section >= 1:
            for beat in range(4):
                add_wrap(drums, t0 + beat * spb, kick(1.0, 50, 0.22), 0.7)
                duck_events.append(t0 + beat * spb)
            for beat in (1, 3):
                add_wrap(drums, t0 + beat * spb + 0.002, clap(int(rng.integers(1e9)), 0.85), 0.32)
        if section >= 2:
            for beat, m, dur in lead_l[k]:
                add_wrap(lead, t0 + beat * spb, soft_pluck(mtof(m), dur * spb + 0.2, 0.85, bright=0.6), 0.38, pan=0.2)
        if section == 3 and k == 3:
            for e in range(4):                                   # snare-ish fill with claps on the last beat
                add_wrap(drums, t0 + (3 + e * 0.25) * spb, clap(int(rng.integers(1e9)), 0.5 + 0.15 * e), 0.25)
    # side-chain-style ducking of bass + pad from the kicks
    duck = np.ones(N)
    tk = np.arange(int(0.3 * SR)) / SR
    shape = 1 - 0.55 * np.exp(-tk / 0.09) * np.minimum(tk / 0.004 + 0.0, 1)
    for te in duck_events:
        idx = (int(te * SR) + np.arange(len(tk))) % N
        duck[idx] = np.minimum(duck[idx], shape)
    bass_d, pad_d = bass * duck, pad * duck
    dl = circ_delay(lead + arpb * 0.4, 0.375, 0.35, 4, 0.5)
    wet = circ_reverb(arpb + lead + pad, 1.4, 0.25, seed=13, damp=5000)
    _dbg(arp=arpb, lead=lead, pad=pad_d, bass=bass_d, drums=drums, dl=dl, wet=wet)
    mix = arpb * 1.3 + lead * 1.1 + pad_d * 1.2 + bass_d * 0.55 + drums + dl * 1.3 + wet
    mix = fft_highpass(mix, 30, 2)
    mix = np.tanh(mix * 0.9)
    return master("upbeat_pluck", mix)


# ------------------------------------------------------------------ SFX
def sweep_noise(dur, fc_of_t, width_oct=1.1, seed=0, n_fft=2048, hop=512):
    """Band-limited noise whose centre frequency follows fc_of_t(t01): STFT masking + overlap-add."""
    n = int(dur * SR)
    x = _noise(n + n_fft, seed)
    win = np.hanning(n_fft)
    out = np.zeros(n + n_fft)
    norm = np.zeros(n + n_fft)
    f = np.fft.rfftfreq(n_fft, 1 / SR)
    for s in range(0, n, hop):
        fc = fc_of_t(min(s / n, 1.0))
        mask = np.exp(-0.5 * (np.log2(np.maximum(f, 20) / fc) / (width_oct / 2.355)) ** 2)
        seg = np.fft.irfft(np.fft.rfft(x[s:s + n_fft] * win) * mask, n=n_fft)
        out[s:s + n_fft] += seg * win
        norm[s:s + n_fft] += win ** 2
    return (out / np.maximum(norm, 1e-6))[:n]


def _plain_reverb(x, seconds, wet, seed):
    ir = reverb_ir(seed, seconds)
    n = len(x) + len(ir)
    y = np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(ir, n), n)
    y[: len(x)] = x * (1 - wet * 0.0) + y[: len(x)] * wet
    y[len(x):] *= wet
    return y


def sfx_whoosh():
    d = 1.0
    x = sweep_noise(d, lambda u: 350 * (14 ** (np.sin(np.pi * u * 0.5) ** 1.3)) * (1 - 0.35 * u ** 3), 1.3, seed=5)
    t = tt(d)
    env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2.2
    return x * env


def sfx_riser():
    d = 2.6
    x = sweep_noise(d, lambda u: 250 * 40 ** (u ** 1.2), 1.0, seed=6)
    t = tt(d)
    u = t / d
    tone = np.sin(2 * np.pi * np.cumsum(180 * 8 ** (u ** 1.5)) / SR) * 0.35
    env = (u ** 2.0) * np.minimum((d - t) / 0.03, 1)
    return (x * 1.0 + tone) * env


def sfx_hit():
    d = 1.6
    t = tt(d)
    f = 42 + 80 * np.exp(-t * 18)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.2)
    thump = fft_lowpass(_noise(len(t), 8), 1800, 2) * np.exp(-t * 60) * 0.7
    x = body * 1.0 + thump
    x = _plain_reverb(x, 1.2, 0.35, 8)[: len(t)]
    return x * np.minimum(t / 0.001, 1) * np.minimum((d - t) / 0.2, 1)


def sfx_click():
    t = tt(0.09)
    x = np.sin(2 * np.pi * 2600 * t) * np.exp(-t * 260) * 0.7 + np.sin(2 * np.pi * 900 * t) * np.exp(-t * 90) * 0.5
    return x + fft_highpass(_noise(len(t), 9), 3000, 2) * np.exp(-t * 400) * 0.25


def sfx_pop():
    t = tt(0.3)
    f = 180 + 620 * np.exp(-t * 32)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 17)
    return x * np.minimum(t / 0.001, 1)


def sfx_tick():
    t = tt(0.06)
    return (np.sin(2 * np.pi * 1500 * t) * np.exp(-t * 130) + 0.4 * np.sin(2 * np.pi * 3000 * t) * np.exp(-t * 200)) * 0.8


def sfx_shimmer():
    d = 2.6
    t = tt(d)
    x = 0
    for k, r in enumerate((1.0, 2.0, 2.76, 4.07, 5.4)):
        x = x + np.sin(2 * np.pi * 880 * r * t + k) * np.exp(-t * (2.0 + 1.5 * k)) / (1 + k * 0.6)
    x = x * np.minimum(t / 0.004, 1)
    return _plain_reverb(x * 0.6, 1.8, 0.6, 12)[: len(t)] * np.minimum((d - t) / 0.5, 1)


SFX = {"whoosh": sfx_whoosh, "riser": sfx_riser, "hit": sfx_hit, "click": sfx_click, "pop": sfx_pop, "tick": sfx_tick,
       "shimmer": sfx_shimmer}
PEAK_DB = {"whoosh": -8, "riser": -9, "hit": -4, "click": -8, "pop": -8, "tick": -10, "shimmer": -10}


def build_sfx(name):
    ensure(SFX_DIR)
    x = SFX[name]()
    x = x - x.mean()
    x = x / (np.abs(x).max() + 1e-9) * 10 ** (PEAK_DB[name] / 20)
    x[:8] *= np.linspace(0, 1, 8)
    x[-64:] *= np.linspace(1, 0, 64)
    out = os.path.join(SFX_DIR, name + ".wav")
    write_wav(out, x)
    return out


MUSIC = {"lofi_keys": lofi_keys, "cinematic_pad": cinematic_pad, "upbeat_pluck": upbeat_pluck}


def build_music(name):
    out, lufs, tp = MUSIC[name]()
    return f"{name}: {lufs:.1f} LUFS, true peak {tp:.1f} dBFS"
