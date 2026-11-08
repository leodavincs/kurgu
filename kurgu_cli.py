"""The `kurgu` command: `kurgu doctor`, `kurgu setup-agent`, `kurgu setup-ffmpeg`, `kurgu notes ...`, otherwise the editor server (`kurgu <project>`).

`setup-agent` lives here (not in its own module) so an installed tool needs no new py-module entry."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

# ----------------------------------------------------------------------------- agent instructions (generated, see guidance/)
BEGIN = "<!-- kurgu:begin (managed by `kurgu setup-agent`; edit outside this block) -->"
END = "<!-- kurgu:end -->"

# AGENT_BLOCK, WAKE_PROMPT and the `kurgu --help` table are generated from guidance/ (python scripts/gen_guidance.py); do not edit them here.
from guidance_gen import AGENT_BLOCK, COMMAND_HELP, HELP_TOPICS, WAKE_PROMPT   # noqa: E402

SKILL_CODEX = """\
---
name: kurgu
description: Edit a video as code with Kurgu (a local timeline editor). Use when the user asks to create, cut, caption, add titles/music/effects to or render a video, mentions project.json or the Kurgu editor, or says "check my notes" / "apply my notes" for a video review. Do not use for unrelated media tasks.
---

""" + AGENT_BLOCK.split("\n", 2)[2]


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def _checkout_dir():
    """The git checkout / plugin folder this file lives in, or None when installed as a package."""
    here = os.path.dirname(os.path.abspath(__file__))
    return here if os.path.isfile(os.path.join(here, "mcp_server.py")) and os.path.isdir(os.path.join(here, "skills")) else None


def mcp_command():
    """(command, args) that starts the Kurgu MCP server: the installed `kurgu-mcp` script, else this checkout's mcp_server.py."""
    exe = shutil.which("kurgu-mcp")
    if not exe:
        for d in (os.path.expanduser("~/.local/bin"), os.path.dirname(os.path.abspath(sys.argv[0] or ""))):
            for n in ("kurgu-mcp", "kurgu-mcp.exe"):
                if os.path.isfile(os.path.join(d, n)):
                    exe = os.path.join(d, n)
                    break
            if exe:
                break
    if exe:
        return os.path.abspath(exe), []
    here = os.path.dirname(os.path.abspath(__file__))
    return sys.executable, [os.path.join(here, "mcp_server.py")]


# ----------------------------------------------------------------------------- TOML (text-level: keeps comments and the other servers)
TOOL_TIMEOUT = 3600     # Codex default is 60 s; notes_wait blocks up to 3500 s and a full render takes minutes
STARTUP_TIMEOUT = 30


def _q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def codex_block_lines(command, args):
    return ["command = " + _q(command),
            "args = [" + ", ".join(_q(a) for a in args) + "]",
            f"startup_timeout_sec = {STARTUP_TIMEOUT}",
            f"tool_timeout_sec = {TOOL_TIMEOUT}"]


_HDR = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")
MANAGED = ("command", "args", "startup_timeout_sec", "tool_timeout_sec")


def merge_codex_config(text, command, args, name="kurgu"):
    """Return config.toml text with [mcp_servers.<name>] set to our command/args/timeouts. Other keys of that table, its sub-tables,
    other servers, comments and everything else stay byte for byte."""
    lines = text.split("\n")
    head = f"mcp_servers.{name}"
    start = None
    for i, ln in enumerate(lines):
        m = _HDR.match(ln)
        if m and not ln.lstrip().startswith("[["):
            h = m.group(1).replace('"', "")
            if h == head:
                start = i
                break
    new = codex_block_lines(command, args)
    if start is None:
        out = text
        if out and not out.endswith("\n"):
            out += "\n"
        if out.strip():
            out += "\n"
        return out + f"[{head}]\n" + "\n".join(new) + "\n"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _HDR.match(lines[j]):
            end = j
            break
    body = lines[start + 1:end]
    kept, k = [], 0
    while k < len(body):
        ln = body[k]
        m = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", ln)
        if m and m.group(1) in MANAGED:
            val = ln.split("=", 1)[1]
            if val.strip().startswith("[") and "]" not in val.split("#")[0]:     # multi-line array: skip to the closing bracket
                while k < len(body) and "]" not in body[k].split("#")[0]:
                    k += 1
            k += 1
            continue
        kept.append(ln)
        k += 1
    while kept and not kept[-1].strip():      # blank lines before the next table are re-added below
        kept.pop()
    tail = [""] + lines[end:] if end < len(lines) else [""]      # keep one blank line before the next table / the final newline
    return "\n".join(lines[:start + 1] + new + kept + tail)


