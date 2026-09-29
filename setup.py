"""Data files (web/, fonts/, skills/, plugin manifest) ride along under <prefix>/share/kurgu; see kurgu_paths.py."""
import os

from setuptools import setup

DATA_DIRS = ["web", "fonts", "skills", ".claude-plugin"]
SKIP = {"__pycache__", ".DS_Store"}


def data_files():
    out = []
    for top in DATA_DIRS:
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames if d not in SKIP]
            files = [os.path.join(dirpath, f).replace(os.sep, "/") for f in filenames if f not in SKIP]
            if files:
                out.append(("share/kurgu/" + dirpath.replace(os.sep, "/"), files))
    return out


setup(data_files=data_files())
