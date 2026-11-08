"""What a Codex run woken by Kurgu (`kurgu wake codex`) may run.

Codex has no `--allowedTools`. What it has: execpolicy `.rules` files (`prefix_rule(pattern=[...], decision="allow"|"prompt"|"forbidden")`), read from
`$CODEX_HOME/rules/`. They are a DENY list, not an allow list: a command that matches no rule is not refused, it runs inside the OS sandbox
(`workspace-write`: writes only in the project folder, no network). An `allow` rule is the opposite of what we want, it runs the command OUTSIDE the sandbox
(`sed -n` could then write anywhere, `rg --pre` runs a program). So the generated rules contain only `forbidden` entries, and the sandbox contains the rest.

To scope this to Kurgu's wake only (never the user's own Codex sessions), the run gets its own CODEX_HOME, `<kurgu config dir>/codex-home`:
rules/kurgu.rules (regenerated on every wake), config.toml (only the model settings copied from the user's), and auth.json as a SYMLINK to the user's
(no secret is copied). A project folder cannot switch the policy off: project `.codex/` layers load only for trusted projects, and this home trusts none;
the escape hatch `"codex_policy": "off"` is read from the USER config file only.
"""
import json
import os
import re
import shutil

import kurgu_paths

# first tokens that are refused outright. A shell wrapper around a plain command is split by Codex and every part is checked; a script it cannot split
# (redirects, $(...), env prefixes, globs) is checked as ONE ["zsh", "-lc", script] invocation, so refusing the wrappers refuses exactly those scripts.
SHELLS = ["bash", "sh", "zsh", "dash", "fish", "ksh", "csh", "tcsh", "pwsh", "powershell", "cmd"]
GROUPS = [
    ("network", "No network tools. The agent works on the project folder only.",
     ["curl", "wget", "nc", "ncat", "netcat", "socat", "telnet", "ssh", "scp", "sftp", "rsync", "ftp", "dig", "nslookup", "ping", "traceroute"]),
    ("destructive or file-writing", "Edit project.json with the patch tool; Kurgu's own commands write what else is needed.",
     ["rm", "rmdir", "unlink", "shred", "srm", "trash", "mv", "cp", "ln", "dd", "tee", "truncate", "chmod", "chown", "chgrp", "touch", "mkdir", "install",
      "ex", "vi", "vim", "nvim", "nano", "emacs", "open", "xdg-open", "kill", "killall", "pkill", "launchctl", "crontab", "sudo", "doas", "su"]),
    ("arbitrary execution", "No interpreters and no command launchers: use the dedicated Kurgu commands.",
     ["python", "python2", "python3", "pypy", "pypy3", "node", "nodejs", "deno", "bun", "ruby", "perl", "php", "lua", "osascript", "awk", "gawk", "mawk",
      "env", "xargs", "nohup", "eval", "exec", "command", "builtin", "time", "timeout", "watch", "find", "make", "cmake", "ffmpeg", "convert", "magick"]),
    ("package managers", "Nothing is installed from a wake run.",
     ["npm", "npx", "pnpm", "yarn", "pip", "pip3", "pipx", "uv", "uvx", "poetry", "conda", "brew", "apt", "apt-get", "dnf", "yum", "pacman", "snap",
      "cargo", "gem", "go", "winget", "choco", "scoop"]),
]
PREFIXES = [   # (pattern tokens, why)
    (["git", "push"], "Nothing leaves this machine."), (["git", "clone"], "No network."), (["git", "fetch"], "No network."), (["git", "pull"], "No network."),
    (["git", "remote"], "No network."), (["git", "config"], "No config changes."), (["git", "reset"], "Not from a wake run."), (["git", "clean"], "Not from a wake run."),
    (["git", "checkout"], "Not from a wake run."), (["git", "restore"], "Not from a wake run."),
    (["sed", "-i"], "sed -n reads; -i writes."), (["sed", "--in-place"], "sed -n reads; --in-place writes."),
    (["rg", "--pre"], "--pre runs a program."), (["rg", "--pre-glob"], "--pre runs a program."),
    (["kurgu", "doctor"], "Not a note-fixing command."), (["kurgu", "setup-agent"], "Changes user config."), (["kurgu", "wake"], "No recursive wake."),
    (["kurgu", "setup-ffmpeg"], "Downloads a binary."),
]
BIN_DIRS = ["/bin/", "/usr/bin/", "/usr/local/bin/", "/opt/homebrew/bin/", "/sbin/", "/usr/sbin/"]
# commands the wake prompt asks for. They match no rule on purpose (see the module docstring); the unit tests check that none of them is refused.
EXPECTED_RUNNABLE = [["kurgu", "notes", "wait", "D", "--timeout", "0"], ["kurgu", "notes", "list", "D"], ["kurgu", "notes", "done", "D", "n_1", "--reply", "x"],
                     ["kurgu", "notes", "ask", "D", "n_1", "--question", "x"], ["kurgu", "frame", "D", "1.5"], ["kurgu", "strip", "D", "0", "2"],
                     ["kurgu", "rerender", "D"], ["kurgu", "detect", "D", "1"], ["ffprobe", "-v", "error", "x.mp4"], ["cat", "NOTLAR.md"], ["ls", "-la"], ["head", "-n", "20", "NOTLAR.md"],
                     ["sed", "-n", "1,20p", "project.json"], ["grep", "-n", "x", "NOTLAR.md"], ["rg", "-n", "x"], ["jq", ".layers", "project.json"]]

