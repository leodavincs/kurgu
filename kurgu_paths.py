"""Where Kurgu keeps things: bundled data (web/, fonts/, skills/) and the per-user cache/config folders.

* From a git checkout the data sits next to the modules.
* From a wheel (pipx / uvx / pip) setup.py ships it under <prefix>/share/kurgu.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))


def data_root():
    """Folder that holds web/, fonts/, skills/ and .claude-plugin/."""
    if os.path.isdir(os.path.join(HERE, "web")):
        return HERE
    import sysconfig
    for base in (sys.prefix, sysconfig.get_path("data"), getattr(sys, "base_prefix", sys.prefix)):
        d = os.path.join(base, "share", "kurgu")
        if os.path.isdir(os.path.join(d, "web")):
            return d
    return HERE


def _user_dir(kind):
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Caches" if kind == "cache" else "Application Support", "kurgu")
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(base, "kurgu", "Cache" if kind == "cache" else "Config")
    var, fallback = (("XDG_CACHE_HOME", ".cache") if kind == "cache" else ("XDG_CONFIG_HOME", ".config"))
    return os.path.join(os.environ.get(var) or os.path.join(home, fallback), "kurgu")


def cache_dir():
    """Per-user cache folder (KURGU_CACHE overrides it). Created on demand."""
    d = os.environ.get("KURGU_CACHE") or _user_dir("cache")
    os.makedirs(d, exist_ok=True)
    return d


def config_file():
    """The user config file: <config folder>/config.json. KURGU_CONFIG may name the FOLDER (the usual way) or the config FILE itself (a path ending in .json or an
    existing file); then the file is used as given and its folder holds the rest (codex-home, agents.key)."""
    v = os.environ.get("KURGU_CONFIG")
    if v and (v.lower().endswith(".json") or os.path.isfile(v)):
        return v
    return os.path.join(config_dir(), "config.json")


def config_dir():
    """Per-user config folder (KURGU_CONFIG overrides it: a folder, or the path of the config file whose folder is used). Not created.
    ~/.config/kurgu on macOS and Linux (XDG_CONFIG_HOME honoured), %APPDATA%\\kurgu on Windows."""
    if os.environ.get("KURGU_CONFIG"):
        v = os.environ["KURGU_CONFIG"]
        return os.path.dirname(os.path.abspath(v)) if (v.lower().endswith(".json") or os.path.isfile(v)) else v
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"), "kurgu")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "kurgu")


# ---------------------------------------------------------------- project / output confinement for the CLI commands an agent can run
class ProjectError(ValueError):
    pass


def require_project(d):
    """Realpath of `d` when it is a real Kurgu project: a regular project.json (not a symlink) that parses to an object with a layers list, and a
    `.kurgu` folder that is not a symlink. Raises ProjectError otherwise. Woken agents run these commands without an OS sandbox."""
    import json
    real = os.path.realpath(os.path.expanduser(d))
    pj = os.path.join(real, "project.json")
    if not os.path.isdir(real) or not os.path.isfile(pj) or os.path.islink(pj):
        raise ProjectError(f"No project.json in {real} (not a Kurgu project folder: a regular project.json file is required)")
    try:
        with open(pj, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise ProjectError(f"project.json is not valid JSON: {d}")
    if not isinstance(data, dict) or not isinstance(data.get("layers", []), list):
        raise ProjectError(f"project.json is not a Kurgu project: {d}")
    if os.path.islink(os.path.join(real, ".kurgu")):
        raise ProjectError(".kurgu is a symlink: refusing to write through it")
    return real


def look_output(d, out, default_name):
    """Where `kurgu frame/strip` may write: only inside <project>/.kurgu/look/. `out` None -> default name there; a bare file name goes there too; any other
    path must resolve (symlinks followed) inside that folder. Raises ProjectError. Creates the folder; refuses a symlinked look folder or target file."""
    look = os.path.join(d, ".kurgu", "look")
    if os.path.islink(look):
        raise ProjectError(".kurgu/look is a symlink: refusing to write through it")
    os.makedirs(look, exist_ok=True)
    if out is None:
        path = os.path.join(look, default_name)
    elif os.path.basename(out) == out:
        path = os.path.join(look, out)
    else:
        path = os.path.abspath(os.path.expanduser(out))
    real_look = os.path.realpath(look)
    parent = os.path.realpath(os.path.dirname(path))
    if parent != real_look and not parent.startswith(real_look + os.sep):
        raise ProjectError(f"-o must point inside {os.path.join('.kurgu', 'look')}/ of the project (got {out}); leave -o out to use the default")
    if os.path.islink(path) or not os.path.splitext(path)[1].lower() in (".jpg", ".jpeg", ".png"):
        raise ProjectError("-o must be a new .jpg/.jpeg/.png file name (not a symlink)")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def clamp_time(t, duration):
    """(t, warning|None): t limited to just inside [0, duration]."""
    if duration and t > duration:
        return max(0.0, duration - 0.04), f"{t:g} s is past the end of the project ({duration:g} s): using {max(0.0, duration - 0.04):.2f} s"
    return t, None


# ---------------------------------------------------------------- hardening shared by every headless Chrome Kurgu starts
WEBRTC_FLAGS = ["--use-mock-keychain", "--password-store=basic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--webrtc-ip-handling-policy=disable_non_proxied_udp"]
# WebRTC sends UDP around a proxy: switch the API off in every document (belt and braces next to the flags)
WEBRTC_JS = ("(function(){var n=['RTCPeerConnection','webkitRTCPeerConnection','RTCDataChannel','RTCSessionDescription','RTCIceCandidate'];"
             "n.forEach(function(k){try{Object.defineProperty(window,k,{value:undefined,configurable:false,writable:false})}catch(e){}});"
             "try{if(navigator.mediaDevices){navigator.mediaDevices.getUserMedia=function(){return Promise.reject(new Error('blocked'))}}}catch(e){}})();")


def no_sandbox_flags():
    """Chrome's sandbox stays ON, except as root (it cannot run sandboxed there) or when the user sets KURGU_NO_SANDBOX=1 (containers / CI)."""
    root = sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0
    if root or os.environ.get("KURGU_NO_SANDBOX") == "1":
        return ["--no-sandbox", "--disable-dev-shm-usage"]
    return []


