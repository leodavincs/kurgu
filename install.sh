#!/bin/sh
# Kurgu installer for macOS and Linux. No admin rights, no Python or ffmpeg needed beforehand.
#   curl -LsSf https://raw.githubusercontent.com/leodavincs/kurgu/main/install.sh | sh
# Safe to run again: it upgrades. Override the package source with KURGU_SOURCE (a path, URL or "git+https://...").
set -eu

SOURCE="${KURGU_SOURCE:-https://github.com/leodavincs/kurgu/archive/refs/heads/main.zip}"
PY="${KURGU_PYTHON:-3.12}"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

ORIG_PATH="$PATH"

# 1. uv (installs and manages Python for us)
if ! command -v uv >/dev/null 2>&1; then
  for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do [ -x "$d/uv" ] && PATH="$d:$PATH"; done
fi
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (the Python manager) into your user folder..."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh || die "Could not download uv. Check your internet connection (behind a proxy set HTTPS_PROXY) or install uv by hand: https://docs.astral.sh/uv/getting-started/installation/"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh || die "Could not download uv. Check your internet connection or install uv by hand: https://docs.astral.sh/uv/getting-started/installation/"
  else
    die "Neither curl nor wget found. Install curl (for example: sudo apt install curl) and run this again."
  fi
  for d in "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do [ -n "$d" ] && [ -x "$d/uv" ] && PATH="$d:$PATH"; done
fi
command -v uv >/dev/null 2>&1 || die "uv was installed but is not on PATH. Open a new terminal and run this command again."

# 2. Kurgu itself, on its own managed Python (kurgu and kurgu-notes land in uv's tool bin folder)
say "Installing Kurgu (Python $PY is downloaded automatically if needed)..."
uv tool install --force --reinstall --python "$PY" "$SOURCE" \
  || die "Installing Kurgu failed. Offline or behind a proxy? Set HTTPS_PROXY and retry. If the message above mentions Python, try: uv python install $PY"
BIN="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
PATH="$BIN:$PATH"; export PATH
KURGU="$BIN/kurgu"
[ -x "$KURGU" ] || die "kurgu was not found in $BIN after installing. Run: uv tool list"

# 3. ffmpeg (static build in Kurgu's own folder; skipped when one is already installed)
say "Checking ffmpeg..."
"$KURGU" setup-ffmpeg || say "ffmpeg download failed (see above). Kurgu is installed; fix the network and run: kurgu setup-ffmpeg"

# 4. Self-check
"$KURGU" doctor || say "Some checks failed. Fix what is listed above, then run: kurgu doctor"

# 5. Make sure new terminals find `kurgu`
case ":$ORIG_PATH:" in
  *":$BIN:"*) ;;
  *)
  uv tool update-shell >/dev/null 2>&1 || true
  say ""
  say "Note: open a new terminal so that the 'kurgu' command is found. Until then use: $KURGU"
  ;;
esac

say ""
say "Done. Next step:  kurgu <your project folder>"
