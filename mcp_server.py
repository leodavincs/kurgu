#!/usr/bin/env python3
"""Kurgu MCP server: the editing powers of Kurgu for any MCP-capable agent (Claude Code, Codex CLI, Cursor, Gemini CLI).

    python3 mcp_server.py            # speaks MCP over stdio; the project directory is a per-call `project_dir`
                                     # argument (default: $KURGU_PROJECT_DIR, else the process cwd)

Zero dependencies beyond Kurgu's own (schema.py, fonts.py, server.py, render.py). The MCP stdio transport is small,
so it is implemented here directly: newline-delimited JSON-RPC 2.0, one message per line on stdin/stdout, logs on stderr.

Protocol (checked against modelcontextprotocol.io, latest revision 2026-07-28, 29 Sep 2026):
- Modern era (2026-07-28): no handshake; every request carries `_meta["io.modelcontextprotocol/protocolVersion"]`.
  We answer `server/discover`, and tools/list, tools/call, ping straight away; results carry `resultType: "complete"`
  and `_meta["io.modelcontextprotocol/serverInfo"]`. An unknown version in `_meta` -> error -32022 with the supported list.
  (We do not enforce the required-`_meta` rules, only what we need to answer correctly.)
- Legacy era (2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05): `initialize` -> we echo the client's version if we
  support it, else answer with our newest legacy version; then `notifications/initialized`. Today's clients still use this.
- Also: ping, notifications/cancelled (stops a running render), notifications/progress for `render` when the client
  sent a progressToken, JSON-RPC batches (legacy revisions), image content blocks.
- tools/call runs on its own thread so ping and cancellation stay responsive during a render.

Decisions:
- server.py stays the single source of truth. With no editor running, edits go through `server.App.save_project`
  (same code path as PUT /api/project: atomic write, backup, changes.md). With an editor running (`.kurgu/server.json`,
  pid alive, answers HTTP) they go through its real `PUT /api/project`, so the open editor reloads live.
- edit_project works on the raw project.json (not the normalised copy), so untouched layers stay byte-identical.
  It rejects a batch only for problems the batch itself introduces; pre-existing problems and unknown fonts/missing
  media are reported as warnings.
"""
import base64
import copy
import hashlib
import http.client as httpclient
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import fonts
import media_tools
import captions
import presets
import kurgu_paths
import schema
import server as kserver
import transcribe as stt

HERE = os.path.dirname(os.path.realpath(__file__))
MODERN_VERSIONS = ["2026-07-28"]
LEGACY_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
PV_KEY = "io.modelcontextprotocol/protocolVersion"
SI_KEY = "io.modelcontextprotocol/serverInfo"


