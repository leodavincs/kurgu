"""The Turkish dative of the agent's name on the Send button (web/js/dative.js): pronunciation, not spelling, decides the vowel."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_dative_of_common_agent_names():
    names = ["Claude", "Codex", "Newton", "Gemini", "Copilot", "Cursor", "Aider", "Kurgu", "claude", "Ayşe", "Ali", "Zed", "Linus", "Öğe", "Kerem"]
    js = "import {dative} from './web/js/dative.js'; console.log(JSON.stringify(%s.map(dative)))" % json.dumps(names)
    out = json.loads(subprocess.run(["node", "--input-type=module", "-e", js], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    assert out[:8] == ["Claude'a", "Codex'e", "Newton'a", "Gemini'ye", "Copilot'a", "Cursor'a", "Aider'a", "Kurgu'ya"]     # the exceptions
    assert out[8] == "claude'a"                                                                                           # case does not matter
    assert out[9:] == ["Ayşe'ye", "Ali'ye", "Zed'e", "Linus'a", "Öğe'ye", "Kerem'e"]                                        # everything else: the spelling rule