def merge_marked_block(text, block):
    """Insert or replace the marked Kurgu section in an AGENTS.md text."""
    sec = BEGIN + "\n" + block.rstrip("\n") + "\n" + END + "\n"
    a, b = text.find("<!-- kurgu:begin"), text.find(END)
    if a != -1 and b != -1 and b > a:
        return text[:a] + sec + text[b + len(END):].lstrip("\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text.strip() else "") + sec


# ----------------------------------------------------------------------------- the command
def _write(path, new, dry, label, backup=True):
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old == new:
        print(f"  = {label}: already up to date ({path})")
        return False
    verb = "would write" if dry else "wrote"
    print(f"  + {label}: {verb} {path}" + ("" if old is not None else " (new file)"))
    if dry:
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if old is not None and backup:
        bak = f"{path}.kurgu-bak-{_stamp()}"
        shutil.copy2(path, bak)
        print(f"    backup: {bak}")
    tmp = path + ".kurgu-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    os.replace(tmp, path)
    return True


def setup_codex(home, project, dry, command, args):
    codex_home = os.environ.get("CODEX_HOME") or os.path.join(home, ".codex")
    print("Codex")
    cfg = os.path.join(codex_home, "config.toml")
    old = ""
    if os.path.exists(cfg):
        with open(cfg, encoding="utf-8") as f:
            old = f.read()
    new = merge_codex_config(old, command, args)
    try:
        import tomllib
        tomllib.loads(new)
    except ImportError:
        pass
    except Exception as e:
        print(f"  ! refusing to write {cfg}: the result would not be valid TOML ({e}). Fix the file by hand or add:\n[mcp_servers.kurgu]\n" + "\n".join(codex_block_lines(command, args)))
        return 1
    _write(cfg, new, dry, "MCP server [mcp_servers.kurgu]")
    skill_dir = os.path.join(home, ".agents", "skills", "kurgu")
    _write(os.path.join(skill_dir, "SKILL.md"), SKILL_CODEX, dry, "skill (~/.agents/skills/kurgu)", backup=False)
    ref = os.path.join(_checkout_dir() or "", "skills", "kurgu", "reference.md")
    if _checkout_dir() and os.path.isfile(ref):
        with open(ref, encoding="utf-8") as f:
            _write(os.path.join(skill_dir, "reference.md"), f.read(), dry, "skill reference", backup=False)
    if project:
        p = os.path.join(os.path.abspath(project), "AGENTS.md")
        old = ""
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                old = f.read()
        _write(p, merge_marked_block(old, AGENT_BLOCK), dry, "project AGENTS.md section")
    else:
        print("  (no --project: AGENTS.md untouched. Add the Kurgu section to a project with: kurgu setup-agent --codex --project <dir>)")
    print("  Restart Codex so it reloads config and skills. Check: codex mcp list")
    return 0


def setup_claude(dry, command, args, runner=subprocess.run):
    print("Claude Code")
    cmd = ["claude", "mcp", "add", "--scope", "user", "kurgu", "--", command] + list(args)
    shown = " ".join(_q(c) if " " in c else c for c in cmd)
    if _checkout_dir() and os.path.isfile(os.path.join(_checkout_dir(), ".mcp.json")) and not shutil.which("kurgu-mcp"):
        print("  Using the plugin? It registers the MCP server and the skill itself (.mcp.json, skills/kurgu); nothing to do.")
    if dry or not shutil.which("claude"):
        print(("  would run: " if dry else "  `claude` not found on PATH; run this where it is installed:\n  ") + shown)
        return 0
    got = runner(["claude", "mcp", "get", "kurgu"], capture_output=True, text=True)
    if got.returncode == 0:
        print("  = MCP server kurgu is already registered with Claude Code (remove it first with `claude mcp remove kurgu` to change it).")
        return 0
    r = runner(cmd, capture_output=True, text=True)
    print("  + registered with Claude Code" if r.returncode == 0 else f"  ! failed ({(r.stderr or r.stdout).strip()}); run: {shown}")
    return r.returncode


# tools the wake run needs (notes incl. ask, stills, strips, `kurgu detect` (OCR + shapes of a frame), `kurgu rerender` = it only runs the adaptor the USER's config / Kurgu itself defines, never a project-supplied command, ffprobe). No general Bash, python, ffmpeg or render.py (they can write files); guidance says one command per call.
# The Codex run has no allowlist: it is limited by sandbox_mode=workspace-write + approval_policy=never (see wake_attempts), so the same read-only commands work there.
CLAUDE_TOOLS = ("Bash(kurgu notes:*),Bash(kurgu frame:*),Bash(kurgu strip:*),Bash(kurgu rerender:*),Bash(kurgu detect:*),Bash(kurgu probe:*),"
                "Bash(kurgu --help),Bash(kurgu help:*),Bash(kurgu * --help),mcp__kurgu")        # --help only: read-only discovery; ffprobe stays out (it reads URLs)


def kurgu_launcher():
    """argv prefix that runs this Kurgu: the installed `kurgu` (absolute path), else this checkout's launcher, else this file under the running Python."""
    exe = shutil.which("kurgu")
    if not exe:
        for n in ("kurgu", "kurgu.exe"):
            c = os.path.join(os.path.expanduser("~/.local/bin"), n)
            if os.path.isfile(c):
                exe = c
                break
    if exe:
        return [os.path.abspath(exe)]
    here = _checkout_dir()
    if here:
        launcher = os.path.join(here, "kurgu.cmd" if os.name == "nt" else "kurgu")
        if os.path.isfile(launcher):
            return [launcher]
    return [sys.executable, os.path.abspath(__file__)]


def wake_command(agent):
    """The on_send hook: `kurgu wake <agent> {dir} {seq}`. The wrapper picks the right agent command (and falls back when there is no session to resume)."""
    return kurgu_launcher() + ["wake", agent, "{dir}", "{seq}"]


ACTIVE_SESSION_SECS = 120      # a session file written this recently is probably open in the app / a terminal: never resume it in the foreground


def _recent(path, now=None):
    try:
        return (time.time() if now is None else now) - os.path.getmtime(path) < ACTIVE_SESSION_SECS
    except OSError:
        return False


def claude_session_active(d, now=None):
    """True when the newest Claude Code session file of folder `d` was modified in the last ACTIVE_SESSION_SECS (two processes must not write one session)."""
    root = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(d)))
    try:
        files = [os.path.join(root, f) for f in os.listdir(root) if f.endswith(".jsonl")]
    except OSError:
        return False
    return any(_recent(f, now) for f in files)


