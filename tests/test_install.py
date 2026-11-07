"""Install story: ffmpeg_fetch (no network), media_tools picking up the managed folder, doctor, and the install scripts (static)."""
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import urllib.error
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import doctor  # noqa: E402
import ffmpeg_fetch  # noqa: E402
import media_tools  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    media_tools.reset()
    yield
    media_tools.reset()


def _zip_bytes(members):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return b.getvalue()


def _tar_bytes(members):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:xz") as t:
        for n, data in members.items():
            ti = tarfile.TarInfo(n)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return b.getvalue()


def _fake_net(blobs, bad_hash=False, fail=None):
    """(download, fetch_text) pair serving `blobs` {url: bytes}; checksums are derived from the bytes."""
    def download(url, dest, progress=None):
        if fail:
            raise fail
        with open(dest, "wb") as f:
            f.write(blobs[url])
        return url

    def fetch_text(url):
        if fail:
            raise fail
        if url.endswith("checksums.sha256"):
            return "\n".join(f"{'0' * 64 if bad_hash else hashlib.sha256(d).hexdigest()}  {u.rsplit('/', 1)[-1]}" for u, d in blobs.items())
        base = url[:-len(".sha256")]
        return ("0" * 64 if bad_hash else hashlib.sha256(blobs[base]).hexdigest()) + "  " + base.rsplit("/", 1)[-1] + "\n"
    return download, fetch_text


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("KURGU_DATA", str(tmp_path / "Kurgu data ışğ"))
    monkeypatch.setattr(ffmpeg_fetch, "verify", lambda p: "ffmpeg version fake")
    return tmp_path / "Kurgu data ışğ"


# ------------------------------------------------------------------ OS mapping
@pytest.mark.parametrize("system,machine,needle,kind,n", [
    ("Windows", "AMD64", "win64-gpl.zip", "zip", 1),
    ("Windows", "ARM64", "win64-gpl.zip", "zip", 1),
    ("Linux", "x86_64", "linux64-gpl.tar.xz", "tar.xz", 1),
    ("Linux", "aarch64", "linuxarm64-gpl.tar.xz", "tar.xz", 1),
    ("Darwin", "arm64", "macos/arm64/release/ffmpeg.zip", "zip", 2),
    ("Darwin", "x86_64", "macos/amd64/snapshot/ffmpeg.zip", "zip", 2),
])
def test_plan_per_os(system, machine, needle, kind, n):
    p = ffmpeg_fetch.plan(system, machine)
    assert len(p["archives"]) == n and p["archives"][0]["kind"] == kind
    assert needle in p["archives"][0]["url"]
    assert p["archives"][0]["url"].startswith("https://")


def test_plan_unsupported():
    with pytest.raises(ffmpeg_fetch.FetchError):
        ffmpeg_fetch.plan("Linux", "riscv64")
    with pytest.raises(ffmpeg_fetch.FetchError):
        ffmpeg_fetch.plan("Plan9", "x86_64")