HOME_KEYS = ("model", "model_reasoning_effort", "model_reasoning_summary", "model_verbosity", "service_tier", "model_provider")


def _alts(names):
    out = []
    for n in names:
        out.append(n)
        out.extend(d + n for d in BIN_DIRS)
    return out


def _q(s):
    return json.dumps(s)


def _list(items):
    return "[" + ", ".join(_q(i) for i in items) + "]"


def patterns():
    """[(pattern, why)] where pattern is a list of tokens or alternative-lists, exactly what goes into prefix_rule."""
    out = [([_alts(cmds)], why) for _, why, cmds in GROUPS]
    out.append(([_alts(SHELLS)], "Run one plain command per call; no shell -c scripts."))
    out.extend((toks, why) for toks, why in PREFIXES)
    return out


def is_forbidden(argv):
    """Python mirror of the prefix match Codex does on one command's argv (used by the tests; Codex itself decides at run time)."""
    for pat, _ in patterns():
        if len(argv) >= len(pat) and all((a in p) if isinstance(p, list) else a == p for a, p in zip(argv, pat)):
            return True
    return False


def rules_text():
    """The execpolicy rules file. Only `forbidden`; `match` / `not_match` examples are validated by Codex when it loads the file."""
    out = ["# Generated by Kurgu on every `kurgu wake codex`; do not edit (it is overwritten). Deny list: what matches no rule runs inside the workspace-write sandbox.",
           "# Counterpart of CLAUDE_TOOLS in kurgu_cli.py (Claude Code gets an allow list; Codex can only get this deny list plus its sandbox)."]
    for pat, why in patterns():
        shown = "[" + ", ".join(_list(t) if isinstance(t, list) else _q(t) for t in pat) + "]"
        out.append(f"prefix_rule(pattern={shown}, decision=\"forbidden\", justification={_q(why)})")
    out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ user switch
