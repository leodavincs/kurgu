"""What a Codex run woken by Kurgu may run: the generated execpolicy rules, the dedicated CODEX_HOME, the escape hatch (user config only)."""
import json
import os
import shutil
import subprocess
import sys

import pytest

import codex_policy
import kurgu_cli
from test_wake_hook import PY, ROOT, fake_agent, wake_env_for

HAVE_CODEX = shutil.which("codex") is not None


@pytest.fixture()
def homes(tmp_path, monkeypatch):
    """A Kurgu config folder, and a 'real' Codex home with a login, a config holding secrets-ish things, and a user rules file."""
    kconf = tmp_path / "kconf"
    kconf.mkdir()
    real = tmp_path / "realcodex"
    (real / "rules").mkdir(parents=True)
    (real / "auth.json").write_text('{"tokens": "SECRET"}')
    (real / "rules" / "default.rules").write_text('prefix_rule(pattern=["curl"], decision="allow")\n')
    (real / "config.toml").write_text('notify = ["/some/app"]\nmodel = "gpt-x"\nmodel_reasoning_effort = "low"\n\n[mcp_servers.thing]\nenv = { TOKEN = "SECRET" }\n\n[projects."/p"]\ntrust_level = "trusted"\n')
    monkeypatch.setenv("KURGU_CONFIG", str(kconf))
    monkeypatch.setenv("CODEX_HOME", str(real))
    return kconf, real


def set_user(kconf, **kv):
    (kconf / "config.json").write_text(json.dumps(kv))


# ------------------------------------------------------------------ the rules text
def test_rules_are_forbidden_only_and_cover_the_dangerous_commands():
    text = codex_policy.rules_text()
    assert 'decision="allow"' not in text and 'decision="prompt"' not in text                       # allow would run a command OUTSIDE the sandbox
    assert text.count("prefix_rule(") == len(codex_policy.patterns()) and text.count('decision="forbidden"') == text.count("prefix_rule(")
    for argv in (["rm", "x"], ["/bin/rm", "x"], ["curl", "https://example.com"], ["wget", "x"], ["python3", "-c", "1"], ["node", "x.js"], ["npm", "i"], ["pip", "install", "x"],
                 ["git", "push"], ["git", "push", "origin"], ["bash", "-c", "x"], ["/bin/zsh", "-lc", "cat a > b"], ["sh", "-c", "x"], ["sed", "-i", "s/a/b/", "f"],
                 ["rg", "--pre", "cmd", "x"], ["env", "x=1", "rm"], ["find", ".", "-delete"], ["ffmpeg", "-i", "x"], ["kurgu", "wake", "codex", "d"], ["kurgu", "setup-agent"]):
        assert codex_policy.is_forbidden(argv), argv


def test_the_commands_the_wake_prompt_needs_are_not_refused():
    for argv in codex_policy.EXPECTED_RUNNABLE:
        assert not codex_policy.is_forbidden(argv), argv
    for sub in ("notes", "frame", "strip", "rerender"):                                              # the four Kurgu commands of the task
        assert any(a[:2] == ["kurgu", sub] for a in codex_policy.EXPECTED_RUNNABLE)
    assert not codex_policy.is_forbidden(["kurgu", "notes", "wait", "d"]) and not codex_policy.is_forbidden(["sed", "-n", "1p", "f"])


def test_the_wake_prompt_stays_inside_the_policy():
    import re
    for cmd in re.findall(r"`(kurgu [^`]+|ffprobe[^`]*)`", kurgu_cli.WAKE_PROMPT):
        assert not codex_policy.is_forbidden(cmd.split()), cmd


@pytest.mark.skipif(not HAVE_CODEX, reason="no codex on PATH")
def test_python_mirror_agrees_with_codexs_own_matcher(tmp_path):
    rules = tmp_path / "k.rules"
    rules.write_text(codex_policy.rules_text())
    for argv in (["rm", "x"], ["/usr/bin/curl", "x"], ["/bin/zsh", "-lc", "x"], ["git", "push"], ["git", "status"], ["kurgu", "notes", "list", "d"], ["sed", "-i", "x"],
                 ["sed", "-n", "1p", "x"], ["ffprobe", "x"], ["rg", "-n", "x"], ["python3", "x"], ["jq", ".", "f"]):
        out = json.loads(subprocess.run(["codex", "execpolicy", "check", "--rules", str(rules), "--", *argv], capture_output=True, text=True, check=True).stdout)
        assert (out.get("decision") == "forbidden") == codex_policy.is_forbidden(argv), argv


