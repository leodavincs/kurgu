"""Where Kurgu keeps things: bundled data (web/, fonts/, skills/) and the per-user cache/config folders.

* From a git checkout the data sits next to the modules.
* From a wheel (pipx / uvx / pip) setup.py ships it under <prefix>/share/kurgu.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))


def data_root():
    """Folder that holds web/, fonts/, skills/ and .claude-plugin/."""
    if os.path.isdir(os.path.join(HERE, "web")):
        return HERE
    import sysconfig
    for base in (sys.prefix, sysconfig.get_path("data"), getattr(sys, "base_prefix", sys.prefix)):
        d = os.path.join(base, "share", "kurgu")
        if os.path.isdir(os.path.join(d, "web")):
            return d
    return HERE


def _user_dir(kind):
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Caches" if kind == "cache" else "Application Support", "kurgu")
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(base, "kurgu", "Cache" if kind == "cache" else "Config")
    var, fallback = (("XDG_CACHE_HOME", ".cache") if kind == "cache" else ("XDG_CONFIG_HOME", ".config"))
    return os.path.join(os.environ.get(var) or os.path.join(home, fallback), "kurgu")


def cache_dir():
    """Per-user cache folder (KURGU_CACHE overrides it). Created on demand."""
    d = os.environ.get("KURGU_CACHE") or _user_dir("cache")
    os.makedirs(d, exist_ok=True)
    return d


def config_dir():
    """Per-user config folder (KURGU_CONFIG overrides it). Not created.
    ~/.config/kurgu on macOS and Linux (XDG_CONFIG_HOME honoured), %APPDATA%\\kurgu on Windows."""
    if os.environ.get("KURGU_CONFIG"):
        return os.environ["KURGU_CONFIG"]
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"), "kurgu")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "kurgu")
