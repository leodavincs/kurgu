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
