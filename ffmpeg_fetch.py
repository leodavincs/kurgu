"""Download a static ffmpeg + ffprobe into Kurgu's per-user data folder. Stdlib only, no admin rights.

    kurgu setup-ffmpeg            # download if ffmpeg/ffprobe are not found, verify, done
    kurgu setup-ffmpeg --force    # download again (upgrade) even when a system ffmpeg exists
    kurgu setup-ffmpeg --json     # machine-readable result for agents

Folder (media_tools looks here after PATH, before the usual install folders; `KURGU_DATA` overrides the base):
    Windows  %LOCALAPPDATA%\\Kurgu\\ffmpeg          macOS  ~/Library/Application Support/Kurgu/ffmpeg
    Linux    $XDG_DATA_HOME or ~/.local/share  /kurgu/ffmpeg

Sources (static builds, GPL; see LICENSES below). Downloads only ever happen when this command runs (install script,
`kurgu setup-ffmpeg`, `kurgu doctor --fix`); the editor server never downloads anything.
"""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
RIEDL = "https://ffmpeg.martin-riedl.de/redirect/latest/"
TOOLS = ("ffmpeg", "ffprobe")
LICENSE_NOTE = ("The downloaded ffmpeg is a static GPL build ({source}). ffmpeg is free software under the GPL "
                "(https://ffmpeg.org/legal.html); Kurgu only calls it as a separate program and does not bundle it.")


class FetchError(RuntimeError):
    pass


def data_dir():
    """Base per-user data folder of Kurgu."""
    env = os.environ.get("KURGU_DATA", "").strip()
    if env:
        return os.path.expanduser(env)
    home = os.path.expanduser("~")
    s = platform.system()
    if s == "Windows":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local"), "Kurgu")
    if s == "Darwin":
        return os.path.join(home, "Library", "Application Support", "Kurgu")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"), "kurgu")


def managed_dir():
    return os.path.join(data_dir(), "ffmpeg")


def _exe(name, system):
    return name + ".exe" if system == "Windows" else name


def plan(system=None, machine=None):
    """What to download for this OS/CPU: {"source", "archives": [{"url", "kind", "sha_list"|None}]}.

    `kind` is "zip" or "tar.xz". `sha_list` is a checksum-list URL (BtbN: one file for every build); builds without a
    list publish `<final url>.sha256` next to the archive (martin-riedl.de), which `install` resolves after redirects."""
    system = system or platform.system()
    m = (machine or platform.machine()).lower()
    arm = m in ("arm64", "aarch64", "armv8", "armv8l")
    x64 = m in ("x86_64", "amd64", "x64")
    if system == "Windows":
        if not (x64 or arm):
            raise FetchError(f"Unsupported Windows CPU '{m}'. Install ffmpeg yourself (winget install Gyan.FFmpeg) "
                             "and set KURGU_FFMPEG / KURGU_FFPROBE.")
        # Windows on ARM runs the x64 build through emulation.
        return {"source": "BtbN/FFmpeg-Builds (github.com/BtbN/FFmpeg-Builds)",
                "archives": [{"url": BTBN + "ffmpeg-master-latest-win64-gpl.zip", "kind": "zip", "sha_list": BTBN + "checksums.sha256"}]}
    if system == "Darwin":
        if not (x64 or arm):
            raise FetchError(f"Unsupported Mac CPU '{m}'. Install ffmpeg with Homebrew (brew install ffmpeg).")
        a, chan = ("arm64", "release") if arm else ("amd64", "snapshot")   # the amd64 'release' channel does not exist
        return {"source": "ffmpeg.martin-riedl.de (static macOS builds)",
                "archives": [{"url": f"{RIEDL}macos/{a}/{chan}/{t}.zip", "kind": "zip", "sha_list": None} for t in TOOLS]}
    if system == "Linux":
        if arm:
            name = "ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
        elif x64:
            name = "ffmpeg-master-latest-linux64-gpl.tar.xz"
        else:
            raise FetchError(f"Unsupported Linux CPU '{m}'. Install ffmpeg with your package manager (sudo apt install ffmpeg).")
        return {"source": "BtbN/FFmpeg-Builds (github.com/BtbN/FFmpeg-Builds)",
                "archives": [{"url": BTBN + name, "kind": "tar.xz", "sha_list": BTBN + "checksums.sha256"}]}
    raise FetchError(f"Unsupported system '{system}'. Install ffmpeg yourself and set KURGU_FFMPEG / KURGU_FFPROBE.")