def codex_rollout_active(sid, now=None):
    """True when the Codex rollout file of session `sid` was modified in the last ACTIVE_SESSION_SECS."""
    import glob
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    return any(_recent(f, now) for f in glob.glob(os.path.join(home, "sessions", "*", "*", "*", f"rollout-*{sid}.jsonl")))


def wake_attempts(agent, d, session=None):
    """[(label, argv)] in the order `kurgu wake` tries them. Both run non-interactively in the project folder, never stop for a question, and print JSON so
    hook.log records token usage / cost. `session` = the recorded session of the agent working on this project (agent_notes.latest_session) or None.
    With a session: Claude gets `--bg --resume <id>` (continues it in the background; Claude starts a copy itself when it is running), Codex gets
    `exec resume <id>` unless its rollout is active right now (then a new exec). Without one: the folder's last session, then a new one.
    Source folders outside the project that the USER trusts (render_adaptors.agent_roots) are made writable: `--add-dir` for Claude,
    `sandbox_workspace_write.writable_roots` for Codex (`exec resume` has no --add-dir; the config key works for both forms)."""
    prompt = WAKE_PROMPT.replace("{dir}", d)
    sid = session["session_id"] if session else None
    try:
        import render_adaptors
        roots = render_adaptors.agent_roots(d)
    except Exception:
        roots = []
    if agent == "codex":
        cfg = ["-c", "approval_policy=never"]                 # no double quotes anywhere: codex.cmd goes through cmd.exe (codex reads a bare word as a string)
        if roots:
            lit = [r for r in roots if "'" not in r and not any(ord(c) < 32 and c != "\t" or c == "\x7f" for c in r)]   # TOML literal strings ('...'): no escaping
            for r in roots:
                if r not in lit:
                    print(f"kurgu wake: codex will not get write access to {r!r} (a ' or a control character cannot travel in its settings); rename it to use it",
                          file=sys.stderr)
            if lit:
                cfg += ["-c", "sandbox_workspace_write.writable_roots=[" + ",".join(f"'{r}'" for r in lit) + "]"]
        new = ("new session", ["codex", "exec", "-s", "workspace-write", *cfg, "--skip-git-repo-check", "-C", d, "--json", prompt])
        if sid and codex_rollout_active(sid):
            return [(f"new session (Codex session {sid} was active in the last {ACTIVE_SESSION_SECS} s, not resuming it)", new[1])]
        which = ["resume", sid] if sid else ["resume", "--last"]
        return [(f"resume Codex session {sid}" if sid else "continue the last session", ["codex", "exec", *which, "--skip-git-repo-check", "--json", "-c", "sandbox_mode=workspace-write", *cfg, prompt]), new]
    # --setting-sources user: a downloaded project folder is untrusted; its .claude/settings*.json (hooks, permissions) and .mcp.json servers must not load in a
    # non-interactive run (claude -p skips the workspace trust prompt). The user's own settings still apply.
    common = ["--setting-sources", "user", "--output-format", "json", "--permission-mode", "acceptEdits", "--allowedTools", CLAUDE_TOOLS]
    extra = [x for r in roots for x in ("--add-dir", r)]            # last on the line: --add-dir is variadic
    fresh = ["claude", "-p", prompt, *common, *extra]
    out = []
    if sid:
        out.append((f"background: claude --bg --resume {sid}", ["claude", "--bg", "--resume", sid, prompt, "--setting-sources", "user", "--permission-mode", "acceptEdits", "--allowedTools", CLAUDE_TOOLS, *extra]))   # the prompt goes BEFORE the variadic --allowedTools, which would swallow it
    if claude_session_active(d):                    # the app / a terminal is using the folder's last session right now: start a separate one
        return out + [(f"new session (the last session was active in the last {ACTIVE_SESSION_SECS} s, not continuing it)", fresh)]
    return out + [("continue the last session", ["claude", "-p", prompt, "--continue", *common, *extra]), ("new session", fresh)]


