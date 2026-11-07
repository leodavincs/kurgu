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

# ----------------------------------------------------------------------------- agent instructions (one text, three homes)
BEGIN = "<!-- kurgu:begin (managed by `kurgu setup-agent`; edit outside this block) -->"
END = "<!-- kurgu:end -->"

AGENT_BLOCK = """\
## Kurgu (video as code)

Kurgu is a local video editor. You write `<dir>/project.json`; the human fine-tunes it in a browser and watches it. Use the `kurgu` MCP tools when you have them (`get_state`, `get_changes`, `edit_project`, `render_frame`, `notes_wait`, `notes_done`, ...); otherwise edit the JSON and run `kurgu-render`. Field reference: the Kurgu repo's `skills/kurgu/reference.md`.

**Before every edit:** `get_state` + `get_changes` (or re-read `project.json`, the tail of `changes.md`, `.kurgu/state.json`). Keep layer ids stable; keep what the human changed by hand.

**Watch + note loop** (the human drops timestamped notes, maybe with drawn marks, and clicks Send):
1. At the START of every turn, look once: MCP `notes_wait` with `timeout: 0`, or `kurgu notes wait <dir> --timeout 0` (exit 3 = nothing new).
2. Fix each note (`edit_project`, then `render_frame` to check), then `notes_done` / `kurgu notes done <dir> <id...> --reply "what you changed"`.
3. To keep waiting: MCP `notes_wait` with `timeout: 120` (3000 when the user says they are watching live), call it again whenever it times out. No MCP: `kurgu notes wait <dir> --timeout 20`, repeat on exit 3. The human sees the note picture in `NOTLAR.md`.
- Claude Code only: run `kurgu notes wait <dir>` (no timeout) as a BACKGROUND command; you are re-invoked when it exits.
- Codex / other agents: a background process cannot wake you, and shell calls may be cut or yielded after ~30 s. Do not rely on one. Use the repeated short waits above, or tell the human "send, then say 'check notes'".
"""

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


WAKE_PROMPT = ("Kurgu'dan yeni notlar geldi. `kurgu notes wait {dir} --timeout 0` ile oku, her notu düzelt, "
               "`kurgu notes done {dir} <id> --reply ...` ile kapat.")


CLAUDE_TOOLS = "Bash(kurgu notes:*),mcp__kurgu"     # no ffprobe (it can fetch URLs: `ffprobe https://x/?<data>` would leak), no general Bash, no ffmpeg


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


def wake_attempts(agent, d):
    """[(label, argv)] in the order `kurgu wake` tries them. Both run non-interactively in the project folder, never stop for a question, and print JSON so
    hook.log records token usage / cost. The first continues the folder's most recent session; the second starts a fresh one (nothing to resume yet)."""
    prompt = WAKE_PROMPT.replace("{dir}", d)
    if agent == "codex":
        cfg = ["-c", 'approval_policy="never"']
        return [("continue the last session", ["codex", "exec", "resume", "--last", "--json", "-c", 'sandbox_mode="workspace-write"', *cfg, prompt]),
                ("new session", ["codex", "exec", "-s", "workspace-write", *cfg, "--skip-git-repo-check", "-C", d, "--json", prompt])]
    # --setting-sources user: a downloaded project folder is untrusted; its .claude/settings*.json (hooks, permissions) and .mcp.json
    # servers must not load in a non-interactive run (claude -p skips the workspace trust prompt). The user's own settings still apply.
    common = ["--setting-sources", "user", "--output-format", "json", "--permission-mode", "acceptEdits", "--allowedTools", CLAUDE_TOOLS]
    return [("continue the last session", ["claude", "-p", prompt, "--continue", *common]), ("new session", ["claude", "-p", prompt, *common])]


def wake_main(argv):
    """kurgu wake <codex|claude> <project_dir> [seq]: start the agent for the notes that were just sent (called by the on_send hook)."""
    if len(argv) < 2 or argv[0] not in ("codex", "claude"):
        print("usage: kurgu wake <codex|claude> <project_dir> [seq]", file=sys.stderr)
        return 2
    agent, d = argv[0], os.path.abspath(argv[1])
    exe = shutil.which(agent)                                  # honours PATHEXT: on Windows `codex` / `claude` are .cmd shims
    if not exe:
        print(f"kurgu wake: `{agent}` was not found on PATH", file=sys.stderr)
        return 127
    import agent_notes
    why = agent_notes.shim_refusal(exe, d)
    if why:
        print(f"kurgu wake: {why}", file=sys.stderr)
        import diag
        diag.refusal(d, why)
        return 1
    rc = 1
    for n, (label, cmd) in enumerate(wake_attempts(agent, d)):
        print(f"kurgu wake: {agent}, {label}" + (f" (send #{argv[2]})" if len(argv) > 2 else ""), flush=True)
        rc = subprocess.run([exe] + cmd[1:], cwd=d, stdin=subprocess.DEVNULL).returncode
        if rc == 0:
            return 0
        print(f"kurgu wake: {agent} ({label}) exited {rc}" + ("; trying a new session" if n == 0 else ""), flush=True)
    return rc


def setup_wake(agent, dry, remove=False):
    """Write (or remove) `on_send` in the USER config file. Never in a project folder: a project must not be able to run commands."""
    import kurgu_paths
    path = os.path.join(kurgu_paths.config_dir(), "config.json")
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


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
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
