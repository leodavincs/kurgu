"""The agent guidance has ONE source (guidance/); everything else is generated. These tests fail when a copy drifts.

If a test here says "run scripts/gen_guidance.py", do that and commit the result. Never edit the generated regions by hand."""
import importlib.util
import inspect
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _gen():
    spec = importlib.util.spec_from_file_location("gen_guidance", os.path.join(ROOT, "scripts", "gen_guidance.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", newline="") as f:
        return f.read()


GENERATED_FILES = ["skills/kurgu/SKILL.md", "AGENTS.md", "README.md", "README.tr.md", "SPEC.md"]


def test_generator_output_is_committed():
    stale = [rel for rel, new in _gen().generate().items() if _read(rel) != new]
    assert not stale, f"{stale} out of date: run scripts/gen_guidance.py"


def test_generated_regions_are_balanced():
    for rel in GENERATED_FILES:
        t = _read(rel)
        assert len(re.findall(r"<!-- kurgu:generated:(?!end)[\w-]+ -->", t)) == t.count("<!-- kurgu:generated:end -->") >= 1, rel


def test_kurgu_cli_uses_the_generated_texts():
    import guidance_gen
    import kurgu_cli
    assert kurgu_cli.AGENT_BLOCK is guidance_gen.AGENT_BLOCK and kurgu_cli.WAKE_PROMPT is guidance_gen.WAKE_PROMPT
    assert not re.search(r"^(AGENT_BLOCK|WAKE_PROMPT) = ", _read("kurgu_cli.py"), re.M), "the texts live in guidance/, not in kurgu_cli.py"


def test_size_caps():
    import kurgu_cli
    assert len(kurgu_cli.AGENT_BLOCK.encode()) <= 2850         # Codex reads 32 KiB of AGENTS.md in total; the hard limit is 3000, keep 150 bytes of headroom
    assert len(kurgu_cli.WAKE_PROMPT) < 1400
    for rule in ("kurgu notes ask", "stand-in", "frozen"):
        assert rule in kurgu_cli.AGENT_BLOCK


# ---------------------------------------------------------------- MCP schemas vs argparse
CLI_ONLY = {"output", "full", "json"}       # CLI flags with no MCP argument (file paths, JSON printing, --full is MCP `draft`)
MCP_ONLY = {"project_dir", "draft"}         # MCP arguments with no CLI flag


def _handler_keys(fn):
    src = inspect.getsource(fn)
    return set(re.findall(r'\ba\.get\("(\w+)"', src)) | set(re.findall(r'\ba\["(\w+)"\]', src))


def test_mcp_schemas_match_the_argparse_parsers():
    import mcp_server as M
    assert set(M.CLI_MIRRORS) == {"render_frame", "render_strip", "rerender", "detect", "notes_list", "notes_wait", "notes_done", "notes_ask"}
    for tool, cfg in M.CLI_MIRRORS.items():
        parser, names = cfg["parser"], cfg.get("names", {})
        sch = M.TOOL_BY_NAME[tool][3]
        props, req = sch["properties"], set(sch.get("required", []))
        grouped = {a.dest for g in parser._mutually_exclusive_groups for a in g._group_actions}
        expected_req = set()
        for act in parser._actions:
            if act.dest == "help":
                continue
            if act.dest in grouped:
                assert "status" in props and act.dest in props["status"]["enum"], f"{tool}: flag --{act.dest} is not in the MCP status enum"
                continue
            if act.dest in cfg.get("skip", ()):
                assert act.dest in CLI_ONLY, f"{tool}: CLI arg {act.dest} is skipped in MCP but not declared CLI-only here"
                continue
            if act.dest == "project_dir":
                continue
            name = names.get(act.dest, act.dest)
            assert name in props, f"{tool}: CLI arg {act.dest} missing from the MCP schema"
            want = "boolean" if act.nargs == 0 else "array" if act.nargs in ("+", "*") else {int: "integer", float: "number"}.get(act.type, "string")
            assert props[name]["type"] == want, f"{tool}.{name}: type {props[name]['type']} != {want}"
            if act.required or (not act.option_strings and act.nargs not in ("?", "*")):
                expected_req.add(name)
        assert req == expected_req, f"{tool}: required {req} != CLI {expected_req}"
        cli_names = {names.get(a.dest, a.dest) for a in parser._actions if a.dest != "help"}
        for p in props:
            assert p in cli_names or p in MCP_ONLY or p == "status", f"{tool}: MCP argument {p} has no CLI twin (declare it MCP-only)"
        keys = _handler_keys(M.TOOL_BY_NAME[tool][4])
        assert keys <= set(props), f"{tool}: the handler reads {sorted(keys - set(props))} which the schema does not offer"
        if tool in ("render_frame", "render_strip"):
            assert "draft" in props and "full" in {a.dest for a in parser._actions}


def test_tool_descriptions_come_from_guidance():
    import guidance_gen
    import mcp_server as M
    for tool in guidance_gen.TOOL_DESCRIPTIONS:
        assert M.TOOL_BY_NAME[tool][2] == guidance_gen.TOOL_DESCRIPTIONS[tool]
    assert set(guidance_gen.TOOL_DESCRIPTIONS) == set(M.CLI_MIRRORS)
    assert "{{" not in "".join(guidance_gen.TOOL_DESCRIPTIONS.values())


def test_documented_tool_names_are_the_real_tools():
    import mcp_server as M
    real = set(M.TOOL_BY_NAME)
    listed = set(re.findall(r"`(\w+)`", _gen().sections("tools")["names"]))
    assert listed == real, f"guidance/tools.md out of date: missing {sorted(real - listed)}, unknown {sorted(listed - real)}"
    agents = _read("AGENTS.md")
    assert [t for t in real if f"`{t}`" not in agents] == []


# ---------------------------------------------------------------- every command named in the guidance exists
def _texts():
    import guidance_gen
    out = {"AGENT_BLOCK": guidance_gen.AGENT_BLOCK, "WAKE_PROMPT": guidance_gen.WAKE_PROMPT}
    out.update({f"tool {k}": v for k, v in guidance_gen.TOOL_DESCRIPTIONS.items()})
    out.update({rel: _read(rel) for rel in GENERATED_FILES})
    return out


def _opts(parser):
    return {o for a in parser._actions for o in a.option_strings}


def test_every_cli_command_in_the_guidance_exists():
    import agent_notes
    import kurgu_cli
    notes_subs = agent_notes.build_parsers()[1]
    import ocr_boxes
    import render_adaptors
    flags = {"frame": _opts(kurgu_cli.frame_parser()), "strip": _opts(kurgu_cli.strip_parser()),
             "detect": _opts(ocr_boxes.detect_parser()), "ocr": _opts(ocr_boxes.detect_parser()), "rerender": _opts(render_adaptors.rerender_parser())}
    flags.update({f"notes {k}": _opts(v) for k, v in notes_subs.items()})
    import server
    main_src = inspect.getsource(kurgu_cli.main) + inspect.getsource(server.main)       # `notes` is routed by server.main
    import guidance_gen
    assert dict(guidance_gen.COMMAND_HELP) == dict(kurgu_cli.COMMANDS) and list(kurgu_cli.COMMANDS) == list(guidance_gen.COMMAND_HELP)
    for c in kurgu_cli.COMMANDS:
        assert f'"{c}"' in main_src, f"kurgu_cli.COMMANDS lists {c} but main() does not dispatch it"
    bad = []
    for where, text in _texts().items():
        for span in re.findall(r"`(kurgu [^`]+)`", text):
            if re.fullmatch(r"kurgu [a-z-]+(\|[a-z-]+)+", span):          # `kurgu notes|frame|strip`: a list of commands
                bad += [(where, span, f"unknown command {c}") for c in span[6:].split("|") if c not in kurgu_cli.COMMANDS and c != "ocr"]
                continue
            toks = span.replace("|", " | ").split()
            cmd = toks[1]
            if not re.fullmatch(r"[a-z][a-z-]+", cmd) or cmd in ("and", "mcp", "tools"):
                continue                                            # `kurgu <project>`, `kurgu ./notes`: the editor, not a subcommand
            if cmd not in kurgu_cli.COMMANDS and cmd != "ocr":          # `ocr` is the documented alias of `detect`
                bad.append((where, span, "unknown command"))
                continue
            if cmd == "notes":
                subs = []
                for t in toks[2:]:
                    if t.startswith(("<", "[", "-", "...", "{", "…")):
                        break
                    if t != "|":
                        subs.append(t)
                for sname in subs:
                    if sname not in notes_subs:
                        bad.append((where, span, f"unknown notes subcommand {sname}"))
                key = f"notes {subs[0]}" if len(subs) == 1 and subs[0] in notes_subs else None
            else:
                key = cmd if cmd in flags else None
            if key:
                for t in re.findall(r"--[a-z][a-z-]*", span):
                    if t not in flags[key]:
                        bad.append((where, span, f"unknown flag {t}"))
    assert not bad, bad


def test_scripts_and_mcp_tool_names_in_the_guidance_exist():
    import mcp_server as M
    try:
        import tomllib
    except ImportError:
        pytest.skip("needs Python 3.11+")
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        cfg = tomllib.load(f)
    scripts, mods = set(cfg["project"]["scripts"]), set(cfg["tool"]["setuptools"]["py-modules"])
    assert "guidance_gen" in mods
    bad = []
    for where, text in _texts().items():
        for s in re.findall(r"`(kurgu-[a-z]+)\b", text):
            if s not in scripts:
                bad.append((where, s))
        for n in re.findall(r"`((?:notes|render)_[a-z]+)`", text):
            if n not in M.TOOL_BY_NAME:
                bad.append((where, n))
    assert not bad, bad


def test_agents_md_wake_paragraph_describes_session_routing():
    import kurgu_cli
    text = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AGENTS.md"), encoding="utf-8").read()
    para = next(l for l in text.splitlines() if l.lstrip().startswith("**Wake the agent when he presses Send"))
    assert ".kurgu/agents.json" in para and "--bg --resume" in para and "exec resume <session id>" in para and "resume --last" in para
    assert "`codex exec resume --last --json" not in para                               # that was the old, unconditional behaviour
    assert f"last {kurgu_cli.ACTIVE_SESSION_SECS // 60} minutes" in para and "wake.json" in para and "| " in para