def wake_main(argv):
    """kurgu wake <codex|claude> <project_dir> [seq]: start the agent for the notes that were just sent (called by the on_send hook)."""
    if any(a in ("-h", "--help") for a in argv):             # `kurgu * --help` is allowed to a woken agent: help must never start an agent
        print("usage: kurgu wake <codex|claude> <project_dir> [seq]   (what the on_send hook runs: starts the agent for the notes just sent)")
        return 0
    if len(argv) < 2 or argv[0] not in ("codex", "claude"):
        print("usage: kurgu wake <codex|claude> <project_dir> [seq]", file=sys.stderr)
        return 2
    agent, d = argv[0], os.path.abspath(argv[1])
    exe = shutil.which(agent)                                  # honours PATHEXT: on Windows `codex` / `claude` are .cmd shims
    if not exe:
        print(f"kurgu wake: `{agent}` was not found on PATH", file=sys.stderr)
        return 127
    import agent_notes
    import kurgu_paths
    why = agent_notes.shim_refusal(exe, d)
    if why:
        print(f"kurgu wake: {why}", file=sys.stderr)
        import diag
        diag.refusal(d, why)
        return 1
    env = None
    if agent == "codex":                                       # command rules in a dedicated CODEX_HOME (codex_policy.py); never run without them unless the USER config says "off"
        import codex_policy
        try:
            env, msg = codex_policy.wake_env()
        except RuntimeError as e:
            code = getattr(e, "code", "policy")
            print(f"kurgu wake: codex not started ({code}): command rules could not be applied: {e}", file=sys.stderr)
            try:
                import diag
                diag.event(d, "wake", f"codex not started ({code})", "warn", ctx={"code": code})
            except Exception:
                pass
            return 78                                          # EX_CONFIG: the editor's wake status shows this reason, not just "exit 1"
        print(f"kurgu wake: {msg}", flush=True)
    rc = 1
    env = dict(os.environ if env is None else env, KURGU_WAKE="1")      # the woken run must not be recorded as the user's session
    prompt = WAKE_PROMPT.replace("{dir}", d)
    attempts = wake_attempts(agent, d, agent_notes.latest_session(d, agent))
    for n, (label, cmd) in enumerate(attempts):
        print(f"kurgu wake: {agent}, {label}" + (f" (send #{argv[2]})" if len(argv) > 2 else ""), flush=True)
        run, stdin_text = [exe] + cmd[1:], None
        if kurgu_paths.is_shim(exe) and prompt in run:        # a .cmd shim goes through cmd.exe: the prompt (quotes, <>, &) travels on stdin, never on the command line
            run.remove(prompt)
            stdin_text = prompt
        bad = kurgu_paths.shim_unsafe(exe, run)
        if bad:
            print(f"kurgu wake: not starting {os.path.basename(exe)}: an argument has characters cmd.exe would interpret ({kurgu_paths.SHELL_META}): {bad[0][:60]!r}. "
                  "Use a project path without them.", file=sys.stderr)
            return 126
        if "--bg" in cmd:                                      # prints the background id (`claude attach <id>`); returns at once
            r = subprocess.run(run, cwd=d, stdin=subprocess.DEVNULL, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            print((r.stdout or "").strip() + (("\n" + r.stderr.strip()) if r.returncode and r.stderr else ""), flush=True)
            rc = r.returncode
        else:
            rc = subprocess.run(run, cwd=d, env=env, input=stdin_text, stdin=None if stdin_text is not None else subprocess.DEVNULL, text=stdin_text is not None,
                                encoding="utf-8" if stdin_text is not None else None).returncode
        if rc == 0:
            return 0
        print(f"kurgu wake: {agent} ({label}) exited {rc}" + ("; trying the next way" if n < len(attempts) - 1 else ""), flush=True)
    return rc


def setup_wake(agent, dry, remove=False):
    """Write (or remove) `on_send` in the USER config file. Never in a project folder: a project must not be able to run commands."""
    import kurgu_paths
    path = kurgu_paths.config_file()
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, ValueError):
        cfg = {}
    old = cfg.get("on_send")
    if remove:
        cfg.pop("on_send", None)
    else:
        cfg["on_send"] = wake_command(agent)
    if old == cfg.get("on_send"):
        print(f"  = wake-up: {'no on_send set' if remove else 'on_send already set to this'} ({path})")
        return 0
    print(f"  + wake-up (on_send): {'would ' if dry else ''}{'remove from' if remove else 'write to'} {path}")
    if not remove:
        print("    command: " + " ".join(_q(c) if (" " in c or not c) else c for c in cfg["on_send"]))
        print("    (`kurgu wake` continues the folder's last " + agent + " session, or starts a new one when there is none yet; try it by hand: `kurgu wake " + agent + " <project>`)")
        if agent == "codex":
            import codex_policy
            for line in codex_policy.describe():
                print(line)
            problem = None if codex_policy.policy_off() else codex_policy.auth_problem()
            if problem:
                print(f"  ! this wake will NOT start Codex yet ({problem.code}): {problem}", file=sys.stderr)
        print("    It runs, detached, in the project folder each time you press Send; output goes to <project>/.kurgu/hook.log. It continues the agent's most recent")
        print("    session there (headless): you see the result in Kurgu as \"düzeltildi\" + the agent's reply. Remove it with: kurgu setup-agent --no-wake")
    if old is not None and not remove:
        print(f"    (replaces the previous on_send: {old})")
    if dry:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".kurgu-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return 0


