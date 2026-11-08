"""Diagnostics: a small per-project event log (.kurgu/log.jsonl) and `kurgu report`, ONE plain-text report a user can paste when something does not work.

Events are {ts, kind, level, code?, msg, ctx?}. Never logged: note texts, project content, tokens, environment variables, full argv with prompts, and the agent's output (only a cause code from a fixed list).
The report holds Kurgu's own lines from hook.log; the agent's output (lines starting "| ", which may quote your notes) only with include_agent_output
(`kurgu report --include-agent-output`, or the checkbox in the editor's report window, off by default). Nothing is sent anywhere: the report is printed
(or written with -o) and the editor shows it in a window with a Copy button."""
import argparse
import json
import os
import platform
import re
import stat
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
_KEYS = re.compile(r"\b(?:AKIA[0-9A-Z]{16}|github_pat_[A-Za-z0-9_]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,})")
_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_LONG = re.compile(r"(?<![\w./\\-])[A-Za-z0-9+/_=-]{32,}(?![\w./\\-])")


def _mixed(m):
    s = m.group(0)
    return "<redacted>" if re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) and re.search(r"[0-9]", s) else s     # file names rarely mix all three


_OTHER_HOME = re.compile(r"(?i)(?:[A-Za-z]:)?[\\/](?:Users|home)[\\/][^\\/\r\n\"']+")      # the whole segment up to the next separator: "John Smith" has a space


def redact(text, home=None):
    """The home folder becomes ~ (case-insensitive for Windows paths and drive letters), any other /Users/<name>, /home/<name> or C:\\Users\\<name>
    becomes <user-home>, emails <email>, tokens / keys <redacted>. Project and file names stay."""
    text = str(text)
    homes = {h for h in (home, os.path.expanduser("~"), os.path.realpath(os.path.expanduser("~"))) if h and h not in ("/", "~")}
    for h in sorted(homes, key=len, reverse=True):
        win = os.name == "nt" or bool(re.match(r"^[A-Za-z]:", h))
        forms = {h, h.replace("\\", "/"), h.replace("/", "\\")}
        for form in forms:
            text = re.sub(re.escape(form), "~", text, flags=re.I if win else 0)
    text = _OTHER_HOME.sub("<user-home>", text)
    text = _EMAIL.sub("<email>", text)
    text = _BEARER.sub("Bearer <redacted>", text)
    text = _ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _SK.sub("<redacted>", text)
    text = _KEYS.sub("<redacted>", text)
    text = _HEX.sub("<redacted>", text)
    return _LONG.sub(_mixed, text)


# Known causes of a failed wake run, matched LOCALLY on the agent's last output lines. Only the code is stored / reported, never the lines.
CAUSES = (("not_logged_in", r"not logged in|please run /login|/login|invalid api key|authentication"), ("command_not_found", r"command not found|not found on path|no such file or directory"),
          ("permission_denied", r"permission denied|operation not permitted"), ("untrusted_directory", r"not inside a trusted directory"),
          ("policy_problem", r"policyproblem|policy problem"), ("rate_limited", r"rate limit|too many requests|usage limit"),
          ("network", r"could not resolve|connection (?:refused|reset|error)|network is unreachable|timed? ?out"))


def cause_from(lines):
    """A code from CAUSES for the output lines, or ''."""
    blob = "\n".join(str(l) for l in lines).lower()
    for code, pat in CAUSES:
        if re.search(pat, blob):
            return code
    return ""


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
        if not os.path.isdir(d):                          # never create a project folder just to log into it
            return False
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


