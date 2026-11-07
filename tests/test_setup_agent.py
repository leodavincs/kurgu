"""kurgu setup-agent: Codex config.toml merge, project AGENTS.md section, CLI routing, Claude registration (fake runner)."""
import os
import subprocess
import sys

import pytest

import kurgu_cli as K

EXISTING = '''# my codex config
model = "gpt-5"   # keep me

[mcp_servers.other]
command = "other-mcp"   # theirs

[mcp_servers.kurgu]
command = "old"
args = [
  "a",
  "b",
]
env_vars = ["X"]
tool_timeout_sec = 5

[mcp_servers.kurgu.env]
FOO = "1"

[projects."/x"]
trust_level = "trusted"
'''


def test_merge_into_empty_and_new():
    out = K.merge_codex_config("", "/bin/kurgu-mcp", [])
    assert out.startswith("[mcp_servers.kurgu]\n") and 'command = "/bin/kurgu-mcp"' in out and "tool_timeout_sec = 3600" in out
    out2 = K.merge_codex_config('model = "x" # c\n', "k", ["m.py"])
    assert out2.startswith('model = "x" # c\n\n[mcp_servers.kurgu]') and out2.endswith("\n")


def test_merge_existing_keeps_everything_else():
    out = K.merge_codex_config(EXISTING, "/k/kurgu-mcp", [])
    for keep in ("# my codex config", 'model = "gpt-5"   # keep me', 'command = "other-mcp"   # theirs', 'env_vars = ["X"]',
                 "[mcp_servers.kurgu.env]", 'FOO = "1"', '[projects."/x"]', 'trust_level = "trusted"'):
        assert keep in out
    assert 'command = "old"' not in out and '"a",' not in out and "tool_timeout_sec = 5" not in out
    assert out.count("[mcp_servers.kurgu]") == 1 and out.count("tool_timeout_sec") == 1
    assert K.merge_codex_config(out, "/k/kurgu-mcp", []) == out          # idempotent
    try:
        import tomllib
    except ImportError:
        return
    d = tomllib.loads(out)
    assert d["mcp_servers"]["kurgu"]["command"] == "/k/kurgu-mcp" and d["mcp_servers"]["kurgu"]["env"] == {"FOO": "1"}
    assert d["mcp_servers"]["other"]["command"] == "other-mcp" and d["model"] == "gpt-5"


def test_block_at_end_of_file_idempotent():
    t = '[mcp_servers.kurgu]\ncommand = "x"\n'
    once = K.merge_codex_config(t, "y", [])
    assert once.endswith("\n") and K.merge_codex_config(once, "y", []) == once


def test_marked_block_insert_update_and_keep_user_text():
    base = "# Project\n\nMy rules.\n"
    a = K.merge_marked_block(base, K.AGENT_BLOCK)
    assert a.startswith(base) and a.count(K.END) == 1 and "notes_wait" in a
    assert K.merge_marked_block(a, K.AGENT_BLOCK) == a
    edited = a.replace("## Kurgu", "## Kurgu OLD") + "\nafter\n"
    b = K.merge_marked_block(edited, K.AGENT_BLOCK)
    assert b.count("<!-- kurgu:begin") == 1 and "OLD" not in b and b.endswith("after\n") and "My rules." in b
    assert len(K.AGENT_BLOCK.encode()) < 3000                           # Codex reads 32 KiB of AGENTS.md in total


def test_skill_is_valid_codex_skill():
    assert K.SKILL_CODEX.startswith("---\nname: kurgu\ndescription: ") and "\n---\n" in K.SKILL_CODEX[3:]
    assert "Watch + note loop" in K.SKILL_CODEX


def run_cli(args, home, env_extra=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PATH=os.path.dirname(sys.executable) + os.pathsep + "/usr/bin:/bin")
    env.pop("CODEX_HOME", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, os.path.join(K.os.path.dirname(os.path.abspath(K.__file__)), "kurgu_cli.py"), "setup-agent"] + args,
                          capture_output=True, text=True, env=env, timeout=60)


def test_cli_dry_run_writes_nothing(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    r = run_cli(["--codex", "--project", str(proj), "--dry-run"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "would write" in r.stdout
    assert not (tmp_path / ".codex").exists() and not (tmp_path / ".agents").exists() and not (proj / "AGENTS.md").exists()


def test_cli_codex_real_run_backup_and_idempotent(tmp_path):
    cfg = tmp_path / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(EXISTING, encoding="utf-8")
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("# mine\n", encoding="utf-8")
    r = run_cli(["--codex", "--project", str(proj)], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    baks = [p for p in os.listdir(cfg.parent) if p.startswith("config.toml.kurgu-bak-")]
    assert len(baks) == 1 and (cfg.parent / baks[0]).read_text(encoding="utf-8") == EXISTING
    assert "tool_timeout_sec = 3600" in cfg.read_text(encoding="utf-8") and "[mcp_servers.other]" in cfg.read_text(encoding="utf-8")
    assert (tmp_path / ".agents" / "skills" / "kurgu" / "SKILL.md").read_text(encoding="utf-8") == K.SKILL_CODEX
    assert (proj / "AGENTS.md").read_text(encoding="utf-8").startswith("# mine\n")
    before = (cfg.read_text(encoding="utf-8"), (proj / "AGENTS.md").read_text(encoding="utf-8"))
    r2 = run_cli(["--codex", "--project", str(proj)], tmp_path)
    assert r2.returncode == 0 and "already up to date" in r2.stdout
    assert (cfg.read_text(encoding="utf-8"), (proj / "AGENTS.md").read_text(encoding="utf-8")) == before
    assert len([p for p in os.listdir(cfg.parent) if p.startswith("config.toml.kurgu-bak-")]) == 1      # no new backup when nothing changed


def test_codex_home_env_respected(tmp_path):
    r = run_cli(["--codex"], tmp_path, {"CODEX_HOME": str(tmp_path / "ch")})
    assert r.returncode == 0 and (tmp_path / "ch" / "config.toml").exists() and not (tmp_path / ".codex").exists()


def test_claude_registration_uses_runner(monkeypatch, capsys):
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1 if cmd[:3] == ["claude", "mcp", "get"] else 0, "", "")
    monkeypatch.setattr(K.shutil, "which", lambda n: "/x/claude" if n == "claude" else None)
    assert K.setup_claude(False, "/k/kurgu-mcp", [], runner=fake) == 0
    assert calls[1][:5] == ["claude", "mcp", "add", "--scope", "user"] and calls[1][-1] == "/k/kurgu-mcp"
    calls.clear()
    assert K.setup_claude(True, "/k/kurgu-mcp", [], runner=fake) == 0 and calls == []     # dry run runs nothing


def test_routing_and_codex_only_skips_claude(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(K, "setup_agent_main", lambda a: seen.append(a) or 0)
    with pytest.raises(SystemExit):
        K.main(["setup-agent", "--codex", "--dry-run"])
    assert seen == [["--codex", "--dry-run"]]
    monkeypatch.undo()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(K, "setup_claude", lambda *a, **k: pytest.fail("claude touched"))
    assert K.setup_agent_main(["--codex", "--dry-run"]) == 0