def setup_agent_main(argv):
    ap = argparse.ArgumentParser(prog="kurgu setup-agent", description="Register Kurgu (MCP server + agent instructions) with your coding agent.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--codex", action="store_true", help="OpenAI Codex (CLI, IDE, desktop app share ~/.codex)")
    g.add_argument("--claude", action="store_true", help="Claude Code")
    g.add_argument("--all", action="store_true", help="both (the default)")
    ap.add_argument("--project", metavar="DIR", help="also add/update the Kurgu section in DIR/AGENTS.md (Codex reads it)")
    ap.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    ap.add_argument("--wake", action="store_true", help="also set the on_send hook so pressing Send in Kurgu starts the agent (needs --codex or --claude)")
    ap.add_argument("--no-wake", action="store_true", help="remove the on_send hook")
    a = ap.parse_args(argv)
    if a.wake and not (a.codex or a.claude):
        print("--wake needs to know which agent to start: add --codex or --claude", file=sys.stderr)
        return 2
    if a.no_wake:
        print("Dry run: nothing is written." if a.dry_run else "Kurgu wake-up")
        return setup_wake(None, a.dry_run, remove=True)
    home = os.path.expanduser("~")
    command, args = mcp_command()
    if a.project and not os.path.isdir(a.project):
        print(f"No such folder: {a.project}", file=sys.stderr)
        return 2
    rc = 0
    if a.dry_run:
        print("Dry run: nothing is written.")
    if not a.claude:
        rc |= setup_codex(home, a.project, a.dry_run, command, args)
    if not a.codex:
        rc |= setup_claude(a.dry_run, command, args)
    if a.wake:
        rc |= setup_wake("codex" if a.codex else "claude", a.dry_run)
    return 1 if rc else 0


