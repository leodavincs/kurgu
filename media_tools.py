"""Find ffmpeg / ffprobe (and the optional hardware H.264/HEVC encoders). Stdlib only.

Lookup order for each tool: env `KURGU_FFMPEG` / `KURGU_FFPROBE` (a file, or a folder that holds the tool),
then PATH, then the usual install folders (Homebrew, MacPorts, snap, winget, Chocolatey, Scoop, ...). A GUI or
service launch often has a thinner PATH than a terminal, hence the fallback list.

    media_tools.ffmpeg()      -> absolute path, or raises MediaToolError with an actionable message
    media_tools.ffprobe()
    media_tools.find("ffmpeg") -> path or None (never raises)
    media_tools.hw_encoders() -> {"h264": "h264_videotoolbox" | None, "h265": ...}  (probe-encoded once, cached)
"""
import glob
import os
import platform
import shutil
import subprocess

ENV = {"ffmpeg": "KURGU_FFMPEG", "ffprobe": "KURGU_FFPROBE"}
INSTALL = {"Darwin": "brew install ffmpeg", "Windows": "winget install Gyan.FFmpeg"}
_cache = {}


class MediaToolError(RuntimeError):
    pass


def _exe(name):
    return name + ".exe" if platform.system() == "Windows" else name


def _extra_dirs():
    s = platform.system()
    if s == "Darwin":
        return ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", "/usr/bin"]
    if s == "Windows":
        env = os.environ
        la, pf, pd = env.get("LOCALAPPDATA", ""), env.get("ProgramFiles", r"C:\Program Files"), env.get("ProgramData", r"C:\ProgramData")
        home = os.path.expanduser("~")
        d = [os.path.join(la, "Microsoft", "WinGet", "Links"), os.path.join(pd, "chocolatey", "bin"),
             os.path.join(env.get("SCOOP", os.path.join(home, "scoop")), "shims"),
             os.path.join(pf, "ffmpeg", "bin"), r"C:\ffmpeg\bin"]
        d += sorted(glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "*ffmpeg*", "*", "bin")), reverse=True)
        d += sorted(glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "*ffmpeg*", "bin")), reverse=True)
        return [x for x in d if x]
    return ["/usr/bin", "/usr/local/bin", "/snap/bin", "/opt/homebrew/bin", os.path.expanduser("~/.local/bin")]


def _is_exe(p):
    return os.path.isfile(p) and os.access(p, os.X_OK)


def find(tool):
    """Path of `tool` ("ffmpeg" | "ffprobe") or None."""
    if tool in _cache:
        return _cache[tool]
    found = None
    env = os.environ.get(ENV.get(tool, ""), "").strip()
    if env:
        env = os.path.expanduser(env)
        cand = os.path.join(env, _exe(tool)) if os.path.isdir(env) else env
        if _is_exe(cand):
            found = cand
    if not found:
        found = shutil.which(tool)
    if not found:
        for d in _extra_dirs():
            cand = os.path.join(d, _exe(tool))
            if _is_exe(cand):
                found = cand
                break
    _cache[tool] = found
    return found


def reset():
    """Forget cached lookups (tests, or after installing ffmpeg while the server runs)."""
    _cache.clear()
    _hw.clear()


def missing_message(tool):
    hint = INSTALL.get(platform.system(), "sudo apt install ffmpeg")
    env = os.environ.get(ENV.get(tool, ""), "").strip()
    bad = f" (KURGU_{tool.upper()}={env} is not an executable file)" if env else ""
    return (f"{tool} was not found{bad}. Install it ({hint}), then restart Kurgu, "
            f"or point Kurgu at it with the environment variable {ENV.get(tool, 'KURGU_' + tool.upper())}=/full/path/to/{tool}.")


def require(tool):
    p = find(tool)
    if not p:
        raise MediaToolError(missing_message(tool))
    return p


def ffmpeg():
    return require("ffmpeg")


def ffprobe():
    return require("ffprobe")


# ---------------------------------------------------------------- untrusted inputs
# A project (possibly downloaded) names its media by path. ffmpeg would happily open a playlist / concat script and then
# read other local files or fetch URLs from it, so every input that comes from a project goes through `input_args`
# (only the file and pipe protocols) and through `check_format` (playlist-like containers are refused outright).
PROTOCOLS = "file,pipe"
BAD_FORMATS = frozenset({"hls", "applehttp", "concat", "ffconcat", "dash", "mpegdash", "rtsp", "rtp", "sdp", "tee", "lavfi",
                         "image2pipe_playlist", "libmodplug"})


