"""`kurgu doctor [project_dir] [--json] [--fix]`: is this machine ready to run Kurgu? Exit 0 = yes, 1 = something missing."""
import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

import media_tools

MIN_FREE_GB = 5.0
HW_NAMES = {"videotoolbox": "VideoToolbox (macOS)", "nvenc": "NVENC (NVIDIA)", "qsv": "QSV (Intel)", "amf": "AMF (AMD)",
            "vaapi": "VAAPI (Linux)"}


def _py_managed():
    """True when this interpreter is one uv downloaded (lives in uv's python folder)."""
    p = (sys.executable or "").replace("\\", "/").lower()
    return "/uv/python/" in p or "/uv/tools/" in p


def _version_line(path):
    try:
        r = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=30)
        return r.stdout.splitlines()[0].strip() if r.returncode == 0 and r.stdout else None
    except (OSError, subprocess.SubprocessError):
        return None


def _source(path):
    """Where a found ffmpeg comes from: env | kurgu (downloaded copy) | path | other."""
    if not path:
        return None
    import ffmpeg_fetch
    norm = os.path.normcase(os.path.realpath(path))
    for var in media_tools.ENV.values():
        e = os.environ.get(var, "").strip()
        if e and norm.startswith(os.path.normcase(os.path.realpath(os.path.expanduser(e)))):
            return "env"
    if norm.startswith(os.path.normcase(os.path.realpath(ffmpeg_fetch.managed_dir()))):
        return "kurgu"
    which = shutil.which(os.path.basename(path))
    if which and os.path.normcase(os.path.realpath(which)) == norm:
        return "path"
    return "other"


def _encoders(ffmpeg):
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    names = set(re.findall(r"^\s*V\S*\s+(\S+)", r.stdout, re.M))
    return sorted(n for n in names if any(k in n for k in HW_NAMES) and n.split("_")[0] in ("h264", "hevc", "av1"))


