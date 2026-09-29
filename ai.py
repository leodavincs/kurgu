"""Kurgu "Ask AI": runs the user's own coding-agent CLI headless in the project directory (no API key of our own).

Used by server.py (POST /api/ai, GET /api/ai/<id>?since=n, POST /api/ai/<id>/cancel).

Decisions:
- Default agent is Claude Code (`claude -p ... --output-format stream-json --verbose`), with only Kurgu's MCP tools allowed
  and every built-in tool switched off. Fallback: `codex exec --json` (flags verified against codex-cli 0.149 `--help`,
  but the event parser is UNTESTED against a real Codex run).
- Override: env KURGU_AGENT_CMD, or the USER-level config (~/.config/kurgu/config.json, XDG_CONFIG_HOME honoured; %APPDATA%\\kurgu on
  Windows) {"agent_cmd": "..."}. The project's `.kurgu/config.json` may only hold "agent" ("claude"|"codex"), "ai_timeout",
  "brief_timeout": a project folder is untrusted input and never chooses a command to run.
  A custom command is split like a shell line; `{prompt}`, `{brief}`, `{mcp_config}` are substituted (the prompt is appended
  as the last argument when it does not appear). It must print Claude-style stream-json (or Codex --json when its
  executable is named codex). The child also gets KURGU_PROJECT_DIR, KURGU_AI_PROMPT, KURGU_AI_BRIEF, KURGU_AI_MCP_CONFIG.
- Events are language-neutral: {"kind", "key", "params", "text"?}. The browser translates `key` (i18n "ai.ev.*"); `text` is
  only set for kind "text" (the agent's own words, already in the user's language).
- Conversation threads: sessions are persisted and follow-ups run `--resume <session_id>` (Claude; Codex `exec resume`, untested). Claude runs
  in `.kurgu/ai-work/` so its sessions stay apart from the user's terminal ones. Thread store: `.kurgu/ai-threads.json`.
- brief.md: the creative brief, injected into the system prompt; `brief_command` summarises the terminal conversation (Claude only).
- One job at a time per project; 5 minute timeout (env KURGU_AI_TIMEOUT overrides); the whole process group is killed.
"""
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import kurgu_paths

HERE = os.path.dirname(os.path.realpath(__file__))
MCP_TOOLS = ["get_state", "get_project", "edit_project", "render_frame", "list_fonts", "list_media", "get_changes"]
ALLOWED = ",".join("mcp__kurgu__" + t for t in MCP_TOOLS)
DEFAULT_TIMEOUT = 300
BRIEF_MAX = 2000
BRIEF_TIMEOUT = 90
LANG_NAMES = {"tr": "Turkish", "en": "English"}

INSTALL_HINT = ("No coding agent found. Install Claude Code (https://claude.com/claude-code, then run `claude` once to sign in) "
                "or Codex CLI, or set KURGU_AGENT_CMD.")


class AiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


PROJECT_CONFIG_KEYS = ("agent", "ai_timeout", "brief_timeout")   # harmless; command settings are user-level only
_warned = set()


def user_config_path():
    return os.path.join(kurgu_paths.config_dir(), "config.json")


def _load_json_dict(path):
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def read_config(kurgu_dir):
    """User-level config (agent_cmd, brief_cmd, ...) overlaid with whitelisted keys from the project's .kurgu/config.json.
    A project folder can come from someone else, so it must never choose a command to execute: agent_cmd / brief_cmd
    there are ignored (warned about once)."""
    cfg = _load_json_dict(user_config_path())
    proj = _load_json_dict(os.path.join(kurgu_dir, "config.json"))
    ignored = sorted(k for k in proj if k not in PROJECT_CONFIG_KEYS)
    if ignored and (kurgu_dir, tuple(ignored)) not in _warned:
        _warned.add((kurgu_dir, tuple(ignored)))
        print(f"kurgu: ignoring {', '.join(ignored)} in {os.path.join(kurgu_dir, 'config.json')}: commands are only read from "
              f"KURGU_AGENT_CMD or {user_config_path()}", file=sys.stderr)
    cfg.update({k: v for k, v in proj.items() if k in PROJECT_CONFIG_KEYS})
    return cfg


