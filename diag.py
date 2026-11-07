"""Diagnostics: a small per-project event log (.kurgu/log.jsonl) and `kurgu report`, ONE plain-text report a user can paste when something does not work.

Events are {ts, kind, level, code?, msg, ctx?}. Never logged: note texts, project content, tokens, environment variables, full argv with prompts.
Nothing is sent anywhere: the report is printed (or written with -o) and the editor shows it in a window with a Copy button."""
import argparse
import json
import os
import platform
import re
import sys
import threading
import time
from datetime import datetime

LOG = os.path.join(".kurgu", "log.jsonl")
MAX_BYTES = 1_000_000        # rotate at ~1 MB ...
KEEP = 3                     # ... keeping log.jsonl, log.jsonl.1, log.jsonl.2
_lock = threading.Lock()


# ---------------------------------------------------------------- redaction
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SK = re.compile(r"\b(?:sk|pk|ghp|gho|xox[abp]|AIza)[-_][A-Za-z0-9_-]{8,}")
_ASSIGN = re.compile(r"(?i)\b([A-Za-z_]*(?:token|secret|password|passwd|api[_-]?key|apikey|authorization)[A-Za-z_]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)")
_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_LONG = re.compile(r"(?<![\w./\\-])[A-Za-z0-9+/_=-]{32,}(?![\w./\\-])")


def _mixed(m):
    s = m.group(0)
    return "<redacted>" if re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) and re.search(r"[0-9]", s) else s     # file names rarely mix all three


def redact(text, home=None):
    """The home folder becomes ~, emails <email>, tokens / keys <redacted>. Project and file names stay."""
    text = str(text)
    homes = {h for h in (home, os.path.expanduser("~"), os.path.realpath(os.path.expanduser("~"))) if h and h not in ("/", "~")}
    for h in sorted(homes, key=len, reverse=True):
        for form in (h, h.replace("\\", "/"), h.replace("/", "\\")):
            text = text.replace(form, "~")
    text = _EMAIL.sub("<email>", text)
    text = _BEARER.sub("Bearer <redacted>", text)
    text = _ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _SK.sub("<redacted>", text)
    text = _HEX.sub("<redacted>", text)
    return _LONG.sub(_mixed, text)


