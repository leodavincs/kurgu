"""Export presets and option handling shared by server.py (stdlib only).

An *options* dict (from the export dialog, `POST /api/render {"options": {...}}` or a preset) becomes
`render.py` command-line flags plus an output path inside the project folder:

    codec      h264 | h265 | prores | vp9 | gif | wav | mp3
    format     container / extension (mp4 mov mkv webm gif wav mp3); default from the codec
    crf | bitrate   quality: constant quality, or target video bitrate (kbps number or "8M" / "8000k")
    speed      x264/x265 preset (veryfast ... slow)
    encoder    software (default: libx264/libx265, reproducible) | auto (hardware H.264/HEVC when a working encoder exists:
               VideoToolbox, NVENC, QSV, AMF) | hardware (like auto, but warns when none is available); h264/h265 only.
               crf maps to the encoder's own quality scale, bitrate is passed as is.
    scale | fit_long | width   resolution: fraction of the sequence / long side in px / width in px (aspect kept)
    fps        override (null = sequence fps; gif defaults to min(sequence, 15))
    range      null | {"from": s, "to": s}
    audio      bool; audio_bitrate kbps or "192k"
    name       file name without extension (default "<project>_<preset>"); folder inside the project (default "exports")
    exists     "rename" (default: name (2).ext) | "overwrite"
    open_when_done  bool (the server opens the file with the OS default app)
"""
import os
import re

CODEC_EXT = {"h264": ("mp4", "mov", "mkv"), "h265": ("mp4", "mov", "mkv"), "prores": ("mov",), "vp9": ("webm", "mkv"),
             "gif": ("gif",), "wav": ("wav",), "mp3": ("mp3",)}
AUDIO_ONLY = ("wav", "mp3")
ENCODERS = ("software", "auto", "hardware")
SPEEDS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower")

PRESETS = {
    "youtube": {"codec": "h264", "crf": 18, "fit_long": 1920, "audio": True, "audio_bitrate": 192},
    "master4k": {"codec": "h264", "crf": 14, "fit_long": 3840, "audio": True, "audio_bitrate": 320},
    "social": {"codec": "h264", "bitrate": 8000, "scale": 1, "audio": True, "audio_bitrate": 192},
    "fast": {"codec": "h264", "crf": 20, "fit_long": 1920, "encoder": "auto", "audio": True, "audio_bitrate": 192},
    "draft": {"codec": "h264", "crf": 26, "speed": "veryfast", "scale": 0.5, "audio": True, "audio_bitrate": 128},
    "prores": {"codec": "prores", "format": "mov", "scale": 1, "audio": True},
    "web": {"codec": "vp9", "crf": 32, "scale": 1, "audio": True, "audio_bitrate": 128},
    "gif": {"codec": "gif", "fit_long": 640, "fps": 15, "audio": False},
    "audio": {"codec": "wav", "audio": True},
}


def presets_payload():
    """GET /api/export-presets: the preset table; hardware-encoder presets say whether one is available here."""
    import media_tools
    hw = media_tools.hw_encoders() if media_tools.find("ffmpeg") else {}
    out = {}
    for k, v in PRESETS.items():
        v = dict(v)
        if v.get("encoder") in ("auto", "hardware"):
            v["hw_encoder"] = hw.get(v["codec"])
        out[k] = v
    return out


class OptionError(ValueError):
    pass


def _num(v, name, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise OptionError(f"{name} must be a number")
    if x != x or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise OptionError(f"{name} is out of range")
    return x


def _kbps(v, name):
    """8000 | "8000k" | "8M" -> "8000k" """
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{int(_num(v, name, 8, 2000000))}k"
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmM]?)\s*", str(v))
    if not m:
        raise OptionError(f"{name} must look like 8000k or 8M")
    k = float(m.group(1)) * (1000 if m.group(2).lower() == "m" else 1)
    return f"{int(_num(k, name, 8, 2000000))}k"


def _even(v):
    return max(2, int(round(v / 2)) * 2)


def safe_stem(name):
    stem = re.sub(r"[^\w .()+-]", "_", str(name or ""), flags=re.UNICODE).strip(" .")
    return stem[:120]


