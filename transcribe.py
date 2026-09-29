#!/usr/bin/env python3
"""Kurgu local speech-to-text with word timestamps. Optional: nothing here is needed to edit or render.

    python3 transcribe.py <file> [--model small] [--language tr] [--start 0] [--duration 12]   # prints JSON

Backends (nothing leaves the machine; models download once from Hugging Face on first use):
  mlx     `mlx-whisper`   Apple Silicon (Metal). Models: mlx-community/whisper-*-mlx repos.
  faster  `faster-whisper` any OS (CPU int8, CUDA if present).
If neither is installed, `availability()` says so with one-line pip hints per OS.

Transcript JSON: {language, duration, model, backend, segments:[{start,end,text,words:[{start,end,word,prob}]}]}.
Times are SOURCE-file seconds (the range start `start` is added back), so a cached transcript is independent of
where a clip sits on the timeline; `to_timeline` maps it onto a layer. Cache: <project>/.kurgu/transcripts/<sha1>.json,
key = path + mtime + size + model + language + range.
"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import uuid
import wave

import media_tools

MODELS = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
DEFAULT_MODEL = "small"
ALIASES = {"large": "large-v3", "turbo": "large-v3-turbo"}
# verified 29 Sep 2026 (HF API 200): these mlx-community repos exist
MLX_REPOS = {"tiny": "mlx-community/whisper-tiny-mlx", "base": "mlx-community/whisper-base-mlx",
             "small": "mlx-community/whisper-small-mlx", "medium": "mlx-community/whisper-medium-mlx",
             "large-v3": "mlx-community/whisper-large-v3-mlx", "large-v3-turbo": "mlx-community/whisper-large-v3-turbo"}
# inside the virtualenv / pipx environment Kurgu runs in (`pipx inject kurgu faster-whisper`, or `uv tool install "kurgu[captions]"`)
HINTS = {"mac": "pip install mlx-whisper   (Apple Silicon)   or   pip install faster-whisper",
         "linux": "pip install faster-whisper",
         "win": "pip install faster-whisper"}
_RUN_LOCK = threading.Lock()


class TranscribeError(Exception):
    pass


class Cancelled(Exception):
    pass


def os_key():
    return {"Darwin": "mac", "Windows": "win"}.get(platform.system(), "linux")


def _has(mod):
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def apple_silicon():
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def detect_backend(prefer=None):
    """'mlx' | 'faster' | None. mlx only on Apple Silicon; `prefer` (or $KURGU_STT_BACKEND) forces one if installed."""
    prefer = prefer or os.environ.get("KURGU_STT_BACKEND")
    ok = {"mlx": apple_silicon() and _has("mlx_whisper"), "faster": _has("faster_whisper")}
    if prefer in ok and ok[prefer]:
        return prefer
    return "mlx" if ok["mlx"] else "faster" if ok["faster"] else None


def availability():
    b = detect_backend()
    d = {"available": b is not None, "backend": b, "models": list(MODELS), "default_model": DEFAULT_MODEL,
         "ffmpeg": media_tools.find("ffmpeg") is not None}
    if b is None:
        d["hint"] = HINTS[os_key()]
        d["hints"] = HINTS
        d["message"] = "Speech-to-text is not installed (optional). Install one backend: " + HINTS[os_key()]
    return d


def canonical_model(model):
    m = (model or DEFAULT_MODEL).strip()
    m = ALIASES.get(m, m)
    if m not in MODELS and "/" not in m:
        raise TranscribeError(f"unknown model {model!r}; use one of {', '.join(MODELS)}")
    return m


# ---------------------------------------------------------------- targets (path or layer)
def resolve_target(project, d, path=None, layer_id=None, allow_outside=True):
    """-> {path (absolute), src_in, duration|None, layer|None}. `project`: raw or normalised project dict."""
    layer = None
    if layer_id:
        layer = next((l for l in project.get("layers", []) if isinstance(l, dict) and l.get("id") == layer_id), None)
        if layer is None:
            raise TranscribeError(f'no layer with id "{layer_id}"')
        if layer.get("type") not in ("video", "audio") or not layer.get("src"):
            raise TranscribeError(f'layer "{layer_id}" is a {layer.get("type")} layer: only video and audio layers have speech')
        path = layer["src"]
    if not path:
        raise TranscribeError("give path or layer_id")
    full = os.path.realpath(os.path.join(d, os.path.expanduser(str(path))))
    if not allow_outside:
        root = os.path.realpath(d)
        if full != root and not full.startswith(root + os.sep):
            raise TranscribeError("path must be inside the project directory")
    if not os.path.isfile(full):
        raise TranscribeError(f"file not found: {path}")
    if layer is None:
        return {"path": full, "src_in": 0.0, "duration": None, "layer": None}
    return {"path": full, "src_in": float(layer.get("src_in") or 0), "layer": layer,
            "duration": max(0.0, float(layer.get("end", 0)) - float(layer.get("start", 0)))}


def to_timeline(tr, start, src_in=0.0, end=None):
    """Map a source-time transcript onto the timeline: t_tl = start + (t - src_in). Words outside [src_in, src_in+(end-start)]
    are dropped, times are clamped to the layer. Returns a new transcript (segments without words are dropped)."""
    limit = None if end is None else src_in + (end - start)
    segs = []
    for s in tr.get("segments", []):
        words = []
        for w in s.get("words", []):
            if w["end"] <= src_in or (limit is not None and w["start"] >= limit):
                continue
            a, b = max(w["start"], src_in), w["end"] if limit is None else min(w["end"], limit)
            words.append({"start": round(start + a - src_in, 3), "end": round(start + b - src_in, 3),
                          "word": w["word"], "prob": w.get("prob")})
        if words:
            segs.append({"start": words[0]["start"], "end": words[-1]["end"],
                         "text": " ".join(w["word"] for w in words), "words": words})
    out = {k: v for k, v in tr.items() if k != "segments"}
    out["segments"] = segs
    out["timeline"] = True
    return out


def all_words(tr):
    return [w for s in tr.get("segments", []) for w in s.get("words", [])]


# ---------------------------------------------------------------- audio + cache
def extract_audio(path, wav, start=0.0, duration=None):
    try:
        cmd = [media_tools.ffmpeg(), "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}"]
    except media_tools.MediaToolError as e:
        raise TranscribeError(str(e))
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += media_tools.input_args(path) + ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]
    try:
        media_tools.probe_json(path)
    except media_tools.UnsafeSourceError as e:
        raise TranscribeError(str(e))
    except Exception:
        pass                                    # unreadable: ffmpeg below reports it
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise TranscribeError(media_tools.missing_message("ffmpeg"))
    if r.returncode != 0 or not os.path.exists(wav):
        raise TranscribeError("could not read audio: " + (r.stderr.strip().splitlines() or ["ffmpeg failed"])[-1])


def read_wav(wav):
    import numpy as np
    with wave.open(wav, "rb") as w:
        n, raw = w.getnframes(), None
        raw = w.readframes(n)
    return np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0


def cache_key(path, model, language, start, duration):
    st = os.stat(path)
    return hashlib.sha1(f"{os.path.realpath(path)}|{st.st_mtime_ns}|{st.st_size}|{model}|{language or 'auto'}|"
                        f"{start:.3f}|{'' if duration is None else f'{duration:.3f}'}".encode()).hexdigest()


def cache_file(project_dir, key):
    return os.path.join(project_dir, ".kurgu", "transcripts", key + ".json")


# ---------------------------------------------------------------- backends
def _noop(stage, frac, message):
    pass


def _download_mlx(repo, progress, cancel):
    """Local path of the model; downloads it (with byte progress) if it is not in the Hugging Face cache yet."""
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(repo, local_files_only=True)
    except Exception:
        pass
    progress("downloading", 0.0, f"Downloading model {repo} (first run only, one-time download)")
    from tqdm.auto import tqdm

    class Bar(tqdm):
        def __init__(self, *a, **k):
            k["disable"] = True                       # keep stderr quiet; we report through `progress`
            super().__init__(*a, **k)
            self._k = 0

        def update(self, n=1):
            self._k += n or 0
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if self.total and self.total > 1e6 and self.unit == "B":       # the real byte bar, not the file counter
                progress("downloading", min(1.0, self._k / self.total), f"Downloading model {repo}")

    try:
        return snapshot_download(repo, tqdm_class=Bar)
    except Cancelled:
        raise
    except Exception as e:
        raise TranscribeError(f"model download failed ({repo}): {e}. Check the internet connection; it is needed once.")


def _run_mlx(audio, model, language, progress, cancel):
    import mlx_whisper
    repo = MLX_REPOS.get(model, model)
    local = _download_mlx(repo, progress, cancel)
    tmod = sys.modules["mlx_whisper.transcribe"]         # `mlx_whisper.transcribe` the attribute is the function
    import tqdm as real
    total_s = len(audio) / 16000.0

    class Bar(real.tqdm):
        def __init__(self, *a, **k):
            k["disable"] = True
            super().__init__(*a, **k)

        def update(self, n=1):
            self._k = getattr(self, "_k", 0) + n
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if self.total:
                progress("transcribing", min(1.0, self._k / self.total), "Transcribing")
    saved = tmod.tqdm
    tmod.tqdm = types.SimpleNamespace(tqdm=Bar)
    try:
        progress("transcribing", 0.0, "Loading model and transcribing")
        kw = {"language": language} if language else {}
        res = mlx_whisper.transcribe(audio, path_or_hf_repo=local, word_timestamps=True, verbose=False,
                                     condition_on_previous_text=False, **kw)
    finally:
        tmod.tqdm = saved
    segs = []
    for s in res.get("segments", []):
        words = [{"start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3), "word": str(w["word"]).strip(),
                  "prob": round(float(w.get("probability", 0)), 3)} for w in s.get("words", []) if str(w["word"]).strip()]
        segs.append({"start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3), "text": str(s["text"]).strip(),
                     "words": words})
    return res.get("language") or language or "", total_s, segs


def _run_faster(audio, model, language, progress, cancel):
    from faster_whisper import WhisperModel
    device, ctype = "cpu", "int8"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            device, ctype = "cuda", "float16"
    except Exception:
        pass
    progress("downloading", 0.0, f"Loading model {model} (downloaded once on first use if missing)")
    try:
        wm = WhisperModel(model, device=device, compute_type=ctype)
    except Exception as e:
        raise TranscribeError(f"could not load model {model}: {e}. The first run needs an internet connection.")
    total_s = len(audio) / 16000.0
    it, info = wm.transcribe(audio, language=language, word_timestamps=True, condition_on_previous_text=False)
    segs = []
    for s in it:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        progress("transcribing", min(1.0, s.end / total_s) if total_s else 0.0, "Transcribing")
        words = [{"start": round(float(w.start), 3), "end": round(float(w.end), 3), "word": w.word.strip(),
                  "prob": round(float(w.probability), 3)} for w in (s.words or []) if w.word.strip()]
        segs.append({"start": round(float(s.start), 3), "end": round(float(s.end), 3), "text": s.text.strip(), "words": words})
    return info.language or language or "", total_s, segs


def transcribe_file(project_dir, path, model=None, language=None, start=0.0, duration=None, progress=None, cancel=None,
                    use_cache=True):
    """Transcribe `path` (video or audio) from `start` for `duration` seconds. Blocks. -> transcript (source time).
    progress(stage, fraction, message); cancel: threading.Event. Raises TranscribeError / Cancelled."""
    progress = progress or _noop
    backend = detect_backend()
    if backend is None:
        raise TranscribeError(availability()["message"])
    model = canonical_model(model)
    language = (language or "").strip().lower() or None
    if language == "auto":
        language = None
    key = cache_key(path, model, language, start, duration)
    cf = cache_file(project_dir, key)
    if use_cache and os.path.exists(cf):
        try:
            with open(cf, encoding="utf-8") as f:
                tr = json.load(f)
            tr["cached"] = True
            progress("done", 1.0, "From cache")
            return tr
        except (OSError, ValueError):
            pass
    with _RUN_LOCK:
        tmp = tempfile.mkdtemp(prefix="kurgu-stt-")
        try:
            progress("extracting", 0.0, "Extracting audio")
            wav = os.path.join(tmp, "a.wav")
            extract_audio(path, wav, start, duration)
            audio = read_wav(wav)
            if len(audio) < 1600:
                raise TranscribeError("no audio in that range")
            t0 = time.time()
            run = _run_mlx if backend == "mlx" else _run_faster
            lang, total_s, segs = run(audio, model, language, progress, cancel)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    for s in segs:
        s["start"] = round(s["start"] + start, 3)
        s["end"] = round(s["end"] + start, 3)
        for w in s["words"]:
            w["start"] = round(w["start"] + start, 3)
            w["end"] = round(w["end"] + start, 3)
    tr = {"language": lang, "duration": round(total_s, 3), "model": model, "backend": backend, "source_start": start,
          "elapsed": round(time.time() - t0, 2), "segments": segs}
    os.makedirs(os.path.dirname(cf), exist_ok=True)
    part = cf + f".{os.getpid()}.part"
    with open(part, "w", encoding="utf-8") as f:
        json.dump(tr, f, ensure_ascii=False)
    os.replace(part, cf)
    progress("done", 1.0, "Done")
    return tr


# ---------------------------------------------------------------- one-at-a-time job runner (used by server.py)
class Busy(Exception):
    pass


class JobRunner:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs, self.order, self.cancels = {}, [], {}

    def start(self, work, info=None):
        """work(progress, cancel) -> result dict. Raises Busy when a job is running."""
        with self.lock:
            if any(j["status"] == "running" for j in self.jobs.values()):
                raise Busy()
            jid = uuid.uuid4().hex[:8]
            job = {"id": jid, "status": "running", "stage": "starting", "progress": 0.0, "message": "Starting",
                   "downloading": False, "result": None, "error": None, "t0": time.time(), **(info or {})}
            self.jobs[jid] = job
            self.order.append(jid)
            for old in self.order[:-10]:
                self.jobs.pop(old, None)
            self.order = self.order[-10:]
            cancel = self.cancels[jid] = threading.Event()

        def progress(stage, frac, message):
            job.update(stage=stage, progress=round(float(frac), 3), message=message, downloading=stage == "downloading")

        def run():
            try:
                job["result"] = work(progress, cancel)
                job.update(status="done", progress=1.0, stage="done", message="Done", downloading=False)
            except Cancelled:
                job.update(status="cancelled", downloading=False, message="Cancelled")
            except TranscribeError as e:
                job.update(status="error", error=str(e), downloading=False)
            except Exception as e:
                job.update(status="error", error=f"{type(e).__name__}: {e}", downloading=False)
            job["elapsed"] = round(time.time() - job["t0"], 1)
        threading.Thread(target=run, daemon=True).start()
        return jid

    def snapshot(self, jid):
        j = self.jobs.get(jid)
        if j is None:
            return None
        d = dict(j)
        d["elapsed"] = round(time.time() - j["t0"], 1) if j["status"] == "running" else j.get("elapsed")
        d.pop("t0", None)
        return d

    def cancel(self, jid):
        j = self.jobs.get(jid)
        if j is None or j["status"] != "running":
            return False
        self.cancels[jid].set()
        return True


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--language")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--project", default=".", help="project dir holding .kurgu/transcripts cache")
    a = ap.parse_args(argv)
    try:
        tr = transcribe_file(os.path.abspath(a.project), os.path.abspath(a.file), a.model, a.language, a.start, a.duration,
                             progress=lambda s, f, m: print(f"{s} {f:.2f} {m}", file=sys.stderr))
    except TranscribeError as e:
        print("ERROR " + str(e), file=sys.stderr)
        return 1
    print(json.dumps(tr, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