def open_regular(path, binary=False):
    """Open a project-side file for reading as UTF-8 text (bytes with binary=True), only if it is a regular file (a FIFO or device would block or never end).
    Symlinks are refused where O_NOFOLLOW exists."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"not a regular file: {os.path.basename(path)}")
        return os.fdopen(fd, "rb") if binary else os.fdopen(fd, encoding="utf-8", errors="replace")
    except BaseException:
        os.close(fd)
        raise


def kurgu_is_link(d):
    return os.path.islink(os.path.join(d, ".kurgu"))


def read_events(d, n=200):
    out = []
    if kurgu_is_link(d):
        return out
    path = os.path.join(d, LOG)
    for p in (f"{path}.2", f"{path}.1", path):
        try:
            if os.path.islink(p):
                continue
            with open_regular(p) as f:
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


_RUN = re.compile(r"\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\] run \(send #(\d+), (\d+) notes\): (.*)", re.ASCII)
_DONE = re.compile(r"  (finished, exit code -?\d+|stopped: still running after \d+(\.\d+)? s)", re.ASCII)
_NOSTART = re.compile(r"  could not start: \[(Errno|WinError) (-?\d+)\].*", re.ASCII)
_ERR_CLASS = re.compile(r"(Uncaught )?([A-Za-z_][A-Za-z0-9_.]{0,60})(?=:|$)", re.ASCII)


def own_line(line):
    """A hook.log line rebuilt from Kurgu's own fixed shapes and numbers only, else None.
    Nothing free-form is copied: agent output (also a legacy unprefixed line shaped like Kurgu's) may quote the user's notes."""
    m = _RUN.fullmatch(line)
    if m:
        shown = m.group(4)
        what = next((f"kurgu wake {a}" for a in ("claude", "codex") if f" wake {a} " in f" {shown} "), None)
        what = what or ("custom on_send" if shown.endswith(" arguments)") and "(custom on_send, " in shown else "(command not shown)")
        return f"[{m.group(1)}] run (send #{m.group(2)}, {m.group(3)} notes): {what}"
    m = _DONE.fullmatch(line)
    if m:
        return line
    m = _NOSTART.fullmatch(line)
    if m:
        return f"  could not start: [{m.group(1)} {m.group(2)}]"
    return None


_JS_CLASSES = ("Error", "TypeError", "ReferenceError", "RangeError", "SyntaxError", "URIError", "EvalError", "AggregateError", "DOMException", "AbortError",
               "NotAllowedError", "NotSupportedError", "SecurityError", "QuotaExceededError")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_VERSION = re.compile(r"^[0-9A-Za-z.+~-]{1,30}$")
_ROUTE = re.compile(r"^/api/[a-z0-9/_-]{1,60}$")
_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
# Every message Kurgu writes itself (the diag.event call sites). Anything else in a log.jsonl came from somewhere else (a project can ship one): dropped.
FIXED_MSGS = ("server started", "server stopped", "notes sent", "hook run started", "hook run finished", "hook run stopped (timeout)", "rerender failed", "import failed", "import warning")
_CODEX_MSG = re.compile(r"^codex not started \([a-z_]{1,30}\)$")


def _i(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and -10**9 < v < 10**12 else None


def _b(v):
    return v if isinstance(v, bool) else None


def _c(v, allowed=None):
    return v if isinstance(v, str) and _CODE.match(v) and (allowed is None or v in allowed) else None


KURGU_EXCEPTIONS = ("ToolError", "KurguRefusal", "ElementsError", "RerenderError", "SceneImportError", "PolicyProblem", "ProjectError", "ConflictError")


def _py_class(name):
    import builtins
    if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Za-z0-9_]{0,40}", name):
        return None
    b = getattr(builtins, name, None)
    return name if (isinstance(b, type) and issubclass(b, BaseException)) or name in KURGU_EXCEPTIONS else None


def _own_web_files():
    try:
        return {n for n in os.listdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "js")) if n.endswith(".js")}
    except OSError:
        return set()


def _event_for_report(e, include_agent_output):
    """The default report copies NOTHING free-form from log.jsonl (a project can ship one, and browsers and exceptions write text into it): only the kinds Kurgu
    writes, only fixed message strings, and per kind only the ctx values that are numbers, booleans or fixed codes. Returns None for an event to leave out."""
    if include_agent_output:
        return e
    if not isinstance(e, dict):
        return None
    kind, ctx_in = e.get("kind"), e.get("ctx") if isinstance(e.get("ctx"), dict) else {}
    out = {"ts": e["ts"] if isinstance(e.get("ts"), str) and _ISO.match(e["ts"]) else None, "kind": kind, "level": _c(e.get("level"), ("info", "warn", "error"))}
    code = _c(e.get("code")) or (str(_i(e.get("code"))) if _i(e.get("code")) is not None else None)
    ctx = {}
    msg = e.get("msg") if isinstance(e.get("msg"), str) else ""
    if kind == "server":
        ctx = {"version": ctx_in.get("version") if isinstance(ctx_in.get("version"), str) and _VERSION.match(ctx_in["version"]) else None, "port": _i(ctx_in.get("port")), "pid": _i(ctx_in.get("pid"))}
    elif kind == "send":
        ctx = {"count": _i(ctx_in.get("count")), "listening": _b(ctx_in.get("listening")), "wake": _c(ctx_in.get("wake"), ("configured", "none")),
               "hook": _c(ctx_in.get("hook"), ("started", "queued", "error", "none")), "failed_frames": _i(ctx_in.get("failed_frames"))}
    elif kind == "wake":
        ctx = {"seq": _i(ctx_in.get("seq")), "notes": _i(ctx_in.get("notes")), "exit": _i(ctx_in.get("exit")), "limit_s": _i(ctx_in.get("limit_s")),
               "cause": _c(ctx_in.get("cause"), tuple(c for c, _ in CAUSES)), "code": _c(ctx_in.get("code"))}
    elif kind == "refusal":
        msg = ""                                                          # str(err) of a refusal names paths
        ctx = {}
    elif kind == "frontend":
        m = _ERR_CLASS.match(msg)
        msg = m.group(2) if m and m.group(2) in _JS_CLASSES else "(message left out)"
        src = ctx_in.get("source")
        ctx = {"source": src if isinstance(src, str) and src in _own_web_files() else None, "line": _i(ctx_in.get("line")), "col": _i(ctx_in.get("col"))}
    elif kind == "error":
        m = _ERR_CLASS.match(msg)
        msg = _py_class(m.group(2)) if m else None
        msg = msg or "(class left out)"
        route = ctx_in.get("route")
        ctx = {"route": route if isinstance(route, str) and _ROUTE.match(route) else None, "method": ctx_in.get("method") if ctx_in.get("method") in _METHODS else None,
               "exc": _py_class(ctx_in.get("exc"))}
    elif kind in ("rerender", "import"):
        ctx = {}
    else:
        return None
    if kind not in ("frontend", "error", "refusal") and not (msg in FIXED_MSGS or _CODEX_MSG.match(msg)):
        msg = ""
    out["msg"] = msg
    out["code"] = code
    out["ctx"] = {k: v for k, v in ctx.items() if v is not None}
    return {k: v for k, v in out.items() if v not in (None, "", {})}


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
_WAKE_STATES = ("running", "finished", "stopped")
_WAKE_REASONS = ("login", "missing", "codex_no_auth_file", "codex_auth_replaced", "codex_prepare_failed", "codex_policy")


def _wake_line(w):
    """One line describing the last wake run from wake.json, built only from values whose shape is checked (a shipped wake.json may hold anything)."""
    def num(v):
        return str(v) if isinstance(v, int) and not isinstance(v, bool) and -10**6 < v < 10**9 else ""
    out = []
    if w.get("state") in _WAKE_STATES:
        out.append(f"state={w['state']}")
    for k in ("seq", "count", "exit_code"):
        if num(w.get(k)):
            out.append(f"{k}={num(w[k])}")
    for k in ("started", "ended"):
        if isinstance(w.get(k), str) and _ISO.match(w[k]):
            out.append(f"{k}={w[k]}")
    r = w.get("reason")
    if r:
        out.append(f"reason={r if r in _WAKE_REASONS else 'other'}")
    return "  ".join(out) or "(unreadable)"


def build_report(d, include_agent_output=False):
    import agent_notes
    d = os.path.realpath(d)
    ver, commit = _kurgu_version()
    L = ["Kurgu report", "=" * 12, f"time:    {datetime.now().replace(microsecond=0).isoformat()}",
         f"kurgu:   {ver}" + (f" ({commit})" if commit else ""), f"os:      {platform.platform()}",
         f"python:  {sys.version.split()[0]}", f"project: {os.path.basename(d) if include_agent_output else '<project>'}"]
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
    L += ["", "Last wake run (.kurgu/wake.json)", "-" * 30]
    w = {} if kurgu_is_link(d) else (agent_notes.read_wake(d) or {})
    if not w:
        L.append("(none on record)")
    else:
        L.append(_wake_line(w))                                           # fixed shapes only: numbers, timestamps, a known state and reason code
        if include_agent_output:                                          # the summary / tail are lines the agent printed: they may quote the user's notes
            L += [f"summary: {w.get('summary', '')}"] + [f"| {x}" for x in (w.get("tail") or []) if isinstance(x, str)]
    L += ["", "Doctor", "------"]
    try:
        import doctor
        L.append(doctor.render(doctor.collect(d, log=lambda *a, **k: None)).rstrip())
    except Exception as e:
        L.append(f"(doctor failed: {type(e).__name__})")
    L += ["", "Last events (newest last)", "-------------------------"]
    if kurgu_is_link(d):
        L.append("(refused: .kurgu is a symbolic link)")
    else:
        shown = [_event_for_report(e, include_agent_output) for e in read_events(d, 200)]
        L += [json.dumps(x, ensure_ascii=False) for x in shown if x is not None] or ["(none)"]
    L += ["", "hook.log (last 50 lines" + (", with the agent's output" if include_agent_output else ", agent output left out") + ")", "-" * 30]
    try:
        p = os.path.join(d, agent_notes.HOOK_LOG)
        if kurgu_is_link(d) or os.path.islink(p):
            raise OSError("symbolic link")
        with open_regular(p) as f:
            lines = [l.rstrip("\n") for l in f.readlines()]
        if not include_agent_output:
            lines = [o for o in map(own_line, lines) if o is not None]     # allowlist, rebuilt from fixed shapes + numbers (agent output, also legacy unprefixed output, may quote notes)
        L += lines[-50:] or ["(empty)"]
    except OSError as e:
        L.append("(refused: hook.log is a symbolic link)" if os.path.islink(os.path.join(d, ".kurgu")) or "symbolic" in str(e) else "(no hook.log)")
    return redact("\n".join(L), home=os.path.expanduser("~")) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kurgu report", description="Print one plain-text report to paste when something does not work. Nothing is sent anywhere.")
    ap.add_argument("project_dir", nargs="?", default=".")
    ap.add_argument("--include-agent-output", action="store_true", help="also add the last lines of the agent's output from hook.log (it may quote your notes)")
    ap.add_argument("-o", "--output", action="store_true", help="write it to <project>/.kurgu/report-<time>.txt instead of printing")
    a = ap.parse_args(argv)
    d = os.path.realpath(os.path.expanduser(a.project_dir))
    if not os.path.isdir(d):
        print(f"error: {d} is not a folder", file=sys.stderr)
        return 1
    text = build_report(d, a.include_agent_output)
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