# ------------------------------------------------------------------ the dedicated home
def test_wake_env_builds_a_separate_codex_home_without_touching_the_users(homes):
    kconf, real = homes
    before = {p: (real / p).read_bytes() for p in ("auth.json", "config.toml", "rules/default.rules")}
    env, msg = codex_policy.wake_env({"PATH": "x"})
    home = env["CODEX_HOME"]
    assert home == str(kconf / "codex-home") and home != str(real) and env["PATH"] == "x" and "rules on" in msg
    assert open(os.path.join(home, "rules", "kurgu.rules"), encoding="utf-8").read() == codex_policy.rules_text()
    cfg = open(os.path.join(home, "config.toml"), encoding="utf-8").read()
    assert 'model = "gpt-x"' in cfg and 'model_reasoning_effort = "low"' in cfg                       # model settings follow the user
    assert "SECRET" not in cfg and "notify" not in cfg and "mcp_servers" not in cfg and "trusted" not in cfg
    auth = os.path.join(home, "auth.json")
    assert os.path.realpath(auth) == os.path.realpath(real / "auth.json")                              # linked (or copied on Windows), never rewritten
    assert before == {p: (real / p).read_bytes() for p in before}                                      # the user's own files are untouched
    assert not os.path.exists(real / "rules" / "kurgu.rules")
    env2, _ = codex_policy.wake_env({})
    assert env2["CODEX_HOME"] == home                                                                  # idempotent


def test_sessions_are_shared_so_resume_finds_the_users_threads(homes):
    _, real = homes
    env, _ = codex_policy.wake_env({})
    link = os.path.join(env["CODEX_HOME"], "sessions")
    if not os.path.islink(link):
        pytest.skip("no symlinks here")
    assert os.path.realpath(link) == os.path.realpath(real / "sessions")
    (real / "sessions" / "t.jsonl").write_text("x")
    assert os.path.exists(os.path.join(link, "t.jsonl"))
    assert codex_policy.wake_env({})[0]["CODEX_HOME"] == env["CODEX_HOME"]                           # idempotent


def test_a_stale_rules_file_is_regenerated_and_a_project_cannot_edit_it_away(homes):
    kconf, _ = homes
    env, _ = codex_policy.wake_env({})
    rules = os.path.join(env["CODEX_HOME"], "rules", "kurgu.rules")
    open(rules, "w").write('prefix_rule(pattern=["rm"], decision="allow")\n')
    codex_policy.wake_env({})
    assert open(rules, encoding="utf-8").read() == codex_policy.rules_text()


def test_missing_login_refuses_instead_of_running_unrestricted(homes):
    _, real = homes
    os.remove(real / "auth.json")
    with pytest.raises(RuntimeError) as e:
        codex_policy.wake_env({})
    assert "codex_policy" in str(e.value) and "off" in str(e.value)


# ------------------------------------------------------------------ the escape hatch is the user's alone
def test_off_in_the_user_config_only(homes, tmp_path, monkeypatch, project_dir):
    kconf, _ = homes
    assert codex_policy.policy_off() is False
    set_user(kconf, codex_policy="off")
    assert codex_policy.policy_off() is True
    env, msg = codex_policy.wake_env({})
    assert env is None and "off" in msg
    set_user(kconf, codex_policy="on")
    assert codex_policy.policy_off() is False


def test_a_project_cannot_turn_the_policy_off(homes, tmp_path, monkeypatch, project_dir):
    kconf, _ = homes
    os.makedirs(os.path.join(project_dir, ".kurgu"), exist_ok=True)
    json.dump({"codex_policy": "off"}, open(os.path.join(project_dir, ".kurgu", "config.json"), "w"))
    json.dump({"codex_policy": "off"}, open(os.path.join(project_dir, "project.json.config"), "w"))
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("KURGU_PROJECT_DIR", project_dir)
    assert codex_policy.policy_off() is False
    env, _ = codex_policy.wake_env({})
    assert env is not None and env["CODEX_HOME"].endswith("codex-home")
    src = open(os.path.join(ROOT, "codex_policy.py"), encoding="utf-8").read()
    assert ".kurgu" not in src.replace("`", "")                                                       # the module never looks inside a project


# ------------------------------------------------------------------ `kurgu wake codex` and `setup-agent`
def logging_codex(tmp_path):
    bin_dir, log = fake_agent(tmp_path, "codex", "never-matches")
    impl = bin_dir / "codex_impl.py"
    impl.write_text(impl.read_text().replace("import sys, json\n", "import sys, json, os\n").replace("json.dumps(sys.argv[1:])", "json.dumps([os.environ.get('CODEX_HOME')] + sys.argv[1:])"))
    return bin_dir, log


def test_wake_codex_runs_under_the_policy_home_with_the_same_argv(tmp_path, project_dir):
    bin_dir, log = logging_codex(tmp_path)
    r = subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", "codex", project_dir, "3"], capture_output=True, text=True, env=wake_env_for(tmp_path, bin_dir), timeout=60)
    call = json.loads(open(log).read().splitlines()[0])
    assert r.returncode == 0 and call[0] == str(tmp_path / "kconf" / "codex-home") and "rules on" in r.stdout
    assert call[1:4] == ["exec", "resume", "--last"] and "approval_policy=never" in call and "sandbox_mode=workspace-write" in call
    assert os.path.isfile(tmp_path / "kconf" / "codex-home" / "rules" / "kurgu.rules")