def _duration(d):
    import json
    import schema
    try:
        with open(schema.project_path(d), encoding="utf-8") as f:
            v = json.load(f).get("duration")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None
    except (OSError, ValueError, TypeError):
        return None


def frame_parser():
    ap = argparse.ArgumentParser(prog="kurgu frame", description="Render one frame of the project (as it is on disk now) to a JPEG and print its path. "
                                 "Works with or without the editor running; no browser needed.")
    ap.add_argument("project_dir", help="project folder")
    ap.add_argument("seconds", type=float, help="timeline time in seconds")
    ap.add_argument("-o", "--output", default=None, help="JPEG path (default: <project_dir>/.kurgu/look/frame-<seconds>.jpg)")
    ap.add_argument("--full", action="store_true", help="full project resolution (slower); default is the half-size draft")
    return ap


def frame_main(argv):
    """kurgu frame <project_dir> <seconds> [-o out.jpg] [--full]: render the project as it is on disk now at that time to a JPEG, print the path."""
    a = frame_parser().parse_args(argv)
    import kurgu_paths
    import schema
    try:
        d = kurgu_paths.require_project(a.project_dir)
        out = kurgu_paths.look_output(d, a.output, f"frame-{a.seconds:g}.jpg")
    except kurgu_paths.ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.seconds < 0:
        print("error: seconds must be >= 0", file=sys.stderr)
        return 1
    dur = _duration(d)
    a.seconds, warn = kurgu_paths.clamp_time(a.seconds, dur)
    if warn:
        print("warning: " + warn, file=sys.stderr)
    import io
    import json
    import mcp_server
    from PIL import Image
    try:
        png = mcp_server.render_still(d, a.seconds, draft=not a.full)
    except mcp_server.ToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with Image.open(io.BytesIO(png)) as im:
        im = im.convert("RGB")
        im.save(out, format="JPEG", quality=88)
        w, h = im.size
    try:
        with open(schema.project_path(d), encoding="utf-8") as f:
            pw = int(json.load(f).get("width") or w)
    except (OSError, ValueError, TypeError):
        pw = w
    k = pw / w if w else 1
    print(out)
    print(f"{w}x{h} px" + (f" (project is {pw} wide: multiply image px by {k:g} to get project px)" if abs(k - 1) > 0.01 else " (= project px)"), file=sys.stderr)
    return 0


def strip_parser():
    ap = argparse.ArgumentParser(prog="kurgu strip", description="Render n evenly spaced frames from t0 to t1 (inclusive) of the project as it is on disk now "
                                 "into ONE labelled contact-sheet JPEG and print its path. Works with or without the editor running; no browser needed.")
    ap.add_argument("project_dir", help="project folder")
    ap.add_argument("t0", type=float, help="start time in seconds")
    ap.add_argument("t1", type=float, help="end time in seconds (> t0)")
    ap.add_argument("--n", type=int, default=6, help="number of frames, 2-12 (default 6)")
    ap.add_argument("-o", "--output", default=None, help="JPEG path (default: <project_dir>/.kurgu/look/strip-<t0>-<t1>.jpg)")
    ap.add_argument("--full", action="store_true", help="full project resolution (slower); default is the half-size draft")
    return ap