def test_data_dir_per_os(monkeypatch):
    monkeypatch.delenv("KURGU_DATA", raising=False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", "/home/u") if p.startswith("~") else p)
    monkeypatch.setenv("LOCALAPPDATA", "/lad")
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg")
    for system, tail in (("Windows", ["lad", "Kurgu"]), ("Darwin", ["Library", "Application Support", "Kurgu"]), ("Linux", ["xdg", "kurgu"])):
        monkeypatch.setattr(ffmpeg_fetch.platform, "system", lambda s=system: s)
        parts = re.split(r"[\\/]+", ffmpeg_fetch.managed_dir())
        assert parts[-1] == "ffmpeg" and parts[-1 - len(tail):-1] == tail, (system, parts)


# ------------------------------------------------------------------ install
def test_install_zip_windows_layout(data):
    url = ffmpeg_fetch.plan("Windows", "AMD64")["archives"][0]["url"]
    blob = _zip_bytes({"ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe": b"A", "ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe": b"B",
                       "ffmpeg-master-latest-win64-gpl/bin/ffplay.exe": b"C", "ffmpeg-master-latest-win64-gpl/LICENSE.txt": b"x"})
    dl, tx = _fake_net({url: blob})
    r = ffmpeg_fetch.install(download=dl, fetch_text=tx, system="Windows", machine="AMD64", log=lambda *_: None)
    assert sorted(os.listdir(r["dir"])) == ["ffmpeg.exe", "ffprobe.exe"]
    assert open(os.path.join(r["dir"], "ffprobe.exe"), "rb").read() == b"B"
    assert os.path.dirname(r["dir"]) == str(data)
    assert not [d for d in os.listdir(data) if d.startswith("dl-")], "temp download folder must be cleaned up"


def test_install_tar_linux_and_runs(data, monkeypatch):
    monkeypatch.undo()                                    # use the real verify(): run the fake executables
    monkeypatch.setenv("KURGU_DATA", str(data))
    if sys.platform.startswith("win"):
        pytest.skip("fake shell-script executables")
    url = ffmpeg_fetch.plan("Linux", "x86_64")["archives"][0]["url"]
    script = b"#!/bin/sh\necho 'ffmpeg version 0-fake'\n"
    blob = _tar_bytes({"ffmpeg-master-latest-linux64-gpl/bin/ffmpeg": script, "ffmpeg-master-latest-linux64-gpl/bin/ffprobe": script})
    dl, tx = _fake_net({url: blob})
    r = ffmpeg_fetch.install(download=dl, fetch_text=tx, system="Linux", machine="x86_64", log=lambda *_: None)
    assert "0-fake" in r["versions"]["ffprobe"]
    assert os.stat(os.path.join(r["dir"], "ffmpeg")).st_mode & stat.S_IXUSR


def test_install_mac_separate_zips_and_sidecar_sha(data):
    p = ffmpeg_fetch.plan("Darwin", "arm64")
    blobs = {a["url"]: _zip_bytes({a["url"].rsplit("/", 1)[-1][:-4]: b"bin"}) for a in p["archives"]}
    dl, tx = _fake_net(blobs)
    r = ffmpeg_fetch.install(download=dl, fetch_text=tx, system="Darwin", machine="arm64", log=lambda *_: None)
    assert sorted(os.listdir(r["dir"])) == ["ffmpeg", "ffprobe"]


def test_checksum_mismatch_installs_nothing(data):
    url = ffmpeg_fetch.plan("Windows", "AMD64")["archives"][0]["url"]
    dl, tx = _fake_net({url: _zip_bytes({"b/ffmpeg.exe": b"A", "b/ffprobe.exe": b"B"})}, bad_hash=True)
    with pytest.raises(ffmpeg_fetch.FetchError, match="Checksum mismatch"):
        ffmpeg_fetch.install(download=dl, fetch_text=tx, system="Windows", machine="AMD64", log=lambda *_: None)
    assert not os.path.exists(ffmpeg_fetch.managed_dir())


def test_failed_verification_keeps_previous_copy(data, monkeypatch):
    old = data / "ffmpeg"
    old.mkdir(parents=True)
    (old / "ffmpeg").write_bytes(b"old")
    def boom(p):
        raise ffmpeg_fetch.FetchError("ffmpeg does not run on this machine")
    monkeypatch.setattr(ffmpeg_fetch, "verify", boom)
    url = ffmpeg_fetch.plan("Linux", "x86_64")["archives"][0]["url"]
    dl, tx = _fake_net({url: _tar_bytes({"x/bin/ffmpeg": b"n", "x/bin/ffprobe": b"n"})})
    with pytest.raises(ffmpeg_fetch.FetchError, match="does not run"):
        ffmpeg_fetch.install(download=dl, fetch_text=tx, system="Linux", machine="x86_64", log=lambda *_: None)
    assert (old / "ffmpeg").read_bytes() == b"old"


def test_archive_missing_tool(data):
    url = ffmpeg_fetch.plan("Windows", "AMD64")["archives"][0]["url"]
    dl, tx = _fake_net({url: _zip_bytes({"b/ffmpeg.exe": b"A"})})
    with pytest.raises(ffmpeg_fetch.FetchError, match="ffprobe"):
        ffmpeg_fetch.install(download=dl, fetch_text=tx, system="Windows", machine="AMD64", log=lambda *_: None)


def test_archive_paths_cannot_escape(data):
    url = ffmpeg_fetch.plan("Windows", "AMD64")["archives"][0]["url"]
    dl, tx = _fake_net({url: _zip_bytes({"../../evil/ffmpeg.exe": b"A", "..\\..\\ffprobe.exe": b"B"})})
    ffmpeg_fetch.install(download=dl, fetch_text=tx, system="Windows", machine="AMD64", log=lambda *_: None)
    assert sorted(os.listdir(ffmpeg_fetch.managed_dir())) == ["ffmpeg.exe", "ffprobe.exe"]
    assert sorted(os.listdir(data)) == ["ffmpeg"]


def test_network_failure_message(data):
    dl, tx = _fake_net({}, fail=urllib.error.URLError("getaddrinfo failed"))
    with pytest.raises(ffmpeg_fetch.FetchError) as e:
        ffmpeg_fetch.install(download=dl, fetch_text=tx, system="Linux", machine="x86_64", log=lambda *_: None)
    msg = str(e.value)
    assert "HTTPS_PROXY" in msg and "KURGU_FFMPEG" in msg and str(data) in msg


def test_expected_sha_parsing():
    h = "a" * 64
    assert ffmpeg_fetch.expected_sha(f"{h}  other.zip\n{'b' * 64}  want.zip\n", "want.zip") == "b" * 64
    assert ffmpeg_fetch.expected_sha(h + "\n") == h
    with pytest.raises(ffmpeg_fetch.FetchError):
        ffmpeg_fetch.expected_sha(f"{h}  other.zip", "want.zip")


# ------------------------------------------------------------------ media_tools lookup order
def _bin(d, name):
    d.mkdir(parents=True, exist_ok=True)
    p = d / media_tools._exe(name)
    p.write_bytes(b"x")
    p.chmod(0o755)
    return str(p)


def test_media_tools_uses_managed_folder(data, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("KURGU_FFMPEG", raising=False)
    monkeypatch.setattr(media_tools, "_extra_dirs", lambda: [])
    assert media_tools.find("ffmpeg") is None
    media_tools.reset()
    managed = _bin(data / "ffmpeg", "ffmpeg")
    assert media_tools.find("ffmpeg") == managed


def test_media_tools_order_env_then_path_then_managed(data, tmp_path, monkeypatch):
    managed = _bin(data / "ffmpeg", "ffmpeg")
    on_path = _bin(tmp_path / "pathdir", "ffmpeg")
    monkeypatch.setenv("PATH", str(tmp_path / "pathdir"))
    monkeypatch.setattr(media_tools, "_extra_dirs", lambda: [])
    assert os.path.normcase(media_tools.find("ffmpeg")) == os.path.normcase(on_path) != os.path.normcase(managed)    # Windows: PATHEXT may return .EXE for .exe
    media_tools.reset()
    envp = _bin(tmp_path / "envdir", "ffmpeg")
    monkeypatch.setenv("KURGU_FFMPEG", envp)
    assert media_tools.find("ffmpeg") == envp


def test_missing_message_points_at_setup(monkeypatch):
    monkeypatch.setattr(media_tools.platform, "system", lambda: "Windows")
    assert "kurgu setup-ffmpeg" in media_tools.missing_message("ffmpeg")


# ------------------------------------------------------------------ doctor
def test_doctor_report_shape(tmp_path):
    rep = doctor.collect(str(tmp_path))
    for k in ("ok", "problems", "python", "packages", "tools", "project"):
        assert k in rep
    assert rep["project"]["writable_unicode"] is True and rep["project"]["free_gb"] > 0
    json.dumps(rep)


def test_doctor_missing_ffmpeg_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(media_tools, "find", lambda t: None)
    code = doctor.main([str(tmp_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and not out["ok"]
    assert any("kurgu setup-ffmpeg" in p for p in out["problems"])


def test_doctor_missing_project_folder(tmp_path):
    rep = doctor.collect(str(tmp_path / "nope"))
    assert not rep["ok"] and rep["project"]["exists"] is False


def test_kurgu_cli_routes_subcommands(monkeypatch):
    import kurgu_cli
    calls = []
    monkeypatch.setattr(doctor, "main", lambda a: calls.append(("doctor", a)) or 0)
    monkeypatch.setattr(ffmpeg_fetch, "main", lambda a: calls.append(("fetch", a)) or 0)
    for argv in (["doctor", "--json"], ["setup-ffmpeg", "--force"]):
        with pytest.raises(SystemExit):
            kurgu_cli.main(argv)
    assert calls == [("doctor", ["--json"]), ("fetch", ["--force"])]


# ------------------------------------------------------------------ install scripts (static: no Windows here)
def _read(name):
    with open(os.path.join(ROOT, name), "rb") as f:
        return f.read()


def test_install_sh_commands():
    t = _read("install.sh").decode()
    assert t.startswith("#!/bin/sh") and b"\r" not in t.encode()
    for needle in ("https://astral.sh/uv/install.sh", "uv tool install", "--python", "setup-ffmpeg", "doctor",
                   "Next step:  kurgu <your project folder>", "HTTPS_PROXY"):
        assert needle in t, needle
    assert t.index("install.sh | sh") < t.index("uv tool install") < t.index("setup-ffmpeg") < t.index('doctor ||')
    assert "sudo" not in t.replace("sudo apt install curl", "")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="needs a POSIX sh")
def test_install_sh_syntax():
    assert subprocess.run(["sh", "-n", os.path.join(ROOT, "install.sh")]).returncode == 0


def test_install_ps1_commands():
    raw = _read("install.ps1")
    assert raw.count(b"\r\n") == raw.count(b"\n"), "install.ps1 must use CRLF line endings"
    t = raw.decode()
    for needle in ("https://astral.sh/uv/install.ps1", "'tool', 'install'", "'--python'", "setup-ffmpeg", "doctor",
                   "Next step:  kurgu <your project folder>", "HTTPS_PROXY", "Tls12", "UTF8"):
        assert needle in t, needle
    assert t.index("uv/install.ps1") < t.index("'tool', 'install'") < t.index("setup-ffmpeg") < t.index("'doctor'")
    assert "RunAs" not in t and "-Verb" not in t   # no elevation


def test_gitattributes_keep_scripts_intact():
    t = _read(".gitattributes").decode()
    assert "*.ps1" in t and "eol=crlf" in t and "*.sh" in t


def test_readmes_document_one_liners():
    for name in ("README.md", "README.tr.md"):
        t = _read(name).decode()
        assert "raw.githubusercontent.com/leodavincs/kurgu/main/install.sh" in t, name
        assert "raw.githubusercontent.com/leodavincs/kurgu/main/install.ps1" in t, name
        assert "uv tool uninstall kurgu" in t, name


def test_entry_point_is_cli():
    tomllib = pytest.importorskip("tomllib")
    d = tomllib.loads(_read("pyproject.toml").decode())
    assert d["project"]["scripts"]["kurgu"] == "kurgu_cli:main"
    assert {"kurgu_cli", "doctor", "ffmpeg_fetch"} <= set(d["tool"]["setuptools"]["py-modules"])