# ---------------------------------------------------------------- network (injectable for tests)
def _http_download(url, dest, progress=None):
    """Stream `url` to `dest`; returns the final URL after redirects."""
    req = urllib.request.Request(url, headers={"User-Agent": "kurgu-setup-ffmpeg"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
        return r.geturl()


def _http_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "kurgu-setup-ffmpeg"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def _network_hint(err):
    return (f"Could not download ffmpeg ({err}). Check your internet connection. Behind a company proxy set HTTPS_PROXY "
            "(e.g. https://user:pass@proxy:8080) and run it again. Offline fallback: download any ffmpeg build "
            "yourself (https://ffmpeg.org/download.html), then set KURGU_FFMPEG and KURGU_FFPROBE to the full paths of "
            "ffmpeg and ffprobe, or put both files into " + managed_dir())


# ---------------------------------------------------------------- verification + extraction
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def expected_sha(text, filename=None):
    """Hash for `filename` from a 'hash  name' list, or the lone hash of a one-file .sha256."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if filename:
        for l in lines:
            parts = l.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == filename and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                return parts[0].lower()
        raise FetchError(f"No checksum for {filename} in the published list.")
    m = re.search(r"\b[0-9a-fA-F]{64}\b", text)
    if not m:
        raise FetchError("The published checksum file has no SHA-256 value.")
    return m.group(0).lower()


def _extract(archive, kind, out_dir, system):
    """Copy only the ffmpeg / ffprobe executables (by file name, never by archive path) into out_dir."""
    want = {_exe(t, system): t for t in TOOLS}
    found = []
    if kind == "zip":
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
                if base in want and not info.is_dir():
                    with z.open(info) as src, open(os.path.join(out_dir, base), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.append(base)
    else:
        with tarfile.open(archive, "r:*") as t:
            for m in t:
                base = m.name.replace("\\", "/").rsplit("/", 1)[-1]
                if m.isfile() and base in want:
                    with t.extractfile(m) as src, open(os.path.join(out_dir, base), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.append(base)
    for base in found:
        os.chmod(os.path.join(out_dir, base), 0o755)
    return found


def verify(path):
    """Run `<tool> -version`; returns the first line, raises FetchError when it does not run."""
    try:
        r = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise FetchError(f"{os.path.basename(path)} does not run on this machine: {e}")
    if r.returncode != 0 or not r.stdout.strip():
        raise FetchError(f"{os.path.basename(path)} -version failed (exit {r.returncode}): {r.stderr.strip()[:200]}")
    return r.stdout.splitlines()[0].strip()


def installed():
    """{"ffmpeg": path|None, "ffprobe": path|None} inside the managed folder."""
    s = platform.system()
    out = {}
    for t in TOOLS:
        p = os.path.join(managed_dir(), _exe(t, s))
        out[t] = p if os.path.isfile(p) else None
    return out


def install(progress=None, download=_http_download, fetch_text=_http_text, system=None, machine=None, log=print):
    """Download, checksum-verify, extract and run-test ffmpeg + ffprobe. Returns {"dir", "versions", "source"}."""
    system = system or platform.system()
    p = plan(system, machine)
    target = managed_dir()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    work = tempfile.mkdtemp(prefix="dl-", dir=os.path.dirname(target))     # same drive as the target -> atomic rename
    stage = os.path.join(work, "stage")
    os.makedirs(stage)
    try:
        for a in p["archives"]:
            fname = a["url"].rsplit("/", 1)[-1]
            log(f"Downloading {a['url']}")
            arc = os.path.join(work, fname)
            try:
                final = download(a["url"], arc, progress)
            except OSError as e:           # URLError / HTTPError / timeouts are OSError subclasses
                raise FetchError(_network_hint(e))
            try:
                if a["sha_list"]:
                    want = expected_sha(fetch_text(a["sha_list"]), fname)
                else:
                    want = expected_sha(fetch_text((final or a["url"]) + ".sha256"))
            except OSError as e:
                raise FetchError(_network_hint(e))
            got = sha256_file(arc)
            if got != want:
                raise FetchError(f"Checksum mismatch for {fname} (expected {want}, got {got}). The download was "
                                 "corrupted or tampered with; nothing was installed. Try again.")
            log("Checksum ok, extracting")
            _extract(arc, a["kind"], stage, system)
        missing = [t for t in TOOLS if not os.path.isfile(os.path.join(stage, _exe(t, system)))]
        if missing:
            raise FetchError("The archive did not contain " + ", ".join(missing) + ". Nothing was installed.")
        versions = {t: verify(os.path.join(stage, _exe(t, system))) for t in TOOLS}
        old = target + ".old"
        if os.path.isdir(target):
            shutil.rmtree(old, ignore_errors=True)
            os.replace(target, old)
        os.replace(stage, target)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    try:
        import media_tools
        media_tools.reset()
    except Exception:
        pass
    return {"dir": target, "versions": versions, "source": p["source"]}


def main(argv=None):
    import argparse
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="kurgu setup-ffmpeg", description="Download a static ffmpeg + ffprobe for Kurgu (no admin rights).")
    ap.add_argument("--force", action="store_true", help="download even when ffmpeg is already found")
    ap.add_argument("--json", action="store_true", help="print one JSON object (for agents)")
    a = ap.parse_args(argv)
    import media_tools
    media_tools.reset()
    have = {t: media_tools.find(t) for t in TOOLS}
    if all(have.values()) and not a.force:
        res = {"ok": True, "changed": False, "ffmpeg": have["ffmpeg"], "ffprobe": have["ffprobe"]}
        print(json.dumps(res) if a.json else f"ffmpeg and ffprobe already available ({have['ffmpeg']}). Use --force to download a fresh copy.")
        return 0
    quiet = a.json
    try:
        last = [-1]

        def prog(done, total):
            if total and not quiet and done * 10 // total != last[0]:
                last[0] = done * 10 // total
                print(f"  {done * 100 // total}% of {total // (1 << 20)} MB", flush=True)
        res = install(progress=prog, log=(lambda *_: None) if quiet else print)
    except FetchError as e:
        print(json.dumps({"ok": False, "error": str(e)}) if a.json else f"setup-ffmpeg failed: {e}", file=sys.stdout if a.json else sys.stderr)
        return 1
    note = LICENSE_NOTE.format(source=res["source"])
    if a.json:
        print(json.dumps({"ok": True, "changed": True, **res, "license": note}))
    else:
        print(f"Installed to {res['dir']}\n  {res['versions']['ffmpeg']}\n  {res['versions']['ffprobe']}\n{note}")
        if platform.system() == "Windows":
            print("Note: Windows Defender may scan ffmpeg.exe on its first start (a few seconds). Files fetched by a script "
                  "carry no 'downloaded from the internet' mark, so SmartScreen normally stays quiet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