def strip_main(argv):
    """kurgu strip <project_dir> <t0> <t1> [--n 6] [-o out.jpg] [--full]: n frames from t0 to t1 as ONE labelled contact-sheet JPEG, print the path."""
    a = strip_parser().parse_args(argv)
    import kurgu_paths
    try:
        d = kurgu_paths.require_project(a.project_dir)
        out = kurgu_paths.look_output(d, a.output, f"strip-{a.t0:g}-{a.t1:g}.jpg")
    except kurgu_paths.ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    dur = _duration(d)
    a.t0, w0 = kurgu_paths.clamp_time(a.t0, dur)
    a.t1, w1 = kurgu_paths.clamp_time(a.t1, dur)
    for w in (w0, w1):
        if w:
            print("warning: " + w, file=sys.stderr)
    import mcp_server
    try:
        jpg, info = mcp_server.render_strip(d, a.t0, a.t1, a.n, draft=not a.full)
    except mcp_server.ToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with open(out, "wb") as f:
        f.write(jpg)
    k = info["scale"]
    print(out)
    print(f"{info['cols']}x{info['rows']} tiles of {info['tile_w']}x{info['tile_h']} px at " + ", ".join(f"{t:.2f}" for t in info["times"]) + " s"
          + (f" (multiply tile px by {k:g} to get project px)" if abs(k - 1) > 0.01 else " (= project px)"), file=sys.stderr)
    return 0


# Every subcommand, in the order `kurgu --help` shows them (tests/test_cli_help.py keeps this in step with main() and the launchers).
COMMANDS = dict(COMMAND_HELP)


def help_topic_text(topic=None):
    """`kurgu help [topic]`: the topic list, or one topic's text (guidance/topics/*.md, compiled into guidance_gen.HELP_TOPICS)."""
    topics = HELP_TOPICS
    if not topic:
        return "help topics: " + ", ".join(sorted(topics)) + "\nkurgu help <topic>   prints one. `kurgu --help` lists the commands.\n"
    if topic not in topics:
        return f"unknown help topic {topic!r}. Topics: {', '.join(sorted(topics))}\n"
    return topics[topic]


def help_text():
    w = max(len(k) for k in COMMANDS)
    return ("usage: kurgu [project_dir] [--port N] [--no-open]\n"
            "       kurgu <command> [args]\n\n"
            "With a project folder (default: the current one) Kurgu opens the editor in your browser.\n"
            "--port N      port to use (default 8765, the next free one when it is taken)\n"
            "--no-open     do not open the browser\n\n"
            "commands:\n" + "\n".join(f"  {k.ljust(w)}  {v}" for k, v in COMMANDS.items()) + "\n\n"
            "Details: README.md, or `kurgu <command> --help` where the command has options.\n"
            "Topics: `kurgu help audio` (volume, envelope for ducking, cinecode mix.json).\n")


def probe_main(argv):
    """kurgu probe <project> [layer-id | file inside the project]: duration, size, fps, streams of a media file the project uses, as JSON. Read-only; no URLs,
    nothing outside the project's own media: the safe replacement for letting a woken agent run ffprobe."""
    import argparse
    ap = argparse.ArgumentParser(prog="kurgu probe", description="Facts about a media file of the project (ffprobe): duration, size, frame rate, streams. "
                                 "Give a layer id, or a file inside the project folder. Without a target: every layer's media.")
    ap.add_argument("project_dir")
    ap.add_argument("target", nargs="?", default=None, help="layer id, or a path inside the project folder")
    a = ap.parse_args(argv)
    import json
    import kurgu_paths
    import media_tools
    try:
        d = kurgu_paths.require_project(a.project_dir)
    except kurgu_paths.ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with open(os.path.join(d, "project.json"), encoding="utf-8") as f:
        layers = [l for l in (json.load(f).get("layers") or []) if isinstance(l, dict)]
    files = {}
    for l in layers:
        if isinstance(l.get("src"), str) and l["src"] and "://" not in l["src"]:
            p = os.path.realpath(l["src"] if os.path.isabs(l["src"]) else os.path.join(d, l["src"]))
            files.setdefault(p, []).append(l.get("id"))
    if a.target is None:
        pick = list(files)
    else:
        by_id = [p for p, ids in files.items() if a.target in ids]
        if by_id:
            pick = by_id
        else:
            p = os.path.realpath(os.path.join(d, a.target))
            if not (p == d or p.startswith(d + os.sep)) or "://" in a.target or not os.path.isfile(p):
                print("error: give a layer id or a file inside the project folder (no URLs, nothing outside the project)", file=sys.stderr)
                return 1
            pick = [p]
    out = []
    import render_adaptors
    allowed = [d] + render_adaptors.trusted_roots(d)                   # the project, and folders the USER trusted for it (`kurgu import` records the film folder)
    for p in pick:
        name = os.path.relpath(p, d) if p.startswith(d + os.sep) else os.path.basename(p)
        if not any(kurgu_paths.inside(a, p) for a in allowed):          # a layer src is project-side text: its metadata is not ours to hand out
            out.append({"file": name, "layers": files.get(p, []), "skipped": "outside the project folder and the folders you trust for it"})
            continue
        try:
            j = media_tools.probe_json(p, "-show_streams")
        except Exception as e:
            out.append({"file": name, "error": type(e).__name__})
            continue
        fmt = j.get("format") or {}
        out.append({"file": name, "layers": files.get(p, []), "duration": round(float(fmt.get("duration") or 0), 3), "format": fmt.get("format_name"),
                    "size_bytes": int(fmt.get("size") or 0),
                    "streams": [{k: s.get(k) for k in ("codec_type", "codec_name", "width", "height", "r_frame_rate", "sample_rate", "channels", "duration") if s.get(k) is not None}
                                for s in j.get("streams") or []]})
    print(json.dumps(out, indent=2))
    return 0


