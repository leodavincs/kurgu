import os
import sys

import pytest

import kurgu_paths


def test_cache_dir_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KURGU_CACHE", str(tmp_path / "c"))
    d = kurgu_paths.cache_dir()
    assert d == str(tmp_path / "c") and os.path.isdir(d)


def test_platform_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("KURGU_CACHE", raising=False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert kurgu_paths._user_dir("cache") == str(tmp_path / "Library" / "Caches" / "kurgu")
    monkeypatch.setattr(sys, "platform", "linux")
    assert kurgu_paths._user_dir("cache") == str(tmp_path / "xdg" / "kurgu")
    monkeypatch.setattr(sys, "platform", "win32")
    assert kurgu_paths._user_dir("cache") == str(tmp_path / "local" / "kurgu" / "Cache")


def test_data_root_has_bundled_assets():
    root = kurgu_paths.data_root()
    assert os.path.isfile(os.path.join(root, "web", "index.html"))
    assert os.path.isdir(os.path.join(root, "fonts"))


def test_kurgu_config_may_name_the_folder_or_the_config_file(tmp_path, monkeypatch):
    import json
    import kurgu_paths
    folder = tmp_path / "conf"
    folder.mkdir()
    monkeypatch.setenv("KURGU_CONFIG", str(folder))
    assert kurgu_paths.config_dir() == str(folder) and kurgu_paths.config_file() == str(folder / "config.json")
    f = tmp_path / "my-kurgu.json"
    f.write_text(json.dumps({"on_send": ["echo", "x"]}))
    monkeypatch.setenv("KURGU_CONFIG", str(f))
    assert kurgu_paths.config_file() == str(f) and kurgu_paths.config_dir() == str(tmp_path)
    import agent_notes
    assert agent_notes.on_send_command() == ["echo", "x"]


def test_replace_retry_waits_out_a_windows_reader(tmp_path, monkeypatch):
    import kurgu_paths
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("new")
    dst.write_text("old")
    real, n = os.replace, []

    def busy(a, b):                                   # the first two attempts meet a reader holding the file (Windows)
        n.append(1)
        if len(n) < 3:
            raise PermissionError("in use")
        return real(a, b)
    monkeypatch.setattr(os, "replace", busy)
    kurgu_paths.replace_retry(str(src), str(dst))
    assert dst.read_text() == "new" and len(n) == 3
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("in use")))
    with pytest.raises(PermissionError):
        kurgu_paths.replace_retry(str(src), str(dst), seconds=0.1)
