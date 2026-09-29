#!/usr/bin/env python3
"""Start a self-contained project from a template (or examples/showcase).

    python3 scripts/examples/new_project.py quote-stat-card ~/videos/my-card

Copies the template folder and every media file that lives outside it (shared templates/_media/...) into
<dest>/media/, rewriting `src` paths so the result does not depend on this repo.
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def new_project(template, dest):
    src_dir = template if os.path.isdir(template) else os.path.join(ROOT, "templates", template)
    if not os.path.isfile(os.path.join(src_dir, "project.json")):
        raise SystemExit(f"no such template: {template}")
    if os.path.exists(dest) and os.listdir(dest):
        raise SystemExit(f"{dest} is not empty")
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(src_dir):
        if name in ("README.md", "thumbnail.png") or name.startswith("."):
            continue
        a, b = os.path.join(src_dir, name), os.path.join(dest, name)
        shutil.copytree(a, b) if os.path.isdir(a) else shutil.copy2(a, b)
    pj = os.path.join(dest, "project.json")
    proj = json.load(open(pj, encoding="utf-8"))
    for l in proj["layers"]:
        s = l.get("src")
        if not s or os.path.isabs(s):
            continue
        full = os.path.normpath(os.path.join(src_dir, s))
        if os.path.commonpath([full, src_dir]) == src_dir:
            continue
        rel = os.path.join("media", os.path.basename(os.path.dirname(full)), os.path.basename(full))
        os.makedirs(os.path.join(dest, os.path.dirname(rel)), exist_ok=True)
        shutil.copy2(full, os.path.join(dest, rel))
        l["src"] = rel.replace(os.sep, "/")
    with open(pj, "w", encoding="utf-8") as f:
        f.write(json.dumps(proj, indent=2, ensure_ascii=False) + "\n")
    return dest


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    print("created", new_project(sys.argv[1], os.path.expanduser(sys.argv[2])))
