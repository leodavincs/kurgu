import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# keep the font cache out of the user's home during tests (subprocesses inherit this)
os.environ.setdefault("KURGU_CACHE", tempfile.mkdtemp(prefix="kurgu-test-cache-"))

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="kurgu-test-xdg-")   # never read the developer's own ~/.config/kurgu

EXAMPLE = os.path.join(ROOT, "examples", "basic")


@pytest.fixture()
def project_dir(tmp_path):
    """A private copy of examples/basic."""
    dst = tmp_path / "proj"
    shutil.copytree(EXAMPLE, dst, ignore=shutil.ignore_patterns(".kurgu", "output*.mp4"))
    return str(dst)
