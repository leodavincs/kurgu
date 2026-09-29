---
description: Start the Kurgu video editor for a project folder and report its URL
argument-hint: "[project-dir]"
allowed-tools: Bash
---

Open the Kurgu editor for the project directory `$ARGUMENTS` (use the current working directory if empty).

1. Check that `ffmpeg` and `ffprobe` are on the PATH (`ffmpeg -version`). If not, show the install command for the user's OS (macOS `brew install ffmpeg`, Debian/Ubuntu `sudo apt install ffmpeg`, Windows `winget install Gyan.FFmpeg`), offer to run it, and stop until ffmpeg is present. Also make sure `kurgu --help` works (Kurgu installed with pipx) or `uvx --version` works (uv installed, Kurgu runs from the plugin folder). If neither does, tell the user to install uv (`brew install uv`, `winget install astral-sh.uv`, or `pipx install uv`) and stop.
2. Resolve the directory to an absolute path. If it does not exist, create it (the server writes an empty `project.json` template if none exists). Keep paths quoted: they may contain spaces or non-ASCII characters.
3. Start the server in the background with the Bash tool's `run_in_background`, using whichever launcher step 1 found:
   `kurgu "<absolute dir>" --no-open` or `uvx --from "${CLAUDE_PLUGIN_ROOT}" kurgu "<absolute dir>" --no-open`
   Read its first output lines to find the URL (it binds 127.0.0.1 and moves to the next port if 8765 is busy). If a Kurgu server for this directory is already running, reuse it instead of starting another.
4. Tell the user the URL (for example http://127.0.0.1:8765) in one or two lines and that they can open it in a browser, edit by hand, and come back to ask for changes.
5. Follow the `kurgu` skill from here on: re-read `project.json`, `changes.md` and `.kurgu/state.json` before every edit.