def user_config():
    try:
        with open(kurgu_paths.config_file(), encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def policy_off():
    """True only when the USER config file says `"codex_policy": "off"`. Never read from a project folder or the environment of a project."""
    return str(user_config().get("codex_policy", "")).strip().lower() == "off"


# ------------------------------------------------------------------ the dedicated CODEX_HOME
def real_codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def policy_home():
    return os.path.join(kurgu_paths.config_dir(), "codex-home")


def copied_settings(text):
    """Top-level `key = value` lines (before the first [table]) for the whitelisted model keys. No MCP servers, notify programs, hooks, tokens or project trust."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            break
        m = re.match(r"^([A-Za-z0-9_]+)\s*=\s*(.+)$", s)
        if m and m.group(1) in HOME_KEYS:
            out.append(f"{m.group(1)} = {m.group(2)}")
    return out


class PolicyProblem(RuntimeError):
    """The dedicated Codex home cannot be set up. `.code` is a stable word the editor / hook log can show: no_auth_file | auth_replaced | prepare_failed."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def auth_problem(real=None, home=None):
    """None when the user's Codex login can be linked into the dedicated home, else a PolicyProblem (not raised). Checked up front by `setup-agent --codex --wake`
    and again on every wake. Never changes anything."""
    real, home = real or real_codex_home(), home or policy_home()
    auth = os.path.join(real, "auth.json")
    if not os.path.isfile(auth):
        return PolicyProblem("no_auth_file", f"no {auth} (Codex may keep its login in the OS keyring, which a separate home cannot reuse). Log in with file storage "
                                             "(codex login), or set \"codex_policy\": \"off\" in the Kurgu user config to run without the command rules")
    link = os.path.join(home, "auth.json")
    if os.path.lexists(link) and not os.path.islink(link):
        try:
            same = os.path.isfile(link) and open(link, "rb").read() == open(auth, "rb").read()
        except OSError:
            same = False
        if not same:
            return PolicyProblem("auth_replaced", f"{link} is a regular file that differs from {auth}: Codex replaced the link (a token refresh), Kurgu does not overwrite it. "
                                                  "Delete that file if it is only a stale copy, then wake again")
    return None


def prepare_home(real=None, home=None):
    """(path, None) when the dedicated home is ready, else (None, PolicyProblem). Refuses rather than run unrestricted without auth it can link."""
    real, home = real or real_codex_home(), home or policy_home()
    problem = auth_problem(real, home)
    if problem:
        return None, problem
    auth = os.path.join(real, "auth.json")
    try:
        os.makedirs(os.path.join(home, "rules"), exist_ok=True)
        try:
            text = open(os.path.join(real, "config.toml"), encoding="utf-8").read()
        except OSError:
            text = ""
        _write(os.path.join(home, "config.toml"), "# Generated by Kurgu for woken Codex runs.\n" + "\n".join(copied_settings(text)) + "\n")
        _write(os.path.join(home, "rules", "kurgu.rules"), rules_text())
        link_sessions(real, home)
        link = os.path.join(home, "auth.json")
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(auth):
            return home, None                              # already right: touch nothing
        if os.path.islink(link):
            os.remove(link)                                # a link to somewhere else: re-point it (a regular file was handled above and is never removed)
        if not os.path.lexists(link):
            try:
                os.symlink(auth, link)
            except (OSError, NotImplementedError):          # Windows without the symlink right: a copy is the only way, kept in the user's own config folder
                shutil.copyfile(auth, link)
                os.chmod(link, 0o600)
    except OSError as e:
        return None, PolicyProblem("prepare_failed", f"cannot prepare {home}: {e}")
    return home, None


def link_sessions(real, home):
    """`sessions` in the dedicated home is a symlink to the user's, so `codex exec resume <uuid>` / `--last` find the user's own threads (and new wake threads land
    where the user's Codex lists them). Returns False when it cannot (Windows without the symlink right): exact-session resume is then unavailable and the wake
    falls back to a new session; nothing is copied."""
    target, link = os.path.join(real, "sessions"), os.path.join(home, "sessions")
    try:
        os.makedirs(target, exist_ok=True)
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(target):
            return True
        if os.path.islink(link):
            os.remove(link)
        elif os.path.isdir(link):
            if os.listdir(link):
                return False                             # an older real folder with threads in it: leave it alone
            os.rmdir(link)
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def _write(path, text):
    try:
        if open(path, encoding="utf-8").read() == text:
            return
    except OSError:
        pass
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def wake_env(base=None):
    """(env, message) for the codex child process. env is None when the policy is off (caller runs Codex unchanged); message is a line to print.
    Raises RuntimeError when the policy is on but cannot be applied: a wake never silently runs without it."""
    if policy_off():
        return None, "codex_policy is off in the user config: no command rules (sandbox and approval_policy=never still apply)"
    home, err = prepare_home()
    if err:
        raise err
    env = dict(os.environ if base is None else base)
    env["CODEX_HOME"] = home
    return env, f"codex command rules on (CODEX_HOME={home})"


def describe():
    """Lines for `kurgu setup-agent --codex --wake` (also under --dry-run)."""
    return ["    Command rules for the woken Codex: it runs with its own CODEX_HOME (" + policy_home() + ") holding rules/kurgu.rules, the model settings and a link to your login;",
            "    your own ~/.codex and your other Codex sessions are untouched. Forbidden: " + ", ".join(n for n, _, _ in GROUPS) + ", shell -c scripts, git push/clone/fetch/pull,",
            "    sed -i, rg --pre. Everything else (kurgu notes/frame/strip/rerender, ffprobe, cat, ls, head, sed -n, grep, rg, jq) runs inside the workspace-write sandbox",
            "    (writes only in the project folder, no network). File edits through Codex's patch tool stay allowed. Switch off: \"codex_policy\": \"off\" in the user config file."]
