"""Audio facts for a note: what the sound does around the note time, so an agent does not have to derive it with ffprobe filtergraphs.

`audio_block(d, proj, t)` -> one compact entry per sound-carrying layer (audio layers, and video layers that have an audio stream and are not
muted) that is active within +-WINDOW seconds of `t`:
    {id, name, type, window:[t0,t1] (project s), peak_db, rms_db, silences:[[a,b],...] (project s), gain_db, words?:[[a,b,word],...]}
or {id, name, type, error: "..."} when that layer could not be analysed (the send never fails because of audio).
Levels are the source's, plus the layer's `volume_db` and its envelope at `t` (gain_db). Silence = below SILENCE_DB of the source for at least
SILENCE_MIN s. Words come from an already existing transcript in .kurgu/transcripts (whisper is never run here). Results are cached per
file + mtime + media window. Stdlib only (ffmpeg through media_tools)."""
import hashlib
import json
import os
import re
import subprocess

import media_tools

WINDOW = 1.5
SILENCE_DB = -40
SILENCE_MIN = 0.12
TIMEOUT = 20

_cache = {}
_has_audio = {}


def _num(v, dflt=0.0):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else dflt


def _resolve(d, src):
    return os.path.realpath(os.path.join(d, os.path.expanduser(str(src))))


def _file_key(path):
    st = os.stat(path)
    return (path, st.st_mtime_ns, st.st_size)


def has_audio(path):
    k = _file_key(path)
    if k not in _has_audio:
        try:
            streams = media_tools.probe_json(path, "-show_streams").get("streams", [])
            _has_audio[k] = any(s.get("codec_type") == "audio" for s in streams)
        except Exception:
            _has_audio[k] = None            # unreadable: let the analysis report the error
    return _has_audio[k]


def envelope_db(points, t):
    """Linear in dB between [t, dB] points (timeline time), held outside; [] = 0 dB."""
    pts = sorted((_num(p[0]), _num(p[1])) for p in points or [] if isinstance(p, (list, tuple)) and len(p) >= 2)
    if not pts:
        return 0.0
    if t <= pts[0][0]:
        return pts[0][1]
    for (a, va), (b, vb) in zip(pts, pts[1:]):
        if t <= b:
            return va if b == a else va + (vb - va) * (t - a) / (b - a)
    return pts[-1][1]


_FLOAT = r"(-?inf|-?\d+(?:\.\d+)?(?:e[-+]?\d+)?)"


def _f(s):
    try:
        v = float(s)
    except ValueError:
        return None
    return None if v in (float("inf"), float("-inf")) else v