# ---------------------------------------------------------------- Windows .cmd / .bat shims (npm installs `claude` and `codex` as .cmd)
SHELL_META = '&|<>^%"!'


def is_shim(exe):
    return isinstance(exe, str) and exe.lower().endswith((".cmd", ".bat"))


def shim_unsafe(exe, argv):
    """Arguments (beyond argv[0]) that cmd.exe would interpret when `exe` is a .cmd/.bat shim: any of & | < > ^ % " !. [] for a real executable."""
    if not is_shim(exe):
        return []
    return [a for a in argv[1:] if any(ch in a for ch in SHELL_META)]


def stop_chrome(proc, graceful=None, profile=None, wait=6.0):
    """Stop a headless Chrome the way it expects: ask it to quit (`graceful()`, e.g. CDP Browser.close), wait for it to exit, only then terminate (SIGTERM, also a
    normal quit for Chrome), and kill as the last resort; only after it has exited is its user-data-dir deleted. Killing Chrome or pulling its profile away while
    it runs makes it abort, which macOS reports as "Google Chrome quit unexpectedly". Never raises."""
    import shutil
    import subprocess
    import time
    try:
        if proc.poll() is None and graceful:
            try:
                graceful()
            except Exception:
                pass
        for step in ("wait", "terminate", "kill"):
            if proc.poll() is not None:
                break
            if step == "terminate":
                proc.terminate()
            elif step == "kill":
                proc.kill()
            try:
                proc.wait(wait if step != "kill" else 5)
            except subprocess.TimeoutExpired:
                pass
    except Exception:
        pass
    if profile:
        for _ in range(5):                                  # Chrome may still be flushing for a moment after the process object reports exit
            shutil.rmtree(profile, ignore_errors=True)
            if not os.path.exists(profile):
                break
            time.sleep(0.2)


# ---------------------------------------------------------------- temp files next to project files
def write_bytes_atomic(path, data):
    """Write `data` to `path` through a random-named exclusive temp file and os.replace: a symlink shipped at `path` is replaced, never written through."""
    tmp = tmp_path(path)
    try:
        with excl_open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def inside(base, path):
    """True when realpath(path) is realpath(base) or below it."""
    b, p = os.path.realpath(base), os.path.realpath(path)
    return p == b or p.startswith(b.rstrip(os.sep) + os.sep)


def open_append_fd(path):
    """A file descriptor opened for appending (created 0600 when new), never through a symlink: for a log a child process writes to."""
    return os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)


def tmp_path(path, suffix=".tmp"):
    """A temp file name for `path` that nobody can plant a symlink on in advance: `<path>.<16 random hex><suffix>`. A downloaded project folder
    may contain `film.json.part` -> ~/.ssh/x; with a predictable name an atomic write would follow it."""
    import secrets
    return f"{path}.{secrets.token_hex(8)}{suffix}"


def excl_open(path, mode="w", encoding="utf-8"):
    """Create `path` (O_CREAT | O_EXCL | O_NOFOLLOW): fails when anything, even a dangling symlink, is already there. mode "w" (UTF-8 text, \n newlines) or "wb"."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o644)
    return os.fdopen(fd, mode) if "b" in mode else os.fdopen(fd, mode, encoding=encoding, newline="\n")


def chrome_blocked_reason():
    """Why Chrome cannot run here, or None. Inside Codex's macOS sandbox (CODEX_SANDBOX=seatbelt) Chrome cannot reach the window server and ABORTS at start
    (verified with `codex sandbox`: SIGABRT in RegisterApplication, also with --no-sandbox), which macOS reports as "Google Chrome quit unexpectedly"."""
    if sys.platform == "darwin" and os.environ.get("CODEX_SANDBOX") == "seatbelt":
        return ("Chrome cannot start inside Codex's macOS sandbox (it aborts: no window-server access, whatever the flags). "
                "The video is UNCHANGED, so do not mark the note done: fix the source, then ask the human to run `kurgu rerender <project> <unit>` in a terminal (`kurgu notes ask <dir> <id> --question '...'`), or leave the note sent and say so in your reply")
    return None