def test_wake_codex_with_policy_off_keeps_the_users_home(tmp_path, project_dir):
    bin_dir, log = logging_codex(tmp_path)
    env = wake_env_for(tmp_path, bin_dir)
    (tmp_path / "kconf" / "config.json").write_text('{"codex_policy": "off"}')
    r = subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", "codex", project_dir], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and json.loads(open(log).read().splitlines()[0])[0] == env["CODEX_HOME"] and not (tmp_path / "kconf" / "codex-home").exists()


def test_wake_codex_does_not_start_without_a_linkable_login(tmp_path, project_dir):
    bin_dir, log = logging_codex(tmp_path)
    env = wake_env_for(tmp_path, bin_dir)
    os.remove(os.path.join(env["CODEX_HOME"], "auth.json"))
    r = subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", "codex", project_dir], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 78 and "codex not started (no_auth_file)" in r.stderr and not os.path.exists(log)


def test_wake_claude_is_not_affected(tmp_path, project_dir):
    bin_dir, log = fake_agent(tmp_path, "claude", "never-matches")
    r = subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "wake", "claude", project_dir], capture_output=True, text=True, env=wake_env_for(tmp_path, bin_dir), timeout=60)
    assert r.returncode == 0 and "rules" not in r.stdout and not (tmp_path / "kconf" / "codex-home").exists()


def test_setup_agent_dry_run_documents_the_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KURGU_CONFIG", str(tmp_path / "kc"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert kurgu_cli.setup_agent_main(["--codex", "--wake", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Command rules" in out and "codex_policy" in out and "off" in out and "workspace-write" in out
    assert not (tmp_path / "kc").exists()                                                              # dry run wrote nothing
    assert kurgu_cli.setup_agent_main(["--claude", "--wake", "--dry-run"]) == 0
    assert "Command rules" not in capsys.readouterr().out


def test_auth_problem_is_a_structured_status_checked_up_front(homes, capsys):
    kconf, real = homes
    assert codex_policy.auth_problem() is None
    os.remove(real / "auth.json")
    p = codex_policy.auth_problem()
    assert p.code == "no_auth_file" and "keyring" in str(p)
    with pytest.raises(codex_policy.PolicyProblem) as e:
        codex_policy.wake_env({})
    assert e.value.code == "no_auth_file"
    rc = kurgu_cli.setup_wake("codex", True)                          # `setup-agent --codex --wake` says so BEFORE the first wake
    assert rc == 0 and "will NOT start Codex" in capsys.readouterr().err


def test_a_regular_auth_file_codex_wrote_is_never_clobbered(homes):
    kconf, real = homes
    env, _ = codex_policy.wake_env({})
    link = os.path.join(env["CODEX_HOME"], "auth.json")
    if not os.path.islink(link):
        pytest.skip("no symlinks here")
    target = os.path.realpath(link)
    os.remove(link)
    open(link, "w").write('{"refreshed": "token"}')                    # what a token refresh by rename leaves behind
    with pytest.raises(codex_policy.PolicyProblem) as e:
        codex_policy.wake_env({})
    assert e.value.code == "auth_replaced" and open(link).read() == '{"refreshed": "token"}' and not os.path.islink(link)
    open(link, "w").write(open(target).read())                         # identical content: fine, left alone
    assert codex_policy.wake_env({})[0]["CODEX_HOME"] == env["CODEX_HOME"] and not os.path.islink(link)


def test_a_link_to_the_wrong_place_is_repointed_but_a_right_one_is_untouched(homes):
    kconf, real = homes
    env, _ = codex_policy.wake_env({})
    link = os.path.join(env["CODEX_HOME"], "auth.json")
    if not os.path.islink(link):
        pytest.skip("no symlinks here")
    before = os.lstat(link).st_ino
    codex_policy.wake_env({})
    assert os.lstat(link).st_ino == before                              # not recreated
    os.remove(link)
    os.symlink(str(real / "elsewhere.json"), link)
    codex_policy.wake_env({})
    assert os.path.realpath(link) == os.path.realpath(real / "auth.json")


def test_the_woken_codex_home_trusts_no_project_so_a_project_codex_config_is_not_loaded(homes):
    """Codex loads <project>/.codex/config.toml (mcp_servers, hooks, profiles) only for a project marked trusted. The dedicated home copies model settings only,
    never the user's [projects.*] trust entries."""
    kconf, real = homes
    env, _ = codex_policy.wake_env({})
    cfg = open(os.path.join(env["CODEX_HOME"], "config.toml"), encoding="utf-8").read()
    assert "projects" not in cfg and "trust_level" not in cfg and "mcp_servers" not in cfg and "notify" not in cfg