def analyse(path, m0, dur):
    """ffmpeg over media seconds [m0, m0+dur] -> {peak_db, rms_db, silences:[[a,b] relative to m0]}; cached. Raises RuntimeError with a short text."""
    key = (_file_key(path), round(m0, 3), round(dur, 3))
    if key in _cache:
        return _cache[key]
    cmd = [media_tools.ffmpeg(), "-nostdin", "-hide_banner", "-nostats", "-v", "info", "-ss", f"{m0:.3f}", "-t", f"{dur:.3f}"]
    cmd += media_tools.input_args(path) + ["-vn", "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN},astats=metadata=0:reset=0", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("audio analysis timed out")
    except (OSError, media_tools.MediaToolError) as e:
        raise RuntimeError(str(e)[:120])
    err = r.stderr or ""
    if r.returncode != 0:
        raise RuntimeError((err.strip().splitlines() or ["ffmpeg failed"])[-1][:120])
    overall = err.split("Overall", 1)[-1] if "Overall" in err else ""
    pk = re.search(r"Peak level dB:\s*" + _FLOAT, overall)
    rms = re.search(r"RMS level dB:\s*" + _FLOAT, overall)
    if not pk:
        raise RuntimeError("no audio in that range")
    sil, open_at = [], None
    for line in err.splitlines():
        a = re.search(r"silence_start:\s*" + _FLOAT, line)
        b = re.search(r"silence_end:\s*" + _FLOAT, line)
        if a:
            open_at = max(0.0, _f(a.group(1)) or 0.0)
        elif b and open_at is not None:
            sil.append([open_at, min(dur, _f(b.group(1)) or dur)])
            open_at = None
    if open_at is not None:
        sil.append([open_at, dur])
    res = {"peak_db": _f(pk.group(1)), "rms_db": _f(rms.group(1)) if rms else None, "silences": sil}
    _cache[key] = res
    return res


def find_words(d, path, src_in, length):
    """Words [src_a, src_b, word] (SOURCE seconds) of an existing transcript of this media, or []. The cache key of `transcribe` hides the file, so
    the usual calls are tried: this layer's range or the whole file, default model, auto / common languages."""
    import transcribe
    cands = []
    for model in (transcribe.DEFAULT_MODEL,) + tuple(m for m in transcribe.MODELS if m != transcribe.DEFAULT_MODEL):
        for lang in (None, "tr", "en", "de", "es", "fr"):
            for start, dur in ((src_in, length), (0.0, None)):
                cands.append((model, lang, start, dur))
    for model, lang, start, dur in cands:
        try:
            cf = transcribe.cache_file(d, transcribe.cache_key(path, model, lang, start, dur))
        except OSError:
            return []
        if os.path.exists(cf):
            try:
                with open(cf, encoding="utf-8") as f:
                    tr = json.load(f)
            except (OSError, ValueError):
                continue
            return [[w["start"], w["end"], w["word"]] for s in tr.get("segments", []) for w in s.get("words", [])
                    if isinstance(w, dict) and "start" in w and "end" in w]
    return []


def layer_audio(d, l, t):
    """One layer's entry (see module doc), or None when the layer is not audible / not in range."""
    typ = l.get("type")
    if typ not in ("audio", "video") or l.get("muted") or l.get("hidden") or not l.get("src"):
        return None
    start, end = _num(l.get("start")), _num(l.get("end"))
    p0, p1 = max(start, t - WINDOW), min(end, t + WINDOW)
    if p1 <= p0:
        return None
    e = {"id": l.get("id"), "name": l.get("name", ""), "type": typ}
    try:
        path = _resolve(d, l["src"])
        if not os.path.isfile(path):
            e["error"] = "file not found"
            return e
        if typ == "video" and has_audio(path) is False:
            return None
        speed = _num(l.get("speed"), 1.0) or 1.0
        src_in = _num(l.get("src_in"))
        m0, dur = src_in + (p0 - start) * speed, (p1 - p0) * speed
        a = analyse(path, m0, dur)
    except RuntimeError as ex:
        e["error"] = str(ex)
        return e
    except Exception as ex:                      # a failing layer never fails the send
        e["error"] = f"{type(ex).__name__}: {str(ex)[:100]}"
        return e
    gain = _num(l.get("volume_db")) + envelope_db(l.get("envelope"), t)
    r1 = lambda v: None if v is None else round(v, 1)
    e.update(window=[round(p0, 3), round(p1, 3)], peak_db=r1(None if a["peak_db"] is None else a["peak_db"] + gain),
             rms_db=r1(None if a["rms_db"] is None else a["rms_db"] + gain), gain_db=r1(gain),
             silences=[[round(p0 + x / speed, 3), round(p0 + y / speed, 3)] for x, y in a["silences"]])
    try:
        words = find_words(d, path, src_in, max(0.0, end - start) * speed)
    except Exception:
        words = []
    if words:
        w = []
        for a0, b0, txt in words:
            pa, pb = start + (a0 - src_in) / speed, start + (b0 - src_in) / speed
            if pb > p0 and pa < p1:
                w.append([round(pa, 3), round(pb, 3), txt])
        if w:
            e["words"] = w
    return e


def audio_block(d, proj, t):
    """Entries for every sound-carrying layer active within +-WINDOW s of `t` (in the project's layer order)."""
    out = []
    for l in proj.get("layers", []):
        if isinstance(l, dict):
            try:
                e = layer_audio(d, l, float(t))
            except Exception:
                e = None
            if e:
                out.append(e)
    return out


def summary_line(e):
    """One line for NOTLAR.md."""
    nm = e.get("name") or e.get("id")
    if e.get("error"):
        return f"{nm} ({e['type']}): not analysed ({e['error']})"
    s = f"{nm} ({e['type']}) {e['window'][0]:g}-{e['window'][1]:g} s: peak {e['peak_db']} dB, rms {e['rms_db']} dB"
    if e.get("gain_db"):
        s += f" (incl. gain {e['gain_db']:+g} dB)"
    if e.get("silences"):
        s += "; silent " + ", ".join(f"{a:g}-{b:g}" for a, b in e["silences"])
    if e.get("words"):
        s += f"; {len(e['words'])} words: " + " ".join(w[2].strip() for w in e["words"][:12]) + (" ..." if len(e["words"]) > 12 else "")
    return s
