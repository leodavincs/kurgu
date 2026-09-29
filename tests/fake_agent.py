#!/usr/bin/env python3
"""A stand-in for `claude -p ... --output-format stream-json`: prints realistic stream-json lines and (like the real agent
through MCP) performs one edit via mcp_server.tool_edit_project. Behaviour follows the prompt (argv[-1] / $KURGU_AI_PROMPT):
  contains "error"  -> is_error result, exit 1
  contains "login"  -> not-logged-in result, exit 1
  contains "hang"   -> emits one event, then sleeps (cancel / timeout tests)
  contains "noedit" -> answers without touching the project
  contains "twice"  -> two edit batches (like "fix once")
  otherwise         -> reads, edits the first selected layer's opacity, looks at a frame, answers
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp_server  # noqa: E402

prompt = os.environ.get("KURGU_AI_PROMPT") or sys.argv[-1]
d = os.environ["KURGU_PROJECT_DIR"]
scope = json.loads(os.environ.get("KURGU_AI_SCOPE") or "{}")


def out(o):
    print(json.dumps(o), flush=True)


def tool(name, inp):
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_" + name, "name": "mcp__kurgu__" + name, "input": inp}]}})
    out({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_" + name, "content": "ok"}]}})


def edit(opacity):
    target = (scope.get("selected") or ["k_buyuk"])[0]
    mcp_server.tool_edit_project({"project_dir": d, "update": [{"id": target, "set": {"opacity": opacity}}], "summary": "fake edit"}, None)
    tool("edit_project", {"update": [{"id": target, "set": {"opacity": opacity}}], "summary": "fake edit"})


out({"type": "system", "subtype": "init", "session_id": "fake", "tools": []})
out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}]}})
if "hang" in prompt:
    tool("get_state", {})
    time.sleep(60)
    sys.exit(0)
if "login" in prompt:
    out({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"})
    sys.exit(1)
if "error" in prompt:
    print("something went wrong", file=sys.stderr)
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "kaboom"})
    sys.exit(1)
tool("get_state", {})
tool("get_project", {"summary": True})
if "noedit" not in prompt:
    edit(0.5)
    if "twice" in prompt:
        edit(0.4)
    tool("render_frame", {"t": 2.5})
out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done: I dimmed it."}]}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: I dimmed it.", "duration_ms": 12})