class UnsafeSourceError(MediaToolError):
    pass


def input_args(path):
    """`-i <path>` restricted to local files / pipes."""
    return ["-protocol_whitelist", PROTOCOLS, "-i", path]


def check_format(format_name, path=""):
    """Raise UnsafeSourceError for playlist / script containers (format_name as reported by ffprobe)."""
    bad = sorted(BAD_FORMATS.intersection(str(format_name or "").lower().split(",")))
    if bad:
        raise UnsafeSourceError(f"{os.path.basename(path) or 'this file'} is a {bad[0]} playlist/script, not a media file; "
                                "Kurgu refuses playlist sources (they can point at other files or URLs)")


def probe_json(path, *extra, timeout=60):
    """ffprobe -> parsed JSON (raises MediaToolError on a hard failure; UnsafeSourceError for playlist containers)."""
    import json
    r = subprocess.run([ffprobe(), "-protocol_whitelist", PROTOCOLS, "-v", "error", "-print_format", "json", "-show_format",
                        *extra, path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise MediaToolError((r.stderr.strip().splitlines() or ["ffprobe failed"])[-1])
    j = json.loads(r.stdout or "{}")
    check_format((j.get("format") or {}).get("format_name"), path)
    return j


# ---------------------------------------------------------------- hardware encoders
HW_CANDIDATES = {
    "h264": ("h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"),
    "h265": ("hevc_videotoolbox", "hevc_nvenc", "hevc_qsv", "hevc_amf"),
}
_hw = {}


def _listed_encoders():
    try:
        r = subprocess.run([ffmpeg(), "-hide_banner", "-v", "error", "-encoders"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except Exception:
        return ""
    return r.stdout


def _probe_encode(name):
    """1-frame test encode: the encoder can be compiled in and still fail (no GPU, no driver)."""
    try:
        r = subprocess.run([ffmpeg(), "-hide_banner", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                            "color=c=black:s=256x256:r=30:d=0.2", "-frames:v", "1", "-pix_fmt", "yuv420p",
                            *hw_video_args(name, crf=20, gop=15), "-f", "null", "-"], capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def hw_encoders():
    """{"h264": name|None, "h265": name|None}: first candidate per codec that is listed AND encodes a test frame."""
    if _hw:
        return dict(_hw)
    res = {"h264": None, "h265": None}
    if find("ffmpeg"):
        listed = _listed_encoders()
        for codec, names in HW_CANDIDATES.items():
            for n in names:
                if f" {n} " in listed and _probe_encode(n):
                    res[codec] = n
                    break
    _hw.update(res)
    return dict(res)


def hw_video_args(encoder, crf=None, bitrate=None, gop=None, preset=None):
    """Codec args for a hardware encoder. `crf` (x264-like 0-51 quality knob) is mapped to the encoder's own scale;
    `bitrate` ("8000k") wins when given. Output is 8-bit 4:2:0."""
    v = ["-c:v", encoder]
    if encoder.endswith("_videotoolbox"):
        if bitrate:
            v += ["-b:v", bitrate]
        else:
            v += ["-q:v", str(max(1, min(100, round(100 - (crf if crf is not None else 20) * 1.9))))]
        if encoder.startswith("hevc"):
            v += ["-tag:v", "hvc1"]
        v += ["-allow_sw", "1"]
    elif encoder.endswith("_nvenc"):
        v += ["-preset", "p4", "-rc", "vbr"]
        v += ["-b:v", bitrate] if bitrate else ["-cq", str(int(crf if crf is not None else 20)), "-b:v", "0"]
        if encoder.startswith("hevc"):
            v += ["-tag:v", "hvc1"]
    elif encoder.endswith("_qsv"):
        v += ["-preset", "veryfast"]
        v += ["-b:v", bitrate] if bitrate else ["-global_quality", str(int(crf if crf is not None else 20))]
        if encoder.startswith("hevc"):
            v += ["-tag:v", "hvc1"]
    elif encoder.endswith("_amf"):
        v += ["-quality", "speed"]
        v += ["-b:v", bitrate] if bitrate else ["-rc", "cqp", "-qp_i", str(int(crf if crf is not None else 20)),
                                                "-qp_p", str(int(crf if crf is not None else 20))]
        if encoder.startswith("hevc"):
            v += ["-tag:v", "hvc1"]
    if gop:
        v += ["-g", str(int(gop))]
    return v


def hw_decode_args():
    """Input-side args for hardware decoding, or [] (hwaccel picks a working device, falls back to software on failure)."""
    if platform.system() == "Darwin":
        return ["-hwaccel", "videotoolbox"]
    return []
