#!/usr/bin/env python3
"""A stand-in for `claude -p ... --continue --fork-session --output-format json` (brief generation).
Behaviour by marker files in the cwd: `nosession` -> "No conversation found to continue" (exit 1); `hang` -> sleeps;
`nobrief` -> NO_BRIEF; `codexish` n/a. Otherwise prints a JSON result with a brief and records its argv in argv.json."""
import json
import os
import sys
import time

json.dump(sys.argv[1:], open("argv.json", "w"))
if os.path.exists("nosession"):
    print("No conversation found to continue", file=sys.stderr)
    sys.exit(1)
if os.path.exists("hang"):
    time.sleep(60)
if os.path.exists("nobrief"):
    print(json.dumps({"type": "result", "is_error": False, "result": "NO_BRIEF"}))
    sys.exit(0)
lang = os.environ.get("KURGU_BRIEF_LANG", "en")
print(json.dumps({"type": "result", "is_error": False,
                  "result": "- 12 s space teaser (" + lang + ")\n- Minimal white titles\n- Hard rule: no orange"}))
