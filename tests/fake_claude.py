#!/usr/bin/env python3
"""A stand-in for the real `claude` binary (installed as `claude` on PATH by tests): stream-json with a session_id, honours
--resume, appends {argv, cwd, resumed} to $FAKE_CLAUDE_LOG. A resume id starting with "gone" fails like the real CLI."""
import json
import os
import sys
import uuid

argv = sys.argv[1:]
resume = argv[argv.index("--resume") + 1] if "--resume" in argv else None
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n")
sid = resume or "sess-" + uuid.uuid4().hex[:6]


def out(o):
    print(json.dumps(dict(o, session_id=sid)), flush=True)


if resume and resume.startswith("gone"):
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "", "errors": ["No conversation found with session ID: " + resume]})
    sys.exit(0)
out({"type": "system", "subtype": "init"})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}})
out({"type": "result", "subtype": "success", "is_error": False, "result": ("resumed " if resume else "fresh ") + argv[1][:40]})