def read_brief(project_dir):
    """The creative brief (brief.md in the project dir) or ''."""
    try:
        with open(os.path.join(project_dir, "brief.md"), encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return ""


def brief(scope, lang, project_dir):
    """The system-prompt addition. Kept short: the model gets the field reference through the MCP tools and the request."""
    sel = scope.get("selected") or []
    t = scope.get("time")
    where = ("The request applies to the selected layers: " + ", ".join(sel) + "." if sel
             else "No layer is selected: the request is about the moment at the playhead - the frame the user is looking at. "
                  "Change only what is on screen around that moment (the layers active there, or a short new layer/effect starting "
                  "near it). Do NOT apply the change to the whole video.")
    at = f" The playhead is at t = {t:.2f} s." if isinstance(t, (int, float)) else ""
    lang_name = LANG_NAMES.get(lang, lang or "English")
    ref = os.path.join(kurgu_paths.data_root(), "skills", "kurgu", "reference.md")
    pb = read_brief(project_dir)[:BRIEF_MAX]
    pb_text = ("\n\nProject brief (from the user's conversation). It is context about what the video is and the rules the user "
               "set; follow it unless the request clearly overrides it:\n" + pb + "\n\n") if pb else ""
    return (
        "You are editing a video in Kurgu, a timeline editor whose project is project.json, through the kurgu MCP tools "
        "(get_state, get_project, edit_project, render_frame, list_fonts, list_media, get_changes). "
        f"{where}{at} Unless the request says otherwise, apply it there (\"here\" means the playhead time). "
        "The user is not a programmer; they describe the look they want in plain words. Build it from what the format supports: "
        "layer effects (grayscale, brightness, contrast, saturation, blur, zoom push-in, freeze), fade_in/fade_out, opacity, "
        "x/y/scale, crop, fit, text layers (font, size, colour, shadow, letter spacing), colour layers (overlays, flashes, "
        "colour washes; combine with low opacity and fades), sound volume_db and envelope, project-level grain and vignette, "
        "and duplicating layers with start/end offsets and different opacity/scale/position to fake flicker, shake, echo, "
        "strobing or stepped animation. Keep existing ids; give new layers short ids and readable names. "
        f"Field reference: {ref} (read it only if you need details). {pb_text}"
        "Work like this: call get_state and get_project (summary) once, make ONE edit_project batch with a short human "
        f"`summary` written in {lang_name}, then render_frame at the relevant time, look at it, and fix once with a second "
        "edit_project only if it is clearly wrong. Do not render the video, do not touch files. Respect what the user "
        "did by hand (get_changes). Finish with 1-2 sentences in "
        f"{lang_name} saying what you did. If the format cannot do what was asked, say so honestly, then say what you did "
        "instead as the closest thing (still make that edit). Never answer with code or JSON."
    )


def mcp_config_file(project_dir):
    cfg = {"mcpServers": {"kurgu": {"command": sys.executable,
                                    "args": [os.path.join(HERE, "mcp_server.py")],
                                    "env": {"KURGU_PROJECT_DIR": project_dir}}}}
    fd, path = tempfile.mkstemp(prefix="kurgu-mcp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


def with_history(prompt, history):
    """Continuity without a resumable session: the earlier turns go into the prompt as plain text."""
    if not history:
        return prompt
    lines = []
    for m in history[-8:]:
        who = "User" if m.get("role") == "user" else "You"
        if m.get("text"):
            lines.append(f"{who}: {str(m['text'])[:600]}" + (" (the user undid this)" if m.get("undone") else ""))
    if not lines:
        return prompt
    return "Earlier in this conversation about the same spot:\n" + "\n".join(lines) + "\n\nNew message from the user:\n" + prompt


def build_command(cfg, prompt, brief_text, mcp_path, project_dir, resume=None, history=None):
    """Returns (argv, format) where format is 'claude' or 'codex'. Raises AiError(424) when no agent is available.
    `resume`: an agent session id to continue (Claude `--resume`, Codex `exec resume`); custom commands cannot resume, they get
    `history` (earlier turns) inside the prompt instead, and the session id in $KURGU_AI_SESSION."""
    custom = os.environ.get("KURGU_AGENT_CMD") or cfg.get("agent_cmd")
    if custom:
        parts = shlex.split(custom)
        if not parts or not shutil.which(parts[0]):
            raise AiError(424, f"agent command not found: {parts[0] if parts else '(empty)'}. {INSTALL_HINT}")
        prompt = with_history(prompt, history)
        subs = {"{prompt}": prompt, "{brief}": brief_text, "{mcp_config}": mcp_path}
        used = any(k in p for p in parts for k in subs)
        argv = []
        for p in parts:
            for k, v in subs.items():
                p = p.replace(k, v)
            argv.append(p)
        if not used:
            argv.append(prompt)
        fmt = "codex" if os.path.basename(parts[0]) == "codex" else "claude"
        return argv, fmt
    want = os.environ.get("KURGU_AGENT") or cfg.get("agent")
    order = [want] if want in ("claude", "codex") else ["claude", "codex"]
    for name in order:
        exe = shutil.which(name)
        if not exe:
            continue
        if name == "claude":
            # Sessions are persisted (no --no-session-persistence) so a follow-up can `--resume` them. Tools, MCP config and the
            # system prompt are per-invocation flags: re-passed on every turn (the brief also carries the current scope).
            argv = [exe, "-p", prompt if resume else with_history(prompt, history), "--output-format", "stream-json", "--verbose",
                    "--mcp-config", mcp_path, "--strict-mcp-config", "--allowedTools", ALLOWED, "--tools", "",
                    "--setting-sources", "project",  # skip the user's own hooks/plugins/language settings
                    "--append-system-prompt", brief_text]
            if resume:
                argv += ["--resume", resume]
            return argv, "claude"
        # UNTESTED end to end: flags checked against `codex exec --help` / `codex exec resume --help` (0.149).
        srv = json.dumps(os.path.join(HERE, "mcp_server.py"))
        cfgs = ["-c", "mcp_servers.kurgu.command=" + json.dumps(sys.executable),
                "-c", "mcp_servers.kurgu.args=[" + srv + "]",
                "-c", "mcp_servers.kurgu.env={KURGU_PROJECT_DIR=" + json.dumps(project_dir) + "}"]
        if resume:   # `exec resume` has no --sandbox / -C: the sandbox goes through -c, the directory is the process cwd
            return [exe, "exec", "resume", "--json", "--skip-git-repo-check", "-c", 'sandbox_mode="workspace-write"'] + cfgs + \
                   [resume, brief_text + "\n\nUser request:\n" + prompt], "codex"
        return [exe, "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write", "-C", project_dir] + cfgs + \
               [brief_text + "\n\nUser request:\n" + with_history(prompt, history)], "codex"
    raise AiError(424, INSTALL_HINT)


# ---------------------------------------------------------------- stream parsing
def _short(name):
    return name.split("__")[-1] if name else ""


def tool_event(name, inp):
    name = _short(name)
    inp = inp if isinstance(inp, dict) else {}
    if name in ("get_state", "get_project", "get_changes", "list_media"):
        return {"kind": "tool", "key": "ai.ev.read", "params": {}}
    if name == "list_fonts":
        return {"kind": "tool", "key": "ai.ev.fonts", "params": {}}
    if name == "edit_project":
        n = sum(len(inp.get(k) or []) for k in ("update", "add", "remove") if isinstance(inp.get(k), list))
        return {"kind": "tool", "key": "ai.ev.edit", "params": {"n": n}}
    if name == "render_frame":
        t = inp.get("t")
        return {"kind": "tool", "key": "ai.ev.look", "params": {"t": round(float(t), 1) if isinstance(t, (int, float)) else 0}}
    return {"kind": "tool", "key": "ai.ev.working", "params": {}}


def parse_claude(obj, job):
    """One stream-json object -> zero or more events; may set job final/error."""
    out = []
    typ = obj.get("type")
    if isinstance(obj.get("session_id"), str) and obj["session_id"]:
        job["session"] = obj["session_id"]
    if typ == "assistant":
        for c in (obj.get("message") or {}).get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "thinking":
                out.append({"kind": "thinking", "key": "ai.ev.thinking", "params": {}})
            elif c.get("type") == "tool_use":
                out.append(tool_event(c.get("name"), c.get("input")))
            elif c.get("type") == "text" and (c.get("text") or "").strip():
                out.append({"kind": "text", "key": "ai.ev.text", "params": {}, "text": c["text"].strip()})
    elif typ == "result":
        job["result"] = obj
    return out


def parse_codex(obj, job):
    out = []
    typ = obj.get("type")
    item = obj.get("item") or {}
    it = item.get("type")
    if typ == "thread.started" and isinstance(obj.get("thread_id"), str):
        job["session"] = obj["thread_id"]
    if typ == "item.started" and it == "mcp_tool_call":
        out.append(tool_event(item.get("tool"), item.get("arguments")))
    elif typ == "item.completed" and it == "reasoning":
        out.append({"kind": "thinking", "key": "ai.ev.thinking", "params": {}})
    elif typ == "item.completed" and it == "agent_message" and (item.get("text") or "").strip():
        out.append({"kind": "text", "key": "ai.ev.text", "params": {}, "text": item["text"].strip()})
        job["codex_last"] = item["text"].strip()
    elif typ == "turn.failed" or typ == "error":
        job["result"] = {"is_error": True, "result": str((obj.get("error") or {}).get("message") or obj.get("message") or "")}
    return out


def _error_code(text):
    low = (text or "").lower()
    if "not logged in" in low or "/login" in low or "invalid api key" in low or "authentication" in low:
        return "login"
    if "rate limit" in low or "usage limit" in low or "limit reached" in low:
        return "limit"
    return "failed"


# ---------------------------------------------------------------- brief generation
def brief_prompt(lang):
    lang_name = LANG_NAMES.get(lang, lang or "English")
    return (
        "Write the creative brief of the video being made in this conversation. Output ONLY the brief, in Markdown, written in "
        f"{lang_name}, at most 250 words, as short bullets under these labels: what the video is; audience and platform; tone and "
        "style; hard rules (e.g. colours to avoid, real footage only); open decisions. Include only facts the user actually stated "
        "about THIS video; do not invent or suggest anything. Open decisions are only questions the user explicitly left open. "
        "Leave out a label with nothing to say, and write no title. No secrets, file paths, keys or "
        "personal data. No preamble, no code fences. If the conversation says nothing about a video, output exactly: NO_BRIEF")


def claude_sessions_dir(project_dir):
    """Where Claude Code keeps this folder's conversations (~/.claude/projects/<path with every non-alphanumeric as '-'>)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects", re.sub(r"[^a-zA-Z0-9]", "-", os.path.realpath(project_dir)))


def has_session(project_dir):
    """`claude -p --continue` does NOT fail when there is nothing to continue (it just starts a blank chat), so check first."""
    d = claude_sessions_dir(project_dir)
    try:
        return any(x.endswith(".jsonl") and os.path.getsize(os.path.join(d, x)) > 0 for x in os.listdir(d))
    except OSError:
        return False


def brief_command(cfg, lang, project_dir):
    """argv for the summarise run. Raises AiError('unsupported'|'no_agent')."""
    prompt = brief_prompt(lang)
    custom = os.environ.get("KURGU_BRIEF_CMD") or cfg.get("brief_cmd")
    if custom:
        parts = shlex.split(custom)
        if not parts or not shutil.which(parts[0]):
            raise AiError("no_agent", f"brief command not found: {parts[0] if parts else '(empty)'}")
        return [p.replace("{prompt}", prompt) for p in parts] + ([] if any("{prompt}" in p for p in parts) else [prompt])
    agent = os.environ.get("KURGU_AGENT_CMD") or cfg.get("agent_cmd")
    if agent:
        parts = shlex.split(agent)
        name = os.path.basename(parts[0]) if parts else ""
    else:
        want = os.environ.get("KURGU_AGENT") or cfg.get("agent")
        name = want if want in ("claude", "codex") else ("claude" if shutil.which("claude") else "codex" if shutil.which("codex") else "")
    if name == "codex":
        raise AiError("unsupported", "summarising the terminal conversation needs Claude Code")
    exe = shutil.which("claude")
    if not exe:
        raise AiError("no_agent", INSTALL_HINT)
    if not has_session(project_dir):
        raise AiError("no_session", "no Claude Code conversation found in this folder")
    # --continue --fork-session: read the latest conversation of this folder in a NEW session; the user's live one is untouched.
    return [exe, "-p", prompt, "--continue", "--fork-session", "--model", "haiku", "--tools", "",
            "--output-format", "json", "--no-session-persistence", "--setting-sources", "project"]


def clean_brief(text):
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", text).strip()
    if len(text) > BRIEF_MAX:
        text = text[:BRIEF_MAX].rsplit("\n", 1)[0].strip() or text[:BRIEF_MAX]
    return text


# ---------------------------------------------------------------- conversation threads
MAX_THREADS, MAX_MESSAGES = 50, 40          # 40 messages = the last 20 turns (user + agent)
MOMENT_SAME, MOMENT_REGION = 1.0, 5.0        # seconds: same moment / "nearby" region for the earlier-conversation chips


def scope_key(sel, t):
    """Layers: their sorted ids. No selection: the moment at the playhead when the conversation started."""
    sel = sorted(str(x) for x in sel)
    return (",".join(sel), "layers", None) if sel else ("moment", "moment", t if t is not None else 0.0)


class Threads:
    """`.kurgu/ai-threads.json`: {"threads": [{id, key, kind, t, session, created, updated, messages: [...]}]}."""

    def __init__(self, app):
        self.app, self.lock = app, threading.RLock()

    @property
    def path(self):
        return os.path.join(self.app.kurgu, "ai-threads.json")

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            ts = d.get("threads") if isinstance(d, dict) else None
            return [x for x in ts if isinstance(x, dict) and x.get("id")] if isinstance(ts, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, ts):
        ts = sorted(ts, key=lambda x: x.get("updated", 0))[-MAX_THREADS:]
        os.makedirs(self.app.kurgu, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"threads": ts}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def get(self, tid):
        with self.lock:
            return next((x for x in self._load() if x["id"] == tid), None)

    def create(self, sel, t):
        with self.lock:
            key, kind, t0 = scope_key(sel, t)
            ts = self._load()
            # a fresh empty conversation replaces older empty ones of the same scope
            ts = [x for x in ts if not (x.get("key") == key and not x.get("messages") and x.get("kind") == kind
                                        and (kind == "layers" or abs((x.get("t") or 0) - (t0 or 0)) <= MOMENT_SAME))]
            th = {"id": uuid.uuid4().hex[:10], "key": key, "kind": kind, "t": t0, "session": None,
                  "created": time.time(), "updated": time.time(), "messages": []}
            ts.append(th)
            self._save(ts)
            return th

    def add_message(self, tid, msg, session=None):
        with self.lock:
            ts = self._load()
            th = next((x for x in ts if x["id"] == tid), None)
            if not th:
                return None
            msg = dict(msg, id=uuid.uuid4().hex[:8], ts=time.time(), undone=False)
            th["messages"] = (th.get("messages") or []) + [msg]
            th["messages"] = th["messages"][-MAX_MESSAGES:]
            if session:
                th["session"] = session
            th["updated"] = time.time()
            self._save(ts)
            return msg

    def mark(self, tid, mid, undone):
        with self.lock:
            ts = self._load()
            th = next((x for x in ts if x["id"] == tid), None)
            m = next((x for x in (th or {}).get("messages", []) if x.get("id") == mid), None)
            if not m:
                return None
            m["undone"] = bool(undone)
            self._save(ts)
            return m

    def find(self, sel, t):
        """(active, earlier): the newest conversation that still matches the scope, and the other nearby ones (summaries)."""
        key, kind, _ = scope_key(sel, t)
        with self.lock:
            ts = [x for x in self._load() if x.get("kind") == kind]
        if kind == "layers":
            same = [x for x in ts if x.get("key") == key]
            region = same
        else:
            d = lambda x: abs((x.get("t") or 0) - (t or 0))
            same = [x for x in ts if d(x) <= MOMENT_SAME]
            region = [x for x in ts if d(x) <= MOMENT_REGION]
        same.sort(key=lambda x: x.get("updated", 0))
        active = same[-1] if same else None
        earlier = [x for x in sorted(region, key=lambda x: -x.get("updated", 0)) if not active or x["id"] != active["id"]
                   if x.get("messages")]
        return active, [self.summary(x) for x in earlier[:8]]

    @staticmethod
    def summary(th):
        first = next((m.get("text") for m in th.get("messages", []) if m.get("role") == "user"), "")
        return {"id": th["id"], "key": th.get("key"), "kind": th.get("kind"), "t": th.get("t"), "updated": th.get("updated"),
                "turns": sum(1 for m in th.get("messages", []) if m.get("role") == "user"), "first": str(first)[:80]}


# ---------------------------------------------------------------- jobs
class Ai:
    """Per-project manager (one instance on the server App)."""

    def __init__(self, app):
        self.app = app
        self.jobs = {}
        self.lock = threading.Lock()
        self.bjob = None       # latest brief-generation job
        self.threads = Threads(app)

    def start(self, body):
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise AiError(400, "prompt is required")
        if len(prompt) > 4000:
            raise AiError(400, "prompt is too long")
        scope = body.get("scope") if isinstance(body.get("scope"), dict) else {}
        sel = [str(x) for x in (scope.get("selected") or []) if isinstance(x, (str, int))][:50]
        try:
            t = float(scope.get("time")) if scope.get("time") is not None else None
        except (TypeError, ValueError):
            t = None
        scope = {"selected": sel, "time": t}
        lang = str(body.get("lang") or "en")[:8]
        display = str(body.get("display") or prompt).strip()[:4000]
        cfg = read_config(self.app.kurgu)
        custom = bool(os.environ.get("KURGU_AGENT_CMD") or cfg.get("agent_cmd"))
        with self.lock:
            if any(j["status"] == "running" for j in self.jobs.values()):
                raise AiError(409, "an AI job is already running")
            th = self.threads.get(str(body.get("thread") or "")) if body.get("thread") else None
            if body.get("thread") and not th:
                raise AiError(404, "unknown conversation")
            if not th:
                th = self.threads.create(sel, t)
            history = [m for m in th.get("messages", []) if m.get("text")]
            resume = th.get("session")
            brief_text = brief(scope, lang, self.app.dir)
            mcp_path = mcp_config_file(self.app.dir)
            # Claude runs in a sub-folder so its persisted sessions never mix with the user's own terminal conversations
            # (`claude --continue`, and the brief summary, look at the project folder's sessions).
            cwd = self.app.dir
            if not custom:
                cwd = self.app.sub("ai-work")

            def launch(resume_id):
                argv, fmt = build_command(cfg, prompt, brief_text, mcp_path, self.app.dir, resume=resume_id,
                                          history=None if resume_id else history)
                env = dict(os.environ, KURGU_PROJECT_DIR=self.app.dir, KURGU_AI_PROMPT=prompt, KURGU_AI_BRIEF=brief_text,
                           KURGU_AI_MCP_CONFIG=mcp_path, KURGU_AI_SCOPE=json.dumps(scope), KURGU_AI_LANG=lang,
                           KURGU_AI_SESSION=resume_id or "", KURGU_AI_THREAD=th["id"])
                # a resumed Codex run has no -C flag: it works in the process cwd, which must be the project
                run_cwd = self.app.dir if fmt == "codex" else cwd
                proc = subprocess.Popen(argv, cwd=run_cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                        start_new_session=(os.name != "nt"))
                return proc, fmt

            try:
                # only agents that can resume get a session id; custom commands just get the history in the prompt
                proc, fmt = launch(None if custom else resume)
            except AiError:
                os.remove(mcp_path)
                raise
            except OSError as e:
                os.remove(mcp_path)
                raise AiError(424, f"could not start the agent: {e}. {INSTALL_HINT}")
            jid = uuid.uuid4().hex[:12]
            try:
                timeout = float(os.environ.get("KURGU_AI_TIMEOUT") or cfg.get("ai_timeout") or DEFAULT_TIMEOUT)
            except (TypeError, ValueError):
                timeout = DEFAULT_TIMEOUT
            um = self.threads.add_message(th["id"], {"role": "user", "text": display, "retry": bool(body.get("retry")),
                                                     "scope": {"selected": sel, "time": t}})
            job = {"id": jid, "status": "running", "events": [], "final": None, "error": None, "code": None,
                   "changed": False, "proc": proc, "fmt": fmt, "mcp": mcp_path, "cancelled": False, "timed_out": False,
                   "version0": self.app.version(), "started": time.time(), "elapsed": None, "stderr": [], "result": None,
                   "lock": threading.Lock(), "thread": th["id"], "user_msg": um["id"] if um else None, "agent_msg": None,
                   "session": None, "resumed": bool(resume) and not custom, "relaunch": lambda: launch(None), "relaunched": False,
                   "resumed_from": resume}
            self.jobs[jid] = job
            for old in [k for k in self.jobs if k != jid and self.jobs[k]["status"] != "running"][:-5]:
                self.jobs.pop(old, None)
        threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        threading.Thread(target=self._drain_err, args=(job, proc), daemon=True).start()
        threading.Thread(target=self._watchdog, args=(job, timeout), daemon=True).start()
        return {"id": jid, "status": "running", "thread": th["id"], "user_msg": job["user_msg"], "resumed": job["resumed"]}

    def _add(self, job, ev):
        with job["lock"]:
            ev["i"] = len(job["events"])
            job["events"].append(ev)

    def _pump(self, job):
        while True:
            parse = parse_codex if job["fmt"] == "codex" else parse_claude
            try:
                for line in job["proc"].stdout:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(obj, dict):
                        for ev in parse(obj, job):
                            self._add(job, ev)
            except Exception as e:  # never let the reader die silently
                job["stderr"].append(f"reader: {e}")
            code = job["proc"].wait()
            lost = job["resumed"] and not job["relaunched"] and not job["cancelled"] and "no conversation found" in (
                json.dumps((job.get("result") or {}).get("errors") or "") + " ".join(job["stderr"])).lower()
            if not lost:
                break
            # the agent forgot the session (cleaned up / other machine): start a new one, earlier turns go in as plain text
            job.update(relaunched=True, resumed=False, result=None, stderr=[], session=None)
            try:
                job["proc"], job["fmt"] = job["relaunch"]()
            except (AiError, OSError) as e:
                job["stderr"].append(str(getattr(e, "message", e)))
                break
            threading.Thread(target=self._drain_err, args=(job, job["proc"]), daemon=True).start()
        self._finish(job, code)

    def _drain_err(self, job, proc):
        try:
            for line in proc.stderr:
                job["stderr"] = (job["stderr"] + [line.strip()])[-8:]
        except Exception:
            pass

    def _watchdog(self, job, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if job["status"] != "running":
                return
            time.sleep(0.1)
        if job["status"] == "running":
            job["timed_out"] = True
            self._kill(job)

    @staticmethod
    def _kill(job):
        p = job["proc"]
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            else:
                p.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                else:
                    p.kill()
            except (ProcessLookupError, PermissionError, OSError):
                pass

    def _finish(self, job, code):
        res = job.get("result") or {}
        text = res.get("result") if isinstance(res.get("result"), str) else None
        if job["fmt"] == "codex" and not text:
            text = job.get("codex_last")
        status, err, ecode = "done", None, None
        if job["cancelled"]:
            status = "cancelled"
        elif job["timed_out"]:
            status, err, ecode = "error", "timed out", "timeout"
        elif res.get("is_error") or (code != 0 and not text):
            tail = " | ".join(x for x in job["stderr"] if x)[-400:]
            err = (text or tail or f"agent exited with code {code}")
            status, ecode = "error", _error_code(err)
        elif code != 0:
            status, err, ecode = "error", (" | ".join(x for x in job["stderr"] if x)[-400:] or f"agent exited with code {code}"), "failed"
        try:
            job["changed"] = self.app.version() != job["version0"]
        except OSError:
            pass
        try:
            os.remove(job["mcp"])
        except OSError:
            pass
        job["final"] = text if status == "done" else (text if status == "cancelled" else None)
        job["error"], job["code"] = err, ecode
        job["elapsed"] = round(time.time() - job["started"], 1)
        # the thread remembers the turn; only a run that really produced a session keeps (or replaces) the session id
        am = self.threads.add_message(job["thread"], {"role": "agent", "text": job["final"] or "", "status": status, "code": ecode,
                                                      "changed": job["changed"], "elapsed": job["elapsed"]},
                                      session=job.get("session") if status == "done" else None)
        job["agent_msg"] = am["id"] if am else None
        job["status"] = status

    # ---------------------------------------------------------------- API
    def poll(self, jid, since=0):
        job = self.jobs.get(jid)
        if not job:
            raise AiError(404, "unknown AI job")
        with job["lock"]:
            events = [{k: v for k, v in e.items()} for e in job["events"][max(0, since):]]
        out = {"id": jid, "status": job["status"], "events": events, "next": max(0, since) + len(events), "thread": job["thread"]}
        if job["status"] != "running":
            out.update(message=job["agent_msg"], session=bool(job.get("session")),
                       final=job["final"], error=job["error"], code=job["code"], changed=job["changed"], elapsed=job["elapsed"])
        return out

    # ---------------------------------------------------------------- conversation threads
    def threads_for(self, sel, t):
        active, earlier = self.threads.find(sel, t)
        return {"active": active, "earlier": earlier}

    def thread_new(self, body):
        scope = body.get("scope") if isinstance(body.get("scope"), dict) else {}
        try:
            t = float(scope.get("time")) if scope.get("time") is not None else None
        except (TypeError, ValueError):
            t = None
        return self.threads.create([str(x) for x in (scope.get("selected") or [])][:50], t)

    def thread_get(self, tid):
        th = self.threads.get(tid)
        if not th:
            raise AiError(404, "unknown conversation")
        return th

    def thread_mark(self, tid, body):
        m = self.threads.mark(tid, str(body.get("message") or ""), body.get("undone"))
        if not m:
            raise AiError(404, "unknown message")
        return m

    def cancel(self, jid):
        job = self.jobs.get(jid)
        if not job:
            raise AiError(404, "unknown AI job")
        if job["status"] == "running":
            job["cancelled"] = True
            self._kill(job)
            for _ in range(50):  # let the pump thread finish the bookkeeping
                if job["status"] != "running":
                    break
                time.sleep(0.05)
        return self.poll(jid, 10 ** 9)

    # ---------------------------------------------------------------- brief generation (one at a time)
    def brief_snapshot(self):
        j = self.bjob
        if not j:
            return {"status": "idle"}
        out = {"status": j["status"]}
        if j["status"] != "running":
            out.update(code=j["code"], error=j["error"], brief=j["brief"])
        return out

    def brief_start(self, lang):
        cfg = read_config(self.app.kurgu)
        with self.lock:
            if self.bjob and self.bjob["status"] == "running":
                raise AiError(409, "a brief summary is already running")
            job = {"status": "running", "code": None, "error": None, "brief": None, "proc": None, "cancelled": False,
                   "timed_out": False, "stderr": []}
            self.bjob = job
            try:
                argv = brief_command(cfg, str(lang or "en")[:8], self.app.dir)
                env = dict(os.environ, KURGU_PROJECT_DIR=self.app.dir, KURGU_BRIEF_LANG=str(lang or "en")[:8])
                job["proc"] = subprocess.Popen(argv, cwd=self.app.dir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                               start_new_session=(os.name != "nt"))
            except AiError as e:
                job.update(status="error", code=e.code, error=e.message)
                return self.brief_snapshot()
            except OSError as e:
                job.update(status="error", code="no_agent", error=f"could not start the agent: {e}")
                return self.brief_snapshot()
        try:
            timeout = float(os.environ.get("KURGU_BRIEF_TIMEOUT") or cfg.get("brief_timeout") or BRIEF_TIMEOUT)
        except (TypeError, ValueError):
            timeout = BRIEF_TIMEOUT
        threading.Thread(target=self._brief_run, args=(job, timeout), daemon=True).start()
        return self.brief_snapshot()

    def _brief_run(self, job, timeout):
        p = job["proc"]
        err = []
        threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True).start()
        timer = threading.Timer(timeout, lambda: (job.__setitem__("timed_out", True), self._kill(job)))
        timer.start()
        try:
            out = p.stdout.read()
            code = p.wait()
        finally:
            timer.cancel()
        time.sleep(0.05)
        errtext = (err[0] if err else "").strip()
        res = None
        for line in reversed([x for x in out.strip().splitlines() if x.strip().startswith(("{", "["))] or [out.strip()]):
            try:
                res = json.loads(line)
                break
            except ValueError:
                continue
        if isinstance(res, list):  # tolerate an event array: take the result object
            res = next((x for x in reversed(res) if isinstance(x, dict) and x.get("type") == "result"), None)
        text = res.get("result") if isinstance(res, dict) and isinstance(res.get("result"), str) else ""
        blob = (text + " " + errtext + " " + out).lower()
        status, code_s, msg = "done", None, None
        if job["cancelled"]:
            status = "cancelled"
        elif job["timed_out"]:
            status, code_s, msg = "error", "timeout", "timed out"
        elif "no conversation found" in blob or "no conversations found" in blob:
            status, code_s, msg = "error", "no_session", "no Claude Code conversation found in this folder"
        elif (isinstance(res, dict) and res.get("is_error")) or code != 0 or not text.strip():
            msg = (text or errtext or f"agent exited with code {code}")[-400:]
            status, code_s = "error", _error_code(msg)
        elif "NO_BRIEF" in text:
            status, code_s, msg = "error", "empty", "the conversation says nothing about the video yet"
        if status == "done":
            try:
                job["brief"] = self.app.save_brief(clean_brief(text), "[agent] brief summarised from the terminal conversation")
            except Exception as e:  # HttpError etc.
                status, code_s, msg = "error", "failed", str(getattr(e, "message", e))
        job.update(code=code_s, error=msg)
        job["status"] = status

    def brief_cancel(self):
        j = self.bjob
        if j and j["status"] == "running":
            j["cancelled"] = True
            self._kill(j)
            for _ in range(60):
                if j["status"] != "running":
                    break
                time.sleep(0.05)
        return self.brief_snapshot()

    def shutdown(self):
        if self.bjob and self.bjob["status"] == "running":
            self.bjob["cancelled"] = True
            self._kill(self.bjob)
        for j in list(self.jobs.values()):
            if j["status"] == "running":
                j["cancelled"] = True
                self._kill(j)
