"""Unit tests of the track view packing (web/js/trackpack.js) — run with node."""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web", "js")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_trackpack_node_checks(tmp_path):
    shutil.copy(os.path.join(WEB, "trackpack.js"), tmp_path / "trackpack.mjs")
    r = subprocess.run(["node", os.path.join(HERE, "trackpack_node_check.mjs"), str(tmp_path / "trackpack.mjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
