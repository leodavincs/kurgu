# Contributing

Thanks for helping. Kurgu is small on purpose: a JSON timeline, a stdlib server, a numpy renderer and a vanilla-JS editor.

- **Contract first.** `SPEC.md` is the source of truth for the project format, the render behaviour and the server API. If you change behaviour, change the spec in the same pull request.
- **Preview and render must agree.** Anything the editor draws has to match `render.py` (same fonts, same maths). Add a test when you touch either side.
- **Setup:** Python 3.10+ and ffmpeg on PATH. In a clone: `python3 -m venv .venv`, activate it, `python -m pip install -r requirements.txt pytest` (Chrome/Chromium and `websocket-client` are optional and enable the browser tests, which skip themselves without them). Run `python scripts/check_deps.py` and `python -m pytest -q`. CI runs the suite on Linux, macOS and Windows.
- **Packaging:** the modules stay flat at the repo root; `pyproject.toml` + `setup.py` ship `web/`, `fonts/`, `skills/` under `<prefix>/share/kurgu` and `kurgu_paths.py` finds them. If you add a module, list it in `py-modules`. Paths (cache, config, bundled data) go through `kurgu_paths.py`; never hard-code `~/.cache` or `python3`.
- **Language:** code, comments, file names and JSON keys in English. UI text goes through `web/i18n/en.json` and `web/i18n/tr.json`; add both.
- **Dependencies:** avoid new ones. The server is stdlib plus fontTools; the editor has no build step.
- **Fonts:** only OFL-licensed fonts, with their licence file in `fonts/licenses/`.
- **Pull requests:** small and focused, with a short description of what you tried in the editor and by rendering.
- **Privacy:** Kurgu never sends data anywhere. Please keep it that way.

Bug reports: include the OS, `python scripts/check_deps.py` output and, if possible, a minimal `project.json`.

## Releasing

Bump `version` in `pyproject.toml` and `.claude-plugin/plugin.json` together, then check `python -m pytest -q`, `uv build` and `claude plugin validate .`.
