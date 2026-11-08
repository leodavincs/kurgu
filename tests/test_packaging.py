"""Every top-level module must be listed in pyproject's py-modules, or installed entry points crash with ModuleNotFoundError."""
import glob
import os

import pytest

tomllib = pytest.importorskip("tomllib")  # Python 3.11+

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_all_root_modules_are_packaged():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        mods = set(tomllib.load(f)["tool"]["setuptools"]["py-modules"])
    local = {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(ROOT, "*.py"))} - {"setup"}
    assert local <= mods, f"missing from py-modules: {sorted(local - mods)}"


def test_no_merge_conflict_markers_in_tracked_files():
    import re
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    bad = []
    for f in files:
        if not f.endswith((".py", ".toml", ".md", ".json", ".yml", ".js", ".html", ".cmd", ".css")) and f != "kurgu":
            continue
        try:
            text = open(os.path.join(root, f), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"^(<<<<<<< |>>>>>>> )", text, re.M) or re.search(r"^=======\r?$", text, re.M) and f.endswith((".py", ".toml")):
            bad.append(f)
    assert bad == []


def test_wake_guidance_names_detect_and_not_ffprobe():
    import kurgu_cli
    assert "Bash(kurgu detect:*)" in kurgu_cli.CLAUDE_TOOLS and "ffprobe" not in kurgu_cli.CLAUDE_TOOLS
    assert "kurgu detect" in kurgu_cli.AGENT_BLOCK and "kurgu detect" in kurgu_cli.WAKE_PROMPT
    assert len(kurgu_cli.AGENT_BLOCK.encode()) <= 2850 and len(kurgu_cli.WAKE_PROMPT) < 1400


def test_kurgu_cmd_has_unique_labels_resolving_gotos_and_never_searches_the_cwd():
    import collections
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw = open(os.path.join(root, "kurgu.cmd"), "rb").read()
    text = raw.decode("utf-8")
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    import re
    labels = [l.lower() for l in re.findall(r"(?m)^:(\w+)", text)]
    assert [k for k, v in collections.Counter(labels).items() if v > 1] == [], "duplicate labels: cmd.exe jumps to the first one"
    targets = {g.lower() for g in re.findall(r"(?i)goto (\w+)", text)} - {"eof"}
    assert targets <= set(labels), targets - set(labels)
    assert "NoDefaultCurrentDirectoryInExePath=1" in text and "where $PATH:python" in text and "where $PATH:py" in text
    assert not re.search(r"(?im)^\s*where (python|py)\b", text)               # a bare `where python` looks in the current folder first
