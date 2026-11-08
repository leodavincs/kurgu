"""`kurgu --help` lists every subcommand; the list is kept in step with the dispatch in kurgu_cli.main(), server.main() and the launchers."""
import inspect
import os
import re
import subprocess
import sys

import kurgu_cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dispatched():
    names = set()
    for m in re.finditer(r"args\[0\] (?:==|in) (\(?[^:]+?\)?):", inspect.getsource(kurgu_cli.main)):
        names |= set(re.findall(r'"([a-z][a-z-]*)"', m.group(1)))
    import server
    for m in re.finditer(r'args\[0\] == "([a-z]+)"', inspect.getsource(server.main)):
        names.add(m.group(1))
    return names - {"help"}


def test_every_dispatched_subcommand_is_in_the_help_table():
    names = dispatched()
    assert {"notes", "frame", "strip", "rerender", "elements", "import", "detect", "ocr", "setup-agent", "wake", "doctor", "setup-ffmpeg"} <= names
    assert (names | {"help"}) - {"ocr"} == set(kurgu_cli.COMMANDS)           # `ocr` is the documented alias of `detect`; `help <topic>` is dispatched ahead of --help


def test_the_launchers_route_every_command():
    sh, cmd = (open(os.path.join(ROOT, f), encoding="utf-8").read() for f in ("kurgu", "kurgu.cmd"))
    for n in kurgu_cli.COMMANDS:
        assert f'"{n}"' in sh, n
    assert 'kurgu_cli.py" %*' in cmd                                             # kurgu.cmd hands every command, as typed, to kurgu_cli.main (the `kurgu` entry point)
    assert '"--help"' in sh


def test_help_output_lists_commands_and_exits_zero():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "kurgu_cli.py"), "--help"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    for n, desc in kurgu_cli.COMMANDS.items():
        assert re.search(rf"^  {re.escape(n)}\s+\S", r.stdout, re.M), n
    assert "--port" in r.stdout and "--no-open" in r.stdout
