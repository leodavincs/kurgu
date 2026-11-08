import os
import sys

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
