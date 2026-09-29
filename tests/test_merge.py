"""Unit tests of the save-conflict merge (web/js/merge.js) — run with node, like the animation vectors."""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_merge_js_three_way_cases(tmp_path):
    shutil.copy(os.path.join(ROOT, "web", "js", "merge.js"), tmp_path / "merge.mjs")
    r = subprocess.run(["node", os.path.join(HERE, "merge_node_check.mjs"), str(tmp_path / "merge.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert '"n":12' in r.stdout