def _clip(v, n=200):
    s = redact(str(v)).replace("\n", " ").replace("\r", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------- the event log
def _rotate(path):
    try:
        if os.path.getsize(path) < MAX_BYTES:
            return
    except OSError:
        return
    for i in range(KEEP - 1, 0, -1):
        src = path if i == 1 else f"{path}.{i - 1}"
        try:
            os.replace(src, f"{path}.{i}") if os.path.exists(src) else None
        except OSError:
            pass


def event(d, kind, msg="", level="info", code=None, ctx=None):
    """Append one event. Best effort: a diagnostics failure must never break the thing being diagnosed. Returns True when written."""
    try:
        import agent_notes
        path = os.path.join(d, LOG)
        e = {"ts": datetime.now().replace(microsecond=0).isoformat(), "kind": kind, "level": level}
        if code:
            e["code"] = str(code)[:60]
        e["msg"] = _clip(msg, 300)
        if ctx:
            e["ctx"] = {str(k)[:40]: (v if isinstance(v, (bool, int, float)) or v is None else _clip(v)) for k, v in list(ctx.items())[:12]}
        with _lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _rotate(path)
            with agent_notes.open_nofollow(path, "a") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return True
    except Exception:                                     # includes KurguRefusal (symlinked log): refused silently, the send/hook path reports it
        return False


def refusal(d, err):
    event(d, "refusal", str(err), "warn", code=getattr(err, "code", None), ctx=getattr(err, "params", None))


def read_events(d, n=200):
    out = []
    path = os.path.join(d, LOG)
    for p in (f"{path}.2", f"{path}.1", path):
        try:
            if os.path.islink(p):
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
    return out[-n:]


# ---------------------------------------------------------------- the report
def _run(argv, timeout=8):
    import subprocess
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return (r.stdout or r.stderr or "").strip().splitlines()[0] if (r.stdout or r.stderr).strip() else ""
    except Exception:
        return ""


def _kurgu_version():
    here = os.path.dirname(os.path.abspath(__file__))
    ver, commit = "", ""
    try:
        with open(os.path.join(here, "pyproject.toml"), encoding="utf-8") as f:
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', f.read())
            ver = m.group(1) if m else ""
    except OSError:
        pass
    if not ver:
        try:
            from importlib.metadata import version
            ver = version("kurgu")
        except Exception:
            ver = "unknown"
    if os.path.isdir(os.path.join(here, ".git")) or os.path.isfile(os.path.join(here, ".git")):
        commit = _run(["git", "-C", here, "rev-parse", "--short", "HEAD"], 5)
    return ver, commit


def _chrome():
    import shutil
    cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    cands += [shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser", "chrome")]
    for c in cands:
        if c and os.path.exists(c):
            return _run([c, "--version"], 8) or "found"
    return "not found"


def build_report(d):
    import agent_notes
    d = os.path.realpath(d)
    ver, commit = _kurgu_version()
    L = ["Kurgu report", "=" * 12, f"time:    {datetime.now().replace(microsecond=0).isoformat()}",
         f"kurgu:   {ver}" + (f" ({commit})" if commit else ""), f"os:      {platform.platform()}",
         f"python:  {sys.version.split()[0]}", f"project: {os.path.basename(d)}"]
    try:
        import media_tools
        L.append("ffmpeg:  " + (_run([media_tools.ffmpeg(), "-version"]) or "not found"))
    except Exception:
        L.append("ffmpeg:  not found")
    L.append("chrome:  " + _chrome())
    L += ["", "Agent setup", "-----------"]
    cmd = agent_notes.on_send_command()
    if not cmd:
        L.append("wake:      not configured")
    else:
        agent = cmd[cmd.index("wake") + 1] if "wake" in cmd[:-1] and cmd[cmd.index("wake") + 1] in ("claude", "codex") else "custom command"
        L.append(f"wake:      configured ({agent})")
    L.append(f"listening: {'yes' if agent_notes.is_listening(d) else 'no'}")
    L.append(f"agent name set: {'yes' if agent_notes.agent_name(d) else 'no'}")
    L += ["", "Doctor", "------"]
    try:
        import doctor
        L.append(doctor.render(doctor.collect(d, log=lambda *a, **k: None)).rstrip())
    except Exception as e:
        L.append(f"(doctor failed: {type(e).__name__})")
    L += ["", "Last events (newest last)", "-------------------------"]
    ev = read_events(d, 200)
    L += [json.dumps(e, ensure_ascii=False) for e in ev] or ["(none)"]
    L += ["", "hook.log (last 50 lines)", "------------------------"]
    try:
        p = os.path.join(d, agent_notes.HOOK_LOG)
        if os.path.islink(p):
            raise OSError("symbolic link")
        with open(p, encoding="utf-8", errors="replace") as f:
            L += [l.rstrip("\n") for l in f.readlines()[-50:]] or ["(empty)"]
    except OSError:
        L.append("(no hook.log)")
    return redact("\n".join(L), home=os.path.expanduser("~")) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kurgu report", description="Print one plain-text report to paste when something does not work. Nothing is sent anywhere.")
    ap.add_argument("project_dir", nargs="?", default=".")
    ap.add_argument("-o", "--output", action="store_true", help="write it to <project>/.kurgu/report-<time>.txt instead of printing")
    a = ap.parse_args(argv)
    d = os.path.realpath(os.path.expanduser(a.project_dir))
    if not os.path.isdir(d):
        print(f"error: {d} is not a folder", file=sys.stderr)
        return 1
    text = build_report(d)
    if a.output:
        import agent_notes
        path = os.path.join(d, ".kurgu", f"report-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with agent_notes.open_nofollow(path, "w") as f:
                f.write(text)
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(path)
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