def _help_only(args):
    """`--help` / `-h` anywhere on the line (also after `--`) means "print help for this command and do nothing else": a woken agent is allowed
    `kurgu * --help`, so `kurgu doctor --fix -- --help` must not run `doctor --fix`."""
    if not args or args[0] in ("--help", "-h", "help") or not any(a in ("--help", "-h") for a in args):
        return args
    first = args[0]
    if first == "notes" and len(args) > 2 and args[1] in ("list", "wait", "done", "ask", "add"):
        return ["notes", args[1], "--help"]
    return [first if first in COMMANDS or first == "ocr" else "--help", "--help"] if first in COMMANDS or first == "ocr" else ["--help"]


def main(argv=None):
    args = _help_only(sys.argv[1:] if argv is None else list(argv))
    if args and args[0] == "help" and len(args) >= 2 and args[1] not in ("--help", "-h"):
        print(help_topic_text(args[1]), end="")
        sys.exit(0)
    if args and args[0] in ("--help", "-h", "help"):
        print(help_text() + ("\n" + help_topic_text() if args[0] == "help" else ""), end="")
        sys.exit(0)
    if args and args[0] in ("frame", "strip"):
        for _s in (sys.stdout, sys.stderr):
            if hasattr(_s, "reconfigure"):
                _s.reconfigure(encoding="utf-8", errors="replace")
        sys.exit((frame_main if args[0] == "frame" else strip_main)(args[1:]))
    if args and args[0] in ("elements", "import"):
        for _s in (sys.stdout, sys.stderr):
            if hasattr(_s, "reconfigure"):
                _s.reconfigure(encoding="utf-8", errors="replace")
        if args[0] == "elements":
            import elements
            sys.exit(elements.main(args[1:]))
        import scene_import
        sys.exit(scene_import.main(args[1:]))
    if args and args[0] == "rerender":
        import render_adaptors
        sys.exit(render_adaptors.main(args[1:]))
    if args and args[0] == "probe":
        sys.exit(probe_main(args[1:]))
    if args and args[0] in ("detect", "ocr"):
        for _s in (sys.stdout, sys.stderr):
            if hasattr(_s, "reconfigure"):
                _s.reconfigure(encoding="utf-8", errors="replace")
        import ocr_boxes
        sys.exit(ocr_boxes.detect_main(args[1:]))
    if args and args[0] == "report":
        import diag
        sys.exit(diag.main(args[1:]))
    if args and args[0] == "doctor":
        import doctor
        sys.exit(doctor.main(args[1:]))
    if args and args[0] == "setup-agent":
        sys.exit(setup_agent_main(args[1:]))
    if args and args[0] == "wake":
        sys.exit(wake_main(args[1:]))
    if args and args[0] == "setup-ffmpeg":
        import ffmpeg_fetch
        sys.exit(ffmpeg_fetch.main(args[1:]))
    import server
    return server.main(args)   # also handles `notes`


if __name__ == "__main__":
    main()