def _plugin_version():
    try:
        with open(os.path.join(kurgu_paths.data_root(), ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            return json.load(f).get("version", "0.1.0")
    except (OSError, ValueError):
        return "0.1.0"


SERVER_INFO = {"name": "kurgu", "title": "Kurgu video editor", "version": _plugin_version()}
INSTRUCTIONS = (
    "Kurgu edits videos described by a project.json timeline. Every tool takes an optional project_dir (default: the "
    "server's working directory). Before editing, call get_state (what the user has selected / where the playhead is) "
    "get_brief (the creative brief) and get_changes (what the user changed by hand). Change the project only through edit_project, look at the result "
    "with render_frame, and render only when asked. Full field reference: skills/kurgu/reference.md in the Kurgu repo."
)

NUMERIC_LAYER_KEYS = ("x", "y", "scale", "opacity", "start", "end", "fade_in", "fade_out", "src_in", "volume_db",
                      "size", "letter_spacing", "line_height", "rotation", "scale_x", "scale_y", "anchor_x", "anchor_y")
LOCK = threading.Lock()          # stdout writes
CANCELS = {}                     # request id -> threading.Event
PROCS = set()                    # child processes to reap on exit


class ToolError(Exception):
    """A problem the agent can act on; becomes a tool result with isError."""


# ---------------------------------------------------------------- small helpers
def log(*a):
    print("kurgu-mcp:", *a, file=sys.stderr, flush=True)


def text(s):
    return {"type": "text", "text": s}


def jdump(o):
    return json.dumps(o, ensure_ascii=False, indent=2)


def sha1(b):
    return hashlib.sha1(b).hexdigest()


def project_dir(args, create=False):
    p = args.get("project_dir") or os.environ.get("KURGU_PROJECT_DIR") or os.getcwd()
    p = os.path.realpath(os.path.abspath(os.path.expanduser(str(p))))
    if not os.path.isdir(p):
        if create:
            os.makedirs(p, exist_ok=True)
        else:
            raise ToolError(f"project_dir does not exist: {p}")
    return p


def require_project(d):
    if not (os.path.exists(schema.project_path(d)) or os.path.exists(schema.legacy_path(d))):
        raise ToolError(f"No project.json in {d}. Write one (see skills/kurgu/reference.md), or call open_editor, "
                        "which creates an empty template.")


def load_normalized(d):
    require_project(d)
    try:
        return schema.load_project(d)
    except schema.SchemaError as e:
        raise ToolError(str(e))


def read_raw(d):
    """(bytes, parsed dict) of project.json; a v1 project is normalised (migrated) first."""
    path = schema.project_path(d)
    if not os.path.exists(path):
        if os.path.exists(schema.legacy_path(d)):
            schema.migrate_dir(d)
        else:
            require_project(d)
    with open(path, "rb") as f:
        raw = f.read()
    try:
        proj = json.loads(raw)
    except ValueError as e:
        raise ToolError(f"project.json is not valid JSON: {e}")
    if not isinstance(proj, dict):
        raise ToolError("project.json must be a JSON object")
    if schema.is_v1(proj):
        proj = schema.normalize(proj)
    return raw, proj


def as_json_arg(v, name):
    """Some clients send arrays/objects as JSON strings; accept both."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            raise ToolError(f"{name} must be JSON, got an unparseable string")
    return v


# ---------------------------------------------------------------- editor discovery / http
def pid_alive(pid):
    if os.name == "nt":
        return True                      # os.kill(pid, 0) would terminate the process on Windows; rely on the HTTP probe
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def http(port, method, path, body=None, timeout=15, raw=False):
    """-> (status, parsed JSON | bytes when raw). Never raises on HTTP error statuses."""
    data = None if body is None else json.dumps(body).encode()
    req = Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                  headers={"Content-Type": "application/json"} if data else {})
    try:
        with urlopen(req, timeout=timeout) as r:
            payload, code = r.read(), r.status
    except HTTPError as e:
        payload, code = e.read(), e.code
    if raw:
        return code, payload
    try:
        return code, json.loads(payload or b"null")
    except ValueError:
        return code, {"error": payload[:200].decode("utf-8", "replace")}


def editor_port(d):
    """Port of a running Kurgu editor for this directory, else None (a stale server.json counts as not running)."""
    try:
        with open(os.path.join(d, ".kurgu", "server.json"), encoding="utf-8") as f:
            info = json.load(f)
        pid, port = int(info["pid"]), int(info["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not pid_alive(pid):
        return None
    try:
        code, _ = http(port, "GET", "/api/version", timeout=2)
    except Exception:
        return None
    return port if code == 200 else None


def editor_url(port):
    return f"http://127.0.0.1:{port}/"


# ---------------------------------------------------------------- read tools
def tool_open_editor(a, ctx):
    d = project_dir(a, create=True)
    port = editor_port(d)
    started = False
    if port is None:
        os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
        logf = open(os.path.join(d, ".kurgu", "server.log"), "ab")
        kw = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000008 | 0x00000200}
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"), d, "--no-open"],
                                stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, cwd=d, **kw)
        logf.close()
        deadline = time.time() + (60 if os.environ.get("CI") else 20)   # slow shared CI runners
        while time.time() < deadline and port is None:
            if proc.poll() is not None:
                break
            port = editor_port(d)
            time.sleep(0.15)
        if port is None:
            try:
                with open(os.path.join(d, ".kurgu", "server.log"), encoding="utf-8", errors="replace") as f:
                    tail = "".join(f.readlines()[-8:]).strip()
            except OSError:
                tail = ""
            raise ToolError("The editor server did not start." + (f" Log:\n{tail}" if tail else ""))
        started = True
    url = editor_url(port)
    if a.get("open_browser", True):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return [text(f"Kurgu editor {'started' if started else 'already running'} for {d}\nURL: {url}"
                 + ("\nBrowser opened." if a.get("open_browser", True) else ""))]


def active_at(project, t):
    return [l["id"] for l in project["layers"] if not l.get("hidden") and l["start"] <= t < l["end"]]


def tool_get_state(a, ctx):
    d = project_dir(a)
    port = editor_port(d)
    path = os.path.join(d, ".kurgu", "state.json")
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = None
    out = {"editor_running": port is not None, "url": editor_url(port) if port else None}
    if not st or not st.get("updated"):
        out["note"] = ("No editor state yet: the user has not opened the editor for this project"
                       if port is None else "The editor is open but has not reported a state yet (user has not clicked or seeked)")
        out["selected"], out["selected_layers"] = [], []
        return [text(jdump(out))]
    try:
        age = max(0, int(time.time() - datetime.fromisoformat(st["updated"]).timestamp()))
    except (ValueError, TypeError):
        age = None
    out.update(time=st.get("time", 0), playing=bool(st.get("playing")), language=st.get("language") or None,
               selected=st.get("selected") or [], updated=st["updated"], age_seconds=age)
    if port is None:
        out["note"] = ("STALE: the editor is not running, so this is where the user left off last time (" +
                       (f"{age}s ago" if age is not None else st["updated"]) + "). Treat 'this'/'here' as unknown and ask.")
    else:
        out["note"] = ("The editor reports state when the user seeks, selects or changes language, so it stays valid "
                       "until they act again. If the request is ambiguous and this is old, ask.")
    proj = load_normalized(d)
    by_id = {l["id"]: l for l in proj["layers"]}
    out["selected_layers"] = [by_id[i] for i in out["selected"] if i in by_id]
    missing = [i for i in out["selected"] if i not in by_id]
    if missing:
        out["selected_ids_not_in_project"] = missing
    try:
        out["layers_active_at_playhead"] = active_at(proj, float(out["time"]))
    except (TypeError, ValueError):
        pass
    return [text(jdump(out))]


def num(v):
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def key_props(l, w, h):
    t, bits = l["type"], []
    if t == "text":
        s = l.get("text", "").replace("\n", " / ")
        bits.append('"' + (s[:40] + ("…" if len(s) > 40 else "")) + '"')
        bits.append(f'{l.get("font")} {num(l.get("size"))}px {l.get("color")}')
    elif t in ("video", "image", "audio"):
        bits.append(l.get("src", ""))
        if l.get("src_in"):
            bits.append(f'src_in={num(l["src_in"])}')
        if t != "audio":
            bits.append(f'fit={l.get("fit")}')
        if t != "image":
            if l.get("muted"):
                bits.append("muted")
            elif l.get("volume_db"):
                bits.append(f'{num(l["volume_db"])}dB')
            if l.get("envelope"):
                bits.append(f'envelope({len(l["envelope"])} pts)')
    elif t == "color":
        bits.append(l.get("color", ""))
    if t != "audio" and t != "color":
        if l.get("scale", 1) != 1 or (abs(l.get("x", w / 2) - w / 2) > 0.5 or abs(l.get("y", h / 2) - h / 2) > 0.5):
            bits.append(f'pos=({num(l.get("x"))},{num(l.get("y"))})' + (f' ×{num(l["scale"])}' if l.get("scale", 1) != 1 else ""))
    if t != "audio" and l.get("opacity", 1) != 1:
        bits.append(f'opacity={num(l["opacity"])}')
    if l.get("fade_in") or l.get("fade_out"):
        bits.append(f'fade {num(l.get("fade_in", 0))}/{num(l.get("fade_out", 0))}')
    if l.get("rotation"):
        bits.append(f'rotation={num(l["rotation"])}')
    anim_bits = [f"keys:{','.join(l['keys'])}" if l.get("keys") else "", f"mods:{','.join(l['mods'])}" if l.get("mods") else ""]
    bits += [b for b in anim_bits if b]
    effs = [e["type"] for e in l.get("effects") or [] if e.get("enabled", True)]
    if effs:
        bits.append("fx:" + ",".join(effs))
    for flag in ("hidden", "locked"):
        if l.get(flag):
            bits.append(flag)
    if l.get("group"):
        bits.append(f'group={l["group"]}')
    if l.get("link"):
        bits.append(f'link={l["link"]}')
    return "; ".join(str(b) for b in bits)


def tool_get_project(a, ctx):
    d = project_dir(a)
    raw, _ = read_raw(d)
    proj = load_normalized(d)
    ver = sha1(raw)
    if not a.get("summary"):
        return [text(jdump({"version": ver, "project": proj}))]
    g = proj["global"]
    lines = [f'Project "{proj["name"]}" {proj["width"]}x{proj["height"]} @{num(proj["fps"])}fps, {num(proj["duration"])}s, '
             f'output={proj["output"]}, background={proj["background"]}',
             f'global: grain={num(g.get("grain"))} vignette={num(g.get("vignette"))} '
             f'loudness_lufs={g.get("loudness_lufs")} fade_out={num(g.get("fade_out"))}',
             f"version: {ver}  (layers listed front-most first)", ""]
    lines += ["| # | id | type | name | start | end | key props |", "|---|---|---|---|---|---|---|"]
    for i, l in enumerate(proj["layers"]):
        lines.append(f'| {i} | {l["id"]} | {l["type"]} | {l.get("name", "")} | {num(l["start"])} | {num(l["end"])} | '
                     f'{key_props(l, proj["width"], proj["height"]).replace("|", "/")} |')
    if not proj["layers"]:
        lines.append("| | (no layers) | | | | | |")
    groups = schema.link_groups(proj)
    if groups:
        off = {(lk, i): d for lk, i, d in schema.link_offsets(proj)}
        lines += ["", "Linked clips (move, trim, split and delete as one unit in the editor; keep them in sync, see the kurgu skill):"]
        for lk, g in groups.items():
            lines.append(f'- {lk}: ' + " + ".join(f'{x["id"]} ({x["type"]})' for x in g)
                         + ("; OUT OF SYNC: " + ", ".join(f'{i} {d:+g}s' for (k, i), d in off.items() if k == lk) if any(k == lk for k, _ in off) else "; in sync"))
    if proj["notes"]:
        openn = [n for n in proj["notes"] if not n.get("done")]
        lines += ["", f'Notes from the user ({len(openn)} open of {len(proj["notes"])}; treat open ones as instructions, '
                      'then resolve each with update_note done=true + a one-line reply; see get_notes):']
        lines += [f'- {n["id"]} @ {num(n["t"])}s [{"done" if n.get("done") else "open"}]: {n["text"]}' for n in proj["notes"]]
    return [text("\n".join(lines))]


def tool_get_changes(a, ctx):
    d = project_dir(a)
    limit = max(1, min(int(a.get("limit") or 30), 500))
    path = os.path.join(d, "changes.md")
    if not os.path.exists(path):
        return [text("No changes.md yet: nothing has been edited through the editor or edit_project.")]
    heading, entries = "", []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("## "):
                heading = line[3:].strip()
            elif line.startswith("- "):
                entries.append((heading, line))
    if not entries:
        return [text("changes.md has no entries yet.")]
    out, last = ["Most recent last. Lines with [agent] were made through edit_project; the others were made by the user by hand.", ""], None
    for h, line in entries[-limit:]:
        if h != last:
            out.append(f"## {h}")
            last = h
        out.append(line)
    return [text("\n".join(out))]


def tool_get_brief(a, ctx):
    d = project_dir(a)
    try:
        with open(os.path.join(d, "brief.md"), encoding="utf-8") as f:
            b = f.read().strip()
    except OSError:
        b = ""
    if not b:
        return [text("No brief yet (brief.md). If the user has told you what the video is, who it is for, its style or any rules, "
                     "write it down with update_brief.")]
    return [text(b)]


def tool_update_brief(a, ctx):
    d = project_dir(a)
    body = a.get("text")
    if not isinstance(body, str) or not body.strip():
        raise ToolError("text is required: the full new brief (short bullets).")
    body = body.strip()
    if len(body) > 2000:
        raise ToolError(f"Brief too long ({len(body)} > 2000 characters): tighten it to short bullets (max about 250 words).")
    payload = {"text": body, "summary": "[agent] brief updated"}
    port = editor_port(d)
    if port:
        code, data = http(port, "PUT", "/api/brief", payload)
    else:
        try:
            code, data = 200, kserver.App(d).save_brief(body, "[agent] brief updated")
        except kserver.HttpError as e:
            code, data = e.code, {"error": e.message}
    if code != 200:
        raise ToolError(f'Could not save the brief (HTTP {code}): {data.get("error", data) if isinstance(data, dict) else data}')
    return [text(f"Brief updated ({len(body)} characters). Ask AI in the editor now sees it.")]


def tool_list_fonts(a, ctx):
    d = project_dir(a)
    src, q = a.get("source"), (a.get("query") or "").lower()
    faces = fonts.list_faces(d)
    if src in ("project", "bundled", "system"):
        faces = [f for f in faces if f["source"] == src]
    system_hidden = 0
    if not src and not q:
        system_hidden = sum(1 for f in faces if f["source"] == "system")
        faces = [f for f in faces if f["source"] != "system"]
    if q:
        faces = [f for f in faces if q in f["id"] or q in f["family"].lower() or q in f["style"].lower()]
    lines = [f'{f["id"]}  ({f["family"]} {f["style"]}, {f["source"]})' for f in faces] or ["(no matching fonts)"]
    if system_hidden:
        lines.append(f"... plus {system_hidden} system fonts: pass query=\"name\" or source=\"system\" to list them.")
    lines.append("Use the id in a text layer's `font`. Turkish ş ğ ı İ ö ü ç: all bundled fonts have them.")
    return [text("\n".join(lines))]


_PROBE = {}


def probe(path):
    key = (path, os.stat(path).st_mtime_ns)
    if key not in _PROBE:
        try:
            data = kserver.App.ffprobe(path)
        except Exception:
            _PROBE[key] = None
        else:
            streams = data.get("streams", [])
            v = next((s for s in streams if s.get("codec_type") == "video"), None)
            try:
                dur = float(data.get("format", {}).get("duration") or 0)
            except ValueError:
                dur = 0.0
            _PROBE[key] = {"duration": round(dur, 2), "size": f'{v["width"]}x{v["height"]}' if v else None,
                           "audio": any(s.get("codec_type") == "audio" for s in streams)}
    return _PROBE[key]


def tool_list_media(a, ctx):
    d = project_dir(a)
    files = kserver.App(d).list_files()
    lines = ["path | kind | bytes | duration s | size | audio"]
    for i, f in enumerate(files):
        info = probe(os.path.join(d, f["path"])) if f["kind"] != "font" and i < 150 else None
        lines.append(" | ".join([f["path"], f["kind"], str(f["size"]),
                                 str(info["duration"]) if info and f["kind"] != "image" else "",
                                 (info["size"] or "") if info else "", ("yes" if info["audio"] else "no") if info and f["kind"] == "video" else ""]))
    if not files:
        lines.append("(no media yet: put files in media/ or use import_file)")
    return [text("\n".join(lines))]


# ---------------------------------------------------------------- edit_project
def merge_one_level(target, patch, null_deletes):
    for k, v in patch.items():
        if v is None and null_deletes:
            target.pop(k, None)
        elif isinstance(v, dict) and isinstance(target.get(k), dict):
            for kk, vv in v.items():
                if vv is None and null_deletes:
                    target[k].pop(kk, None)
                else:
                    target[k][kk] = vv
        else:
            target[k] = v


def check_numbers(l, tag, probs):
    for k in NUMERIC_LAYER_KEYS:
        if k in l and (isinstance(l[k], bool) or not isinstance(l[k], (int, float))):
            probs.append(f"{tag}: {k} must be a number, got {l[k]!r}")


def apply_batch(proj, a):
    """Apply update/remove/add/project to a deep copy of `proj`. -> (new project, counts, touched layers). Raises ToolError."""
    p = copy.deepcopy(proj)
    layers = p.setdefault("layers", [])
    probs = []
    by_id = {l.get("id"): l for l in layers if isinstance(l, dict)}
    upd = as_json_arg(a.get("update") or [], "update")
    add = as_json_arg(a.get("add") or [], "add")
    rem = as_json_arg(a.get("remove") or [], "remove")
    patch = as_json_arg(a.get("project"), "project")
    if not all(isinstance(x, list) for x in (upd, add, rem)):
        raise ToolError("update, add and remove must be arrays")
    touched = []
    for u in upd:
        if not isinstance(u, dict) or "id" not in u or not isinstance(u.get("set"), dict):
            probs.append(f"update entry must look like {{id, set: {{...}}}}, got {json.dumps(u)[:80]}")
            continue
        l = by_id.get(u["id"])
        if l is None:
            probs.append(f'update: no layer with id "{u["id"]}" (existing: {", ".join(map(str, by_id)) or "none"})')
            continue
        if "id" in u["set"] and u["set"]["id"] != u["id"]:
            probs.append(f'update "{u["id"]}": ids are stable and cannot be changed')
            continue
        merge_one_level(l, u["set"], null_deletes=True)
        touched.append(l)
    rem_ids = set()
    for rid in rem:
        if rid not in by_id:
            probs.append(f'remove: no layer with id "{rid}"')
        rem_ids.add(rid)
    layers[:] = [l for l in layers if not (isinstance(l, dict) and l.get("id") in rem_ids)]
    new_layers = []
    for l in add:
        if not isinstance(l, dict):
            probs.append(f"add entry must be a layer object, got {json.dumps(l)[:80]}")
            continue
        l = copy.deepcopy(l)
        if not l.get("id"):
            l["id"] = f'{l.get("type", "layer")}_{uuid.uuid4().hex[:4]}'
        new_layers.append(l)
    if new_layers:
        idx = a.get("index", 0)
        idx = 0 if idx is None else max(0, min(int(idx), len(layers)))
        layers[idx:idx] = new_layers
        touched += new_layers
    if patch is not None:
        if not isinstance(patch, dict):
            raise ToolError("project must be an object")
        for bad in ("layers", "version"):
            if bad in patch:
                probs.append(f'project.{bad} cannot be set here: use update/add/remove for layers')
        patch = {k: v for k, v in patch.items() if k not in ("layers", "version")}
        merge_one_level(p, patch, null_deletes=False)
    for l in touched:
        check_numbers(l, f'layer "{l.get("name") or l.get("id")}" ({l.get("id")})', probs)
    for l in touched:                       # presets (`use`) are baked into plain keys/mods so the browser preview and render agree
        if isinstance(l.get("use"), list) and not presets.validate_use(l, "use"):
            baked = presets.bake_layer(l, p.get("width", 1920), p.get("height", 1080))
            l.clear()
            l.update(baked)
    schema.stamp_requires(p)
    if probs:
        raise ToolError("Batch rejected, nothing written:\n" + "\n".join("- " + x for x in probs))
    return p, {"updated": len(upd), "added": [l["id"] for l in new_layers], "removed": len(rem_ids)}, touched


def edit_problems(before, after, d):
    font_ids = [f["id"] for f in fonts.list_faces(d)]
    old = set(schema.validate(before, font_ids))
    errors, warnings = [], []
    for x in schema.validate(after, font_ids):
        if x in old:
            continue
        (warnings if "not found (falls back" in x else errors).append(x)
    return errors, warnings


def tool_edit_project(a, ctx):
    d = project_dir(a)
    if not any(a.get(k) for k in ("update", "add", "remove", "project")):
        raise ToolError("Nothing to do: pass at least one of update, add, remove, project.")
    for attempt in (0, 1):
        raw, proj = read_raw(d)
        new, counts, touched = apply_batch(proj, a)
        errors, warnings = edit_problems(proj, new, d)
        if errors:
            raise ToolError("Batch rejected, nothing written:\n" + "\n".join("- " + x for x in errors))
        for l in touched:
            src = l.get("src")
            if isinstance(src, str) and src and not os.path.exists(os.path.join(d, os.path.expanduser(src))):
                warnings.append(f'layer "{l.get("name") or l.get("id")}": file not found: {src} (render will fail until it exists)')
        old_off = {(lk, i) for lk, i, _ in schema.link_offsets(proj)}
        for lk, i, dd in schema.link_offsets(new):
            if (lk, i) not in old_off:
                warnings.append(f'linked clips out of sync: layer "{i}" is {dd:+g}s off its partner (link {lk}); change both layers together, or unlink with link:null')
        try:
            dur = float(new.get("duration", 0))
            last = max([float(l.get("end", 0)) for l in new.get("layers", []) if isinstance(l.get("end"), (int, float))] or [0])
            if last > dur + 1e-6:
                warnings.append(f"a layer ends at {last:g}s but project duration is {dur:g}s: raise `duration` via project:{{duration:{last:g}}} if intended")
        except (TypeError, ValueError):
            pass
        summary = (a.get("summary") or "").strip() or ", ".join(
            f"{n} {w}" for n, w in ((counts["updated"], "updated"), (len(counts["added"]), "added"), (counts["removed"], "removed"),
                                    (1 if a.get("project") else 0, "project fields")) if n)
        body = {"project": new, "summary": ["[agent] " + summary], "base_version": sha1(raw)}
        port = editor_port(d)
        if port:
            code, data = http(port, "PUT", "/api/project", body)
        else:
            try:
                code, data = kserver.App(d).save_project(body)
            except kserver.HttpError as e:
                code, data = e.code, {"error": e.message}
        if code == 200:
            msg = (f'Applied: {counts["updated"]} updated, {len(counts["added"])} added'
                   + (f' ({", ".join(counts["added"])})' if counts["added"] else "") + f', {counts["removed"]} removed.'
                   f' New version {data["version"][:10]}.')
            msg += (" The open editor reloads live." if port else
                    " Editor not running: written to project.json directly (backup in .kurgu/backups, changes.md updated).")
            if warnings:
                msg += "\nWarnings:\n" + "\n".join("- " + w for w in warnings)
            return [text(msg)]
        if code == 409 and attempt == 0:
            continue                       # the file changed between read and write: re-read, re-apply, retry once
        raise ToolError(f'Could not save (HTTP {code}): {data.get("error", data) if isinstance(data, dict) else data}')
    raise ToolError("project.json keeps changing under us (the user is editing); try again in a moment.")


# ---------------------------------------------------------------- notes tools (the user's timeline comments)
def _layers_under(proj, t):
    return [{"id": l.get("id"), "name": l.get("name", ""), "type": l.get("type")} for l in proj.get("layers", [])
            if isinstance(l, dict) and not l.get("hidden") and l.get("start", 0) <= t < l.get("end", 0)]


def _note_view(n, proj):
    out = {"id": n["id"], "t": n["t"], "text": n["text"], "status": "done" if n["done"] else "open",
           "author": n["author"], "layers": _layers_under(proj, n["t"])}
    for k in ("reply", "resolved_at", "created"):
        if n.get(k):
            out[k] = n[k]
    return out


def _now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def _write_notes(d, mutate, summary):
    """Read project.json, let `mutate(notes, proj)` change the normalised notes list, save it like edit_project does
    (through the running editor when there is one, so it reloads live; one retry on a version conflict)."""
    for attempt in (0, 1):
        raw, proj = read_raw(d)
        new = json.loads(json.dumps(proj))
        new["notes"] = [schema.normalize_note(n, i) for i, n in enumerate(new.get("notes") or []) if isinstance(n, dict)]
        result = mutate(new["notes"], new)
        body = {"project": new, "summary": ["[agent] " + summary(result) if callable(summary) else "[agent] " + summary],
                "base_version": sha1(raw)}
        port = editor_port(d)
        if port:
            code, data = http(port, "PUT", "/api/project", body)
        else:
            try:
                code, data = kserver.App(d).save_project(body)
            except kserver.HttpError as e:
                code, data = e.code, {"error": e.message}
        if code == 200:
            return result
        if code == 409 and attempt == 0:
            continue
        raise ToolError(f'Could not save (HTTP {code}): {data.get("error", data) if isinstance(data, dict) else data}')
    raise ToolError("project.json keeps changing under us (the user is editing); try again in a moment.")


def tool_get_notes(a, ctx):
    d = project_dir(a)
    status = a.get("status") or "open"
    if status not in ("open", "done", "all"):
        raise ToolError('status must be "open", "done" or "all".')
    proj = schema.normalize(read_raw(d)[1])
    notes = sorted(proj["notes"], key=lambda n: n["t"])
    if status != "all":
        notes = [n for n in notes if n["done"] == (status == "done")]
    views = [_note_view(n, proj) for n in notes]
    if not views:
        return [text(f"No {status if status != 'all' else ''} notes.".replace("  ", " "))]
    return [text(jdump({"status": status, "count": len(views), "notes": views,
                        "hint": "Handle each open note at its time t (see `layers` for what is on screen there), then call "
                                "update_note with done=true and a one-line reply saying what you changed."}))]


def tool_update_note(a, ctx):
    d = project_dir(a)
    nid = a.get("id")
    if not isinstance(nid, str) or not nid:
        raise ToolError("id is required (see get_notes).")
    if not any(k in a for k in ("text", "done", "reply", "t")):
        raise ToolError("Nothing to do: pass at least one of text, done, reply, t.")
    for k, ty in (("text", str), ("reply", str), ("done", bool)):
        if k in a and not isinstance(a[k], ty):
            raise ToolError(f"{k} must be a {'string' if ty is str else 'boolean'}.")
    if "t" in a and (isinstance(a["t"], bool) or not isinstance(a["t"], (int, float)) or a["t"] < 0):
        raise ToolError("t must be a number >= 0 (seconds).")

    def mutate(notes, proj):
        n = next((x for x in notes if x["id"] == nid), None)
        if n is None:
            raise ToolError(f'No note with id "{nid}". Call get_notes to see the ids.')
        if "text" in a:
            n["text"] = a["text"].strip()
        if "t" in a:
            n["t"] = round(float(a["t"]), 2)
        if "reply" in a:
            n["reply"] = a["reply"].strip()
        if "done" in a:
            if a["done"] and not n["done"]:
                n["resolved_at"] = _now_iso()
            n["done"] = a["done"]
            if not a["done"]:
                n["resolved_at"] = ""
        return n

    n = _write_notes(d, mutate, lambda n: f'note @ {num(n["t"])}s {"done" if n["done"] else "updated"}'
                                          + (f': {n["reply"]}' if n.get("reply") else ""))
    return [text(f'Note {nid} @ {num(n["t"])}s is now {"done" if n["done"] else "open"}'
                 + (f' (reply: {n["reply"]})' if n.get("reply") else "") + ".")]


def tool_add_note(a, ctx):
    d = project_dir(a)
    body = a.get("text")
    if not isinstance(body, str) or not body.strip():
        raise ToolError("text is required.")
    t = a.get("t")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or t < 0:
        raise ToolError("t is required: a number >= 0 (seconds on the timeline).")

    def mutate(notes, proj):
        ids = {x["id"] for x in notes}
        nid = "n_" + uuid.uuid4().hex[:4]
        while nid in ids:
            nid = "n_" + uuid.uuid4().hex[:6]
        n = schema.normalize_note({"id": nid, "t": round(float(t), 2), "text": body.strip(), "created": _now_iso(),
                                   "author": "agent", "layer_ids": [l["id"] for l in _layers_under(proj, float(t))]})
        notes.append(n)
        return n

    n = _write_notes(d, mutate, lambda n: f'note added @ {num(n["t"])}s: {n["text"]}')
    return [text(f'Added note {n["id"]} @ {num(n["t"])}s (shows in the editor as an agent note).')]


# ---------------------------------------------------------------- render tools
def tool_render_frame(a, ctx):
    d = project_dir(a)
    require_project(d)
    try:
        t = float(a.get("t", 0))
    except (TypeError, ValueError):
        raise ToolError("t must be a number of seconds")
    if t < 0:
        raise ToolError("t must be >= 0")
    draft = a.get("draft", True)
    port = editor_port(d)
    try:
        if draft and port:
            code, png = http(port, "GET", f"/api/frame?t={t}", raw=True, timeout=130)
            if code != 200:
                try:
                    msg = json.loads(png).get("error", "")
                except ValueError:
                    msg = ""
                raise ToolError(f"Frame render failed: {msg or code}")
        elif draft:
            with open(kserver.App(d).frame(t), "rb") as f:
                png = f.read()
        else:
            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            try:
                r = subprocess.run([sys.executable, os.path.join(HERE, "render.py"), d, "--frame", f"{t:.3f}", "--output", tmp],
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
                if r.returncode != 0:
                    err = next((s[5:].strip() for s in r.stdout.splitlines() if s.startswith("ERROR")), None)
                    raise ToolError("Frame render failed: " + (err or (r.stderr.strip().splitlines() or ["unknown error"])[-1]))
                with open(tmp, "rb") as f:
                    png = f.read()
            finally:
                os.path.exists(tmp) and os.remove(tmp)
    except kserver.HttpError as e:
        raise ToolError(f"Frame render failed: {e.message}")
    return [{"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
            text(f"Frame at t={t:g}s ({'draft, half resolution' if draft else 'full resolution'}).")]


def tool_render(a, ctx):
    d = project_dir(a)
    require_project(d)
    draft = a.get("draft", True)
    cmd = [sys.executable, os.path.join(HERE, "render.py"), d] + (["--draft"] if draft else [])
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    PROCS.add(proc)
    cancel = ctx["cancel"]

    def watch():
        while proc.poll() is None:
            if cancel.wait(0.2):
                proc.terminate()
                return
    threading.Thread(target=watch, daemon=True).start()
    output, error, warns, tail, last = None, None, [], [], -1.0
    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESS"):
                try:
                    pr = round(max(0.0, min(1.0, float(line.split()[1]))) * 100, 1)
                except (IndexError, ValueError):
                    continue
                if pr > last:
                    last = pr
                    ctx["progress"](pr, 100, f"rendering {pr:g}%")
            elif line.startswith("DONE"):
                output = line[4:].strip()
            elif line.startswith("ERROR"):
                error = line[5:].strip() or "unknown error"
            elif line.startswith("WARN"):
                warns.append(line[4:].strip())
            elif line:
                tail = (tail + [line])[-5:]
        proc.wait()
    finally:
        PROCS.discard(proc)
    if cancel.is_set():
        raise ToolError("Render cancelled.")
    if proc.returncode != 0 or error or not output:
        raise ToolError("Render failed: " + (error or f"render.py exited with code {proc.returncode}: " + " | ".join(tail)))
    dur = None
    try:
        r = subprocess.run([media_tools.ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", output],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        dur = round(float(r.stdout.strip()), 2)
    except Exception:
        try:
            dur = load_normalized(d)["duration"]
        except Exception:
            pass
    msg = f'Rendered {"draft" if draft else "full quality"}: {output}\nduration: {dur if dur is not None else "?"} s, took {time.time() - t0:.0f} s'
    if warns:
        msg += "\nWarnings:\n" + "\n".join("- " + w for w in warns)
    return [text(msg)]


# ---------------------------------------------------------------- import_file
def tool_import_file(a, ctx):
    d = project_dir(a)
    src = a.get("path")
    if not src:
        raise ToolError("path is required (absolute path of the file to import)")
    src = os.path.abspath(os.path.expanduser(src))
    if not os.path.isfile(src):
        raise ToolError(f"file not found: {src}")
    stem, ext = kserver.safe_name(os.path.basename(src))
    if ext in kserver.REJECT_FONT_EXT:
        raise ToolError("woff/woff2 fonts are not supported; use .ttf, .otf or .ttc")
    kind = kserver.kind_of("x" + ext)
    if kind is None:
        raise ToolError(f"unsupported file type: {ext or '(no extension)'} (video, image, audio or ttf/otf/ttc fonts)")
    port = editor_port(d)
    if port:
        conn = httpclient.HTTPConnection("127.0.0.1", port, timeout=600)
        try:
            with open(src, "rb") as f:
                conn.request("POST", "/api/import?name=" + quote(os.path.basename(src)), body=f,
                             headers={"Content-Length": str(os.path.getsize(src))})
                resp = conn.getresponse()
                data = json.loads(resp.read() or b"{}")
        finally:
            conn.close()
        if resp.status != 200:
            raise ToolError(f'Import failed: {data.get("error", resp.status)}')
        result = data
    else:
        app = kserver.App(d)
        fd, dest = app.reserve(os.path.join(d, "fonts" if kind == "font" else "media"), stem, ext)
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out, 1024 * 1024)
        result = {"path": os.path.relpath(dest, d).replace(os.sep, "/"), "kind": kind}
        if kind == "font":
            fonts.refresh()
            faces = fonts.scan_font_file(dest, "project")
            if not faces:
                os.remove(dest)
                raise ToolError("not a readable font file")
            result["font_ids"] = [f["id"] for f in faces]
    msg = f'Imported {kind}: {result["path"]}  (use "src": "{result["path"]}")' if kind != "font" else \
        f'Imported font: {result["path"]}; font ids: {", ".join(result.get("font_ids", []))}'
    return [text(msg)]


# ---------------------------------------------------------------- transcription + captions (optional local speech-to-text)
def _transcript_for(a, ctx):
    """-> (transcript in timeline time, raw project, project dir, layer|None). Path mode: source time + `offset`."""
    d = project_dir(a)
    raw, proj = read_raw(d)
    try:
        tgt = stt.resolve_target(proj, d, a.get("path"), a.get("layer_id"))
        model = stt.canonical_model(a.get("model"))

        span = {"extracting": (0.0, 0.05), "downloading": (0.05, 0.4), "transcribing": (0.4, 0.98), "done": (1.0, 1.0)}

        def progress(stage, frac, message):          # MCP progress must only grow: map each stage onto its own slice
            lo, hi = span.get(stage, (0.0, 1.0))
            ctx["progress"](round(lo + (hi - lo) * frac, 3), 1.0, message)
        tr = stt.transcribe_file(d, tgt["path"], model, a.get("language"), tgt["src_in"], tgt["duration"], progress, ctx["cancel"])
    except stt.TranscribeError as e:
        raise ToolError(str(e))
    except stt.Cancelled:
        raise ToolError("cancelled")
    lay = tgt["layer"]
    if lay is not None:
        tr = stt.to_timeline(tr, float(lay["start"]), tgt["src_in"], float(lay["end"]))
    else:
        off = float(a.get("offset") or 0)
        tr = stt.to_timeline(tr, off, 0.0, None)
    return tr, proj, d, lay


def tool_transcribe(a, ctx):
    av = stt.availability()
    if not av["available"]:
        raise ToolError(av["message"])
    tr, _proj, _d, lay = _transcript_for(a, ctx)
    words = stt.all_words(tr)
    head = (f'language {tr.get("language")}, model {tr.get("model")} ({tr.get("backend")}){", cached" if tr.get("cached") else ""}, '
            f'{len(words)} words, times in {"TIMELINE" if lay else "source (+offset)"} seconds'
            + (f' for layer "{lay["id"]}"' if lay else ""))
    lines = [head]
    for s in tr["segments"]:
        lines.append(f'[{s["start"]:.2f}-{s["end"]:.2f}] {s["text"]}')
    if a.get("words", True):
        lines.append("words [start, end, word, prob]:")
        lines.append(json.dumps([[w["start"], w["end"], w["word"], w.get("prob")] for w in words], ensure_ascii=False,
                                separators=(",", ":")))
    lines.append("Next: make_captions with the same layer_id adds caption text layers from these words.")
    return [text("\n".join(lines))]


def tool_make_captions(a, ctx):
    av = stt.availability()
    if not av["available"]:
        raise ToolError(av["message"])
    tr, proj, d, lay = _transcript_for(a, ctx)
    words = stt.all_words(tr)
    if not words:
        raise ToolError("No speech found in that range: no captions added.")
    W, H = int(proj.get("width") or 1920), int(proj.get("height") or 1080)
    layers = proj.get("layers", [])
    ours = [l for l in layers if isinstance(l, dict) and captions.ID_RE.match(str(l.get("id", "")))]
    replace = a.get("replace", True)
    first = 1 if replace else captions.next_index(l.get("id") for l in ours)
    y = a.get("y")
    new = captions.build_layers(words, W, H, first, a.get("max_words_per_line"), a.get("max_chars"), a.get("font") or "inter-bold",
                                a.get("size"), y, bool(a.get("uppercase")), a.get("color") or "#ffffff", language=tr.get("language", ""))
    rem = [l["id"] for l in ours] if replace else []
    kind = "vertical" if captions.is_vertical(W, H) else "landscape"
    summary = f'{len(new)} caption layers (cap_{first}..cap_{first + len(new) - 1}) from speech, {kind} {W}x{H}'
    res = tool_edit_project({"project_dir": d, "add": new, "remove": rem, "index": 0, "summary": summary}, ctx)
    return [text(f"{summary}; replaced {len(rem)} old cap_* layers. " + res[0]["text"])]


# ---------------------------------------------------------------- tool table
PD = {"type": "string", "description": "Project directory (holds project.json). Default: $KURGU_PROJECT_DIR or the server's working directory."}


def schema_of(props, required=()):
    props = dict(props, project_dir=PD)
    s = {"type": "object", "properties": props}
    if required:
        s["required"] = list(required)
    return s


TOOLS = [
    ("open_editor", "Open the Kurgu browser editor",
     "Start the Kurgu editor for the project (if not already running) and return its URL. Creates an empty project.json if there is none. "
     "The user edits by hand there; you keep editing through edit_project and the editor reloads live.",
     schema_of({"open_browser": {"type": "boolean", "description": "Also open the URL in the user's browser (default true)."}}),
     tool_open_editor, {"readOnlyHint": False, "idempotentHint": True, "openWorldHint": False}),
    ("get_state", "What the user is looking at",
     "What the human has selected and where the playhead is in the editor, with the full JSON of the selected layers. "
     "Call this first to resolve 'this text', 'this clip', 'here', 'now'. Says clearly if the editor is not open or the state is stale.",
     schema_of({}), tool_get_state, {"readOnlyHint": True}),
    ("get_project", "Read the project",
     "Read the normalised project (every default filled in) with its version. summary=true returns a compact table of layers "
     "(id, type, name, start, end, key props) plus global settings and the user's timeline notes: use it for orientation, "
     "then read full JSON only when you need details.",
     schema_of({"summary": {"type": "boolean", "description": "Compact layer table instead of full JSON (default false)."}}),
     tool_get_project, {"readOnlyHint": True}),
    ("edit_project", "Edit the project (atomic batch)",
     "Change the project in one atomic batch; invalid batches are rejected whole with readable problems. Applied in order: "
     "update, remove, add, project. `update`: [{id, set:{...}}] shallow-merges fields into a layer (nested objects such as shadow/crop merge one level; "
     "null deletes a field, back to its default). `add`: layer objects (id optional, generated if missing; type, start, end required; media need src), "
     "inserted at `index` (0 = front-most, default). `remove`: layer ids. `project`: top-level fields (name, width, height, fps, duration, background, output, "
     "global:{grain,vignette,loudness_lufs,fade_out}, notes). Ids are stable: never change them. Field reference: skills/kurgu/reference.md. "
     "Animation: a layer's `keys` ({prop: [{t, v, ease}]}, layer-local seconds), `mods` (wiggle/noise) and `use` (presets cam.punch, cam.ken-burns, cam.shake, "
     "baked into keys/mods on save) go through `set` like any field: {id, set:{keys:{opacity:[{t:0,v:0,ease:\"out-quad\"},{t:0.3,v:1}]}}} replaces just that track. "
     "Also give `summary`, one human sentence: it is logged in changes.md as [agent].",
     schema_of({
         "update": {"type": "array", "description": "Patches to existing layers.",
                    "items": {"type": "object", "properties": {"id": {"type": "string"}, "set": {"type": "object"}}, "required": ["id", "set"]}},
         "add": {"type": "array", "description": "New layers (full layer objects).", "items": {"type": "object"}},
         "index": {"type": "integer", "description": "Where `add` inserts in the layer list; 0 = front-most (default)."},
         "remove": {"type": "array", "description": "Layer ids to delete.", "items": {"type": "string"}},
         "project": {"type": "object", "description": "Top-level/global fields to merge (one level deep). Not `layers`."},
         "summary": {"type": "string", "description": "One-line human description of the change, for changes.md."}}),
     tool_edit_project, {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}),
    ("render_frame", "See a still frame",
     "Render one frame at time t (seconds) and return it as an image so you can look at it. Check first frame, titles, the end, "
     "after every visual change. draft=true (default) is fast, half resolution.",
     schema_of({"t": {"type": "number", "description": "Timeline time in seconds."},
                "draft": {"type": "boolean", "description": "Half resolution, cached (default true). false = full resolution."}}, ["t"]),
     tool_render_frame, {"readOnlyHint": True}),
    ("render", "Render the video",
     "Render the whole video with render.py and wait for it (can take minutes; reports progress if the client supports it). "
     "Draft (default) is half resolution and quick, written to <output>_draft.mp4; use draft=false for the final quality only when the user says it is right.",
     schema_of({"draft": {"type": "boolean", "description": "Half-resolution quick render (default true)."}}),
     tool_render, {"readOnlyHint": False, "idempotentHint": True, "openWorldHint": False}),
    ("get_changes", "What the user changed by hand",
     "Last lines of changes.md: one line per edit with time, newest last. Lines starting [agent] came from edit_project, "
     "the rest are the user's manual edits: respect those, do not revert them unless asked.",
     schema_of({"limit": {"type": "integer", "description": "How many lines (default 30)."}}), tool_get_changes, {"readOnlyHint": True}),
    ("get_brief", "Read the creative brief",
     "Read brief.md: the short creative brief of this video (what it is, audience/platform, tone and style, hard rules, open decisions). "
     "Read it before editing so your changes fit the story and the user's rules.",
     schema_of({}), tool_get_brief, {"readOnlyHint": True}),
    ("update_brief", "Update the creative brief",
     "Replace brief.md with a new short brief (max 2000 characters, about 250 words, Markdown bullets: what the video is; audience/platform; "
     "tone and style; hard rules such as 'no orange' or 'real footage only'; open decisions). Call it whenever the user states intent, style, "
     "audience or a rule about the video: read the old brief first (get_brief), merge the new fact, write the whole thing back. "
     "No secrets, paths or personal data. Logged in changes.md as [agent]; the Ask AI box in the editor passes it to its agent.",
     schema_of({"text": {"type": "string", "description": "The complete new brief (replaces the old one)."}}, ["text"]),
     tool_update_brief, {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}),
    ("get_notes", "Read the user's timeline notes",
     "The timeline notes the user (or an agent) left, sorted by time, each with id, t (seconds), text, status (open/done), author "
     "(user/agent), optional reply, and `layers`: the layers on screen at that moment. status defaults to open. "
     "When the user says 'apply my notes' (or 'notlarımı uygula'): call this, handle each open note at its time with edit_project "
     "(check the frame with render_frame), then call update_note for it with done=true and a one-line reply.",
     schema_of({"status": {"type": "string", "enum": ["open", "done", "all"], "description": "Which notes (default open)."}}),
     tool_get_notes, {"readOnlyHint": True}),
    ("update_note", "Update or resolve a note",
     "Change a note by id: mark it done (done=true, also stamps resolved_at), reopen it (done=false), edit its text or time, "
     "or set `reply`, a one-line answer the user sees under the note in the editor's Notes panel, e.g. \"Blur reduced 22 to 8\". "
     "Notes are never deleted by agents: resolve them instead so the user keeps the history.",
     schema_of({"id": {"type": "string", "description": "Note id from get_notes."},
                "done": {"type": "boolean", "description": "true = resolved, false = reopen."},
                "reply": {"type": "string", "description": "One-line reply shown under the note."},
                "text": {"type": "string", "description": "New note text."},
                "t": {"type": "number", "description": "New time in seconds."}}, ["id"]),
     tool_update_note, {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}),
    ("add_note", "Leave a note on the timeline",
     "Add a note at time t (seconds) that the user sees in the Notes panel and on the ruler, marked as coming from an agent. "
     "Use it to flag something you noticed but did not change, or a question for the user.",
     schema_of({"t": {"type": "number", "description": "Timeline time in seconds."},
                "text": {"type": "string", "description": "The note."}}, ["t", "text"]),
     tool_add_note, {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}),
    ("list_fonts", "List usable fonts",
     "Font ids for text layers' `font` (project, bundled, and with query/source also system fonts).",
     schema_of({"query": {"type": "string", "description": "Filter by family/style/id substring, e.g. 'gill'."},
                "source": {"type": "string", "enum": ["project", "bundled", "system"], "description": "Only this source."}}),
     tool_list_fonts, {"readOnlyHint": True}),
    ("list_media", "List media files",
     "Media files inside the project directory (recursive) with kind, size, duration and pixel size, so start/end times match reality.",
     schema_of({}), tool_list_media, {"readOnlyHint": True}),
    ("import_file", "Import a file into the project",
     "Copy a file from anywhere on disk into the project (video/image/audio to media/, ttf/otf/ttc to fonts/), same as the editor's import. "
     "Returns the relative path to use as `src`, or the new font ids.",
     schema_of({"path": {"type": "string", "description": "Absolute path of the file."}}, ["path"]),
     tool_import_file, {"readOnlyHint": False, "idempotentHint": False, "openWorldHint": False}),
    ("transcribe", "Speech to text with word timestamps",
     "Local speech-to-text (Whisper via mlx-whisper on Apple Silicon or faster-whisper; optional install, nothing leaves the machine). "
     "Give layer_id (a video/audio layer: only its visible range is transcribed and word times come back in TIMELINE seconds) or path "
     "(file in the project or anywhere on disk; times are source seconds + offset). First run downloads the model. "
     "Cached in .kurgu/transcripts. Then use make_captions, or cut/title from the word times.",
     schema_of({"layer_id": {"type": "string", "description": "Video or audio layer id."},
                "path": {"type": "string", "description": "Media file path (project-relative or absolute) instead of a layer."},
                "model": {"type": "string", "description": "tiny | base | small (default) | medium | large-v3 | large-v3-turbo."},
                "language": {"type": "string", "description": "ISO code such as en, tr; omit to auto-detect."},
                "offset": {"type": "number", "description": "path mode only: seconds added to every time (where the file starts on the timeline)."},
                "words": {"type": "boolean", "description": "Include the word list (default true)."}}),
     tool_transcribe, {"readOnlyHint": True, "openWorldHint": False}),
    ("make_captions", "Add word-timed caption layers",
     "Transcribe (cached) and add one `text` layer per caption chunk to the group \"Captions\" (ids cap_1, cap_2, ...; front-most), "
     "through the same path as edit_project (validated, backup, changes.md [agent], live editor reload, undoable). Re-running replaces "
     "the old cap_* layers unless replace=false. Defaults follow the sequence aspect: vertical = 3 words, ~20 chars, size 7.8% of width, "
     "y at 66% of height (clear of TikTok/Reels/Shorts UI); 16:9 = 7 words, 42 chars, size 3.3% of width, y at 86%. "
     "Look with render_frame afterwards; restyle with edit_project (update the cap_* layers).",
     schema_of({"layer_id": {"type": "string", "description": "Video or audio layer id (captions land on the timeline where it plays)."},
                "path": {"type": "string", "description": "Media file instead of a layer (use offset for its timeline start)."},
                "offset": {"type": "number", "description": "path mode only: timeline second where the file starts."},
                "model": {"type": "string"}, "language": {"type": "string"},
                "max_words_per_line": {"type": "integer", "description": "Words per caption (default 3 vertical, 7 landscape)."},
                "max_chars": {"type": "integer", "description": "Max characters per caption (default 20 vertical, 42 landscape)."},
                "font": {"type": "string", "description": "Font id (default inter-bold); list_fonts."},
                "size": {"type": "number", "description": "Font size px."},
                "y": {"type": "number", "description": "Vertical centre: 0-1 = fraction of height (0.66 = 66%), above 1 = pixels."},
                "uppercase": {"type": "boolean", "description": "UPPERCASE captions (Turkish i/I handled)."},
                "color": {"type": "string", "description": "Text colour (default #ffffff)."},
                "replace": {"type": "boolean", "description": "Replace existing cap_* layers (default true); false appends new ids."}}),
     tool_make_captions, {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}),
]
TOOL_BY_NAME = {t[0]: t for t in TOOLS}


def tool_list():
    return [{"name": n, "title": title, "description": desc, "inputSchema": sch, "annotations": ann}
            for n, title, desc, sch, _fn, ann in TOOLS]


# ---------------------------------------------------------------- JSON-RPC plumbing
OUT = sys.stdout        # the protocol channel; main() points sys.stdout at stderr so a stray print (Whisper libraries) cannot corrupt it


def send(msg):
    line = json.dumps(msg, separators=(",", ":"))
    with LOCK:
        OUT.write(line + "\n")
        OUT.flush()


def rpc_error(rid, code, message, data=None):
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": e}


def modern_meta(params):
    m = params.get("_meta") if isinstance(params, dict) else None
    return m.get(PV_KEY) if isinstance(m, dict) else None


def finish(result, modern):
    if modern:
        result = dict(result, resultType="complete")
        result["_meta"] = dict(result.get("_meta") or {}, **{SI_KEY: {"name": SERVER_INFO["name"], "version": SERVER_INFO["version"]}})
    return result


def run_tool(rid, params, modern):
    name = params.get("name")
    entry = TOOL_BY_NAME.get(name)
    if entry is None:
        return rpc_error(rid, -32602, f"Unknown tool: {name}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return rpc_error(rid, -32602, "arguments must be an object")
    token = (params.get("_meta") or {}).get("progressToken") if isinstance(params.get("_meta"), dict) else None
    cancel = threading.Event()
    CANCELS[rid] = cancel

    def progress(p, total, message):
        if token is not None:
            send({"jsonrpc": "2.0", "method": "notifications/progress",
                  "params": {"progressToken": token, "progress": p, "total": total, "message": message}})

    ctx = {"progress": progress, "cancel": cancel}
    try:
        content, is_err = entry[4](args, ctx), False
    except ToolError as e:
        content, is_err = [text(str(e))], True
    except Exception as e:                                  # a bug: tell the agent, keep serving
        log("tool crashed:", name, repr(e))
        content, is_err = [text(f"Internal error in {name}: {type(e).__name__}: {e}")], True
    finally:
        CANCELS.pop(rid, None)
    if cancel.is_set():
        return None                                         # cancelled: no response
    res = {"content": content}
    if is_err:
        res["isError"] = True
    return {"jsonrpc": "2.0", "id": rid, "result": finish(res, modern)}


def handle(msg):
    """Return a response dict, or None (notification / async tools/call)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    is_req = "id" in msg and method is not None
    if method is None:
        return None                                         # a response from the client: we send no requests
    if not is_req:
        if method == "notifications/cancelled":
            ev = CANCELS.get(params.get("requestId"))
            if ev:
                ev.set()
                log("cancel requested for", params.get("requestId"))
        return None
    pv = modern_meta(params)
    modern = pv in MODERN_VERSIONS
    if pv is not None and pv not in MODERN_VERSIONS and pv not in LEGACY_VERSIONS:
        return rpc_error(rid, -32022, "Unsupported protocol version", {"supported": MODERN_VERSIONS, "requested": pv})
    if method == "initialize":
        want = params.get("protocolVersion")
        ver = want if want in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                                                       "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}}
    if method == "server/discover":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "resultType": "complete", "supportedVersions": MODERN_VERSIONS, "capabilities": {"tools": {}},
            "_meta": {SI_KEY: {"name": SERVER_INFO["name"], "version": SERVER_INFO["version"]}},
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": finish({}, modern)}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": finish({"tools": tool_list()}, modern)}
    if method == "tools/call":
        if not isinstance(params, dict):
            return rpc_error(rid, -32602, "Invalid params")
        return ("async", rid, params, modern)
    return rpc_error(rid, -32601, f"Method not found: {method}")


def dispatch(line):
    try:
        msg = json.loads(line)
    except ValueError:
        send(rpc_error(None, -32700, "Parse error"))
        return
    if isinstance(msg, list):                               # JSON-RPC batch (older revisions)
        out = []
        for m in msg:
            r = handle(m)
            if isinstance(r, tuple):
                r = run_tool(r[1], r[2], r[3])
            if r is not None:
                out.append(r)
        if out:
            send(out)
        return
    r = handle(msg)
    if isinstance(r, tuple):
        _, rid, params, modern = r

        def work():
            try:
                res = run_tool(rid, params, modern)
                if res is not None:
                    send(res)
            except Exception as e:
                log("dispatch failed:", repr(e))
                send(rpc_error(rid, -32603, f"Internal error: {e}"))
        threading.Thread(target=work, daemon=True).start()
    elif r is not None:
        send(r)


def main():
    for _s in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page; paths may hold emoji / Turkish letters
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    global OUT
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, ValueError):
        pass
    OUT, sys.stdout = sys.stdout, sys.stderr
    log(f"ready (stdio), default project dir: {os.environ.get('KURGU_PROJECT_DIR') or os.getcwd()}")
    stdin = sys.stdin.buffer
    while True:
        raw = stdin.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").strip()
        if line:
            dispatch(line)
    for ev in list(CANCELS.values()):
        ev.set()
    for p in list(PROCS):
        try:
            p.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()
