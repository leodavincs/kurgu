#!/usr/bin/env python3
"""Check Kurgu's dependencies and print install hints. Exit 0 = all good, 1 = something missing."""
import importlib
import platform
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import media_tools  # noqa: E402

OS = {"Darwin": "mac", "Windows": "win"}.get(platform.system(), "linux")

HINTS = {
    "python": {"mac": "brew install python", "linux": "sudo apt install python3 python3-venv",
               "win": "winget install Python.Python.3.12"},
    "ffmpeg": {"mac": "brew install ffmpeg", "linux": "sudo apt install ffmpeg",
               "win": "winget install Gyan.FFmpeg"},
}
# Modern Python installs refuse a bare `pip install` (PEP 668), so the hint is a virtualenv or pipx.
PIP = ("pipx install git+https://github.com/leodavincs/kurgu   (or: python -m venv .venv, activate it, "
       "then: python -m pip install -r requirements.txt)")


def main() -> int:
    problems = []
    v = sys.version_info
    ok = v >= (3, 10)
    print(f"[{'ok' if ok else 'MISSING'}] python {v.major}.{v.minor}.{v.micro} (need >= 3.10)")
    if not ok:
        problems.append(("python", HINTS["python"][OS]))

    missing_pip = False
    for label, mod in (("numpy", "numpy"), ("Pillow", "PIL"), ("fontTools", "fontTools")):
        try:
            m = importlib.import_module(mod)
            print(f"[ok] {label} {getattr(m, '__version__', '')}".rstrip())
        except Exception:
            print(f"[MISSING] {label}")
            missing_pip = True
    if missing_pip:
        problems.append(("python packages", PIP))

    for tool in ("ffmpeg", "ffprobe"):
        path = media_tools.find(tool)
        print(f"[{'ok' if path else 'MISSING'}] {tool}" + (f" ({path})" if path else ""))
        if not path:
            problems.append((tool, HINTS["ffmpeg"][OS] + f"  (or set {media_tools.ENV[tool]}=/full/path/to/{tool})"))

    if media_tools.find("ffmpeg"):
        hw = {k: v for k, v in media_tools.hw_encoders().items() if v}
        print("[ok] hardware video encoders: " + (", ".join(hw.values()) if hw else "none (software libx264 is used)") + " (optional)")

    try:
        import transcribe
        av = transcribe.availability()
    except Exception:
        av = {"available": False, "hint": "pip install faster-whisper (inside the same virtualenv / pipx environment)"}
    if av["available"]:
        print(f"[ok] speech-to-text ({av['backend']}) (optional)")
    else:
        print("[optional] speech-to-text not installed (only needed for automatic captions): " + av["hint"])

    if not problems:
        print("\nAll dependencies present.")
        return 0
    print("\nInstall hints:")
    seen = set()
    for what, cmd in problems:
        if cmd not in seen:
            seen.add(cmd)
            print(f"  {what}: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