def resolve_options(body, project):
    """Merge preset + options -> a fully validated options dict (raises OptionError)."""
    body = body or {}
    opts = {}
    pid = body.get("preset")
    if pid is not None and pid not in PRESETS:
        raise OptionError(f"unknown preset: {pid}")
    if pid:
        opts.update(PRESETS[pid])
    o = body.get("options")
    if isinstance(o, dict):
        if o.get("preset") in PRESETS and not pid:
            pid = o["preset"]
            opts.update(PRESETS[pid])
        opts.update({k: v for k, v in o.items() if k != "preset"})
        # a resolution key in `options` replaces the preset's own resolution
        if any(k in o for k in ("scale", "fit_long", "width")):
            for k in ("scale", "fit_long", "width"):
                if k not in o:
                    opts.pop(k, None)
        if "crf" in o and "bitrate" not in o:
            opts.pop("bitrate", None)
        if "bitrate" in o and "crf" not in o:
            opts.pop("crf", None)
    opts["preset"] = pid or "custom"
    codec = opts.get("codec", "h264")
    if codec not in CODEC_EXT:
        raise OptionError(f"unknown codec: {codec}")
    opts["codec"] = codec
    fmt = str(opts.get("format") or CODEC_EXT[codec][0]).lstrip(".").lower()
    if fmt not in CODEC_EXT[codec]:
        raise OptionError(f"codec {codec} cannot be written to .{fmt} (use {', '.join('.' + e for e in CODEC_EXT[codec])})")
    opts["format"] = fmt
    if opts.get("crf") is not None:
        opts["crf"] = int(_num(opts["crf"], "crf", 0, 63))
    if opts.get("bitrate"):
        opts["bitrate"] = _kbps(opts["bitrate"], "bitrate")
    if opts.get("speed") and opts["speed"] not in SPEEDS:
        raise OptionError("unknown speed preset")
    opts["encoder"] = opts.get("encoder") or "software"
    if opts["encoder"] not in ENCODERS:
        raise OptionError("encoder must be software, auto or hardware")
    if codec not in ("h264", "h265"):
        opts["encoder"] = "software"
    for k in ("scale", "fit_long", "width"):
        if opts.get(k) in (None, "", 0):
            opts.pop(k, None)
    if "scale" in opts:
        opts["scale"] = _num(opts["scale"], "scale", 0.05, 4)
    for k in ("fit_long", "width"):
        if k in opts:
            opts[k] = int(_num(opts[k], k, 16, 16384))
    if opts.get("fps"):
        opts["fps"] = _num(opts["fps"], "fps", 1, 240)
    else:
        opts["fps"] = None
    rg = opts.get("range")
    if rg:
        a = _num(rg.get("from") or 0, "range.from", 0)
        b = rg.get("to")
        b = _num(b, "range.to", 0) if b not in (None, "") else None
        if b is not None and b <= a:
            raise OptionError("range end must be after its start")
        opts["range"] = {"from": a, "to": b} if (a > 0 or b is not None) else None
    else:
        opts["range"] = None
    opts["audio"] = True if codec in AUDIO_ONLY else (False if codec == "gif" else bool(opts.get("audio", True)))
    if opts.get("audio_bitrate"):
        opts["audio_bitrate"] = _kbps(opts["audio_bitrate"], "audio_bitrate")
    if opts.get("exists") not in ("rename", "overwrite"):
        opts["exists"] = "rename"
    opts["open_when_done"] = bool(opts.get("open_when_done"))
    return opts


def output_size(opts, project):
    """(w, h) for video codecs (aspect of the sequence kept), None for audio-only or the sequence size."""
    if opts["codec"] in AUDIO_ONLY:
        return None
    w0, h0 = int(project["width"]), int(project["height"])
    if "width" in opts:
        k = opts["width"] / w0
    elif "fit_long" in opts:
        k = opts["fit_long"] / max(w0, h0)
    elif "scale" in opts:
        k = opts["scale"]
    else:
        return None
    return _even(w0 * k), _even(h0 * k)


def plan_output(opts, project, pdir):
    """-> (absolute output path, exists_before_rename). Raises OptionError when the folder leaves the project."""
    folder = str(opts.get("folder") or "exports").replace("\\", "/").strip("/")
    root = os.path.realpath(pdir)
    target = os.path.realpath(os.path.join(root, folder))
    if target != root and not target.startswith(root + os.sep):
        raise OptionError("the export folder must be inside the project folder")
    stem = safe_stem(opts.get("name") or "")
    if not stem:
        stem = safe_stem(project.get("name") or os.path.basename(root) or "export") or "export"
        stem += "_" + opts["preset"]
    for e in ("." + x for x in CODEC_EXT[opts["codec"]]):
        if stem.lower().endswith(e):
            stem = stem[:-len(e)]
    path = os.path.join(target, f"{stem}.{opts['format']}")
    existed = os.path.exists(path)
    if existed and opts["exists"] == "rename":
        i = 2
        while os.path.exists(os.path.join(target, f"{stem} ({i}).{opts['format']}")):
            i += 1
        path = os.path.join(target, f"{stem} ({i}).{opts['format']}")
    return path, existed


def cli_args(opts, project, output):
    """render.py argv (after the project dir)."""
    a = ["--output", output, "--codec", opts["codec"]]
    if opts.get("crf") is not None and not opts.get("bitrate"):
        a += ["--crf", str(opts["crf"])]
    if opts.get("bitrate"):
        a += ["--bitrate", opts["bitrate"]]
    if opts.get("speed"):
        a += ["--preset", opts["speed"]]
    if opts.get("encoder") and opts["encoder"] != "software":
        a += ["--encoder", opts["encoder"]]
    sz = output_size(opts, project)
    if sz:
        a += ["--size", f"{sz[0]}x{sz[1]}"]
    if opts.get("fps"):
        a += ["--fps", f"{opts['fps']:g}"]
    rg = opts.get("range")
    if rg:
        if rg["from"] > 0:
            a += ["--from", f"{rg['from']:.4f}"]
        if rg["to"] is not None:
            a += ["--to", f"{rg['to']:.4f}"]
    if not opts["audio"] and opts["codec"] not in AUDIO_ONLY:
        a += ["--no-audio"]
    if opts.get("audio_bitrate"):
        a += ["--audio-bitrate", opts["audio_bitrate"]]
    return a