def _project_checks(folder):
    out = {"path": os.path.abspath(folder), "exists": os.path.isdir(folder)}
    if not out["exists"]:
        return out
    u = shutil.disk_usage(folder)
    out["free_gb"] = round(u.free / 1e9, 1)
    out["path_length"] = len(out["path"])
    try:   # can we write and read back a file whose name has spaces / Turkish letters / emoji?
        with tempfile.TemporaryDirectory(dir=folder, prefix="kurgu doctor İıŞğ ") as d:
            p = os.path.join(d, "dene 🎬 çalışma.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("ok")
            out["writable_unicode"] = open(p, encoding="utf-8").read() == "ok"
    except OSError as e:
        out["writable_unicode"] = False
        out["error"] = str(e)
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
                out["long_paths_enabled"] = bool(winreg.QueryValueEx(k, "LongPathsEnabled")[0])
        except Exception:
            out["long_paths_enabled"] = None
    return out


def collect(project_dir=".", fix=False, log=print):
    import ffmpeg_fetch
    rep = {"ok": True, "problems": [], "warnings": [], "os": f"{platform.system()} {platform.release()} {platform.machine()}"}
    v = sys.version_info
    rep["python"] = {"version": f"{v.major}.{v.minor}.{v.micro}", "executable": sys.executable, "managed_by_uv": _py_managed(),
                     "ok": v >= (3, 10)}
    if not rep["python"]["ok"]:
        rep["problems"].append("Python is older than 3.10. Reinstall Kurgu with the install script (it brings its own Python).")
    pk = {}
    for label, mod in (("numpy", "numpy"), ("Pillow", "PIL"), ("fontTools", "fontTools")):
        try:
            pk[label] = getattr(importlib.import_module(mod), "__version__", "ok")
        except Exception:
            pk[label] = None
            rep["problems"].append(f"Python package {label} is missing. Reinstall: uv tool install --force git+https://github.com/leodavincs/kurgu")
    rep["packages"] = pk

    media_tools.reset()
    if fix and not all(media_tools.find(t) for t in ("ffmpeg", "ffprobe")):
        try:
            ffmpeg_fetch.install(log=log)
        except ffmpeg_fetch.FetchError as e:
            rep["problems"].append(f"setup-ffmpeg failed: {e}")
        media_tools.reset()
    tools = {}
    for t in ("ffmpeg", "ffprobe"):
        p = media_tools.find(t)
        tools[t] = {"path": p, "source": _source(p), "version": _version_line(p) if p else None}
        if not p:
            rep["problems"].append(f"{t} not found. Run: kurgu setup-ffmpeg   (or: kurgu doctor --fix)")
        elif not tools[t]["version"]:
            rep["problems"].append(f"{t} at {p} does not run. Run: kurgu setup-ffmpeg --force")
    rep["tools"] = tools
    if tools["ffmpeg"]["path"] and tools["ffmpeg"]["version"]:
        listed = _encoders(tools["ffmpeg"]["path"])
        working = {k: v for k, v in media_tools.hw_encoders().items() if v}
        rep["hardware_encoders"] = {"listed": listed, "working": sorted(working.values())}
    try:
        import transcribe
        av = transcribe.availability()
        rep["speech_to_text"] = {"available": bool(av["available"]), "backend": av.get("backend"), "hint": av.get("hint")}
    except Exception:
        rep["speech_to_text"] = {"available": False, "hint": "optional: uv tool install --with faster-whisper ..."}
    rep["project"] = _project_checks(project_dir)
    pr = rep["project"]
    if not pr["exists"]:
        rep["problems"].append(f"Project folder does not exist: {pr['path']}")
    else:
        if pr.get("free_gb", 99) < MIN_FREE_GB:
            rep["warnings"].append(f"Only {pr['free_gb']} GB free in the project folder; renders need several GB.")
        if pr.get("writable_unicode") is False:
            rep["problems"].append("Cannot write files in the project folder (permissions, or a name it does not accept): " + pr.get("error", ""))
        if pr.get("path_length", 0) > 200:
            rep["warnings"].append("The project path is very long; Windows may fail above 260 characters. Move the project closer to the drive root.")
        if pr.get("long_paths_enabled") is False and pr.get("path_length", 0) > 150:
            rep["warnings"].append("Windows long paths are off (LongPathsEnabled=0); keep the project path short.")
    rep["ok"] = not rep["problems"]
    return rep


def render(rep):
    L = []
    def line(ok, text):
        L.append(f"[{'ok' if ok else 'MISSING'}] {text}")
    p = rep["python"]
    line(p["ok"], f"python {p['version']} ({'managed by uv' if p['managed_by_uv'] else 'not uv-managed'}; {p['executable']})")
    for k, v in rep["packages"].items():
        line(bool(v), f"{k} {v or ''}".rstrip())
    for t, d in rep["tools"].items():
        line(bool(d["path"]), f"{t}: {d['path'] or 'not found'}" + (f" [{d['source']}] {d['version']}" if d["path"] else ""))
    if "hardware_encoders" in rep:
        h = rep["hardware_encoders"]
        L.append("[ok] hardware encoders working: " + (", ".join(h["working"]) or "none (software libx264 is used)")
                 + ("; listed: " + ", ".join(h["listed"]) if h["listed"] else "") + " (optional)")
    s = rep["speech_to_text"]
    L.append("[ok] speech-to-text: " + s["backend"] if s["available"] else "[optional] speech-to-text not installed (only for automatic captions)")
    pr = rep["project"]
    if pr["exists"]:
        L.append(f"[ok] project folder {pr['path']}: {pr.get('free_gb')} GB free, unicode names {'ok' if pr.get('writable_unicode') else 'FAILED'}")
    for w in rep["warnings"]:
        L.append("[warn] " + w)
    if rep["ok"]:
        L.append("\nAll good. Start the editor with: kurgu <your project folder>")
    else:
        L.append("\nProblems:")
        L += ["  - " + x for x in rep["problems"]]
    return "\n".join(L)


def main(argv=None):
    import argparse
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="kurgu doctor", description="Check that Kurgu can run on this machine.")
    ap.add_argument("project_dir", nargs="?", default=".")
    ap.add_argument("--json", action="store_true", help="one JSON object on stdout (for agents)")
    ap.add_argument("--fix", action="store_true", help="download ffmpeg if it is missing")
    a = ap.parse_args(argv)
    rep = collect(a.project_dir, fix=a.fix, log=(lambda *_: None) if a.json else print)
    print(json.dumps(rep, ensure_ascii=False, indent=2) if a.json else render(rep))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
