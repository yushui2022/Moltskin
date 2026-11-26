#!/bin/bash
# Build a technical-preview macOS package for Apple Silicon.
#
# Usage:
#   ./release/macos/build_alpha.sh
#   MOLTSKIN_CHROMIUM_BINARY="$HOME/chromium-baseline/src/out/MoltskinHardwarePrivacy/Chromium.app" \
#     ./release/macos/build_alpha.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${MOLTSKIN_VERSION:-$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/moltskin/__init__.py")}"
ARCH="$(uname -m)"
CHROMIUM="${MOLTSKIN_CHROMIUM_BINARY:-$HOME/chromium-baseline/src/out/MoltskinHardwarePrivacy/Chromium.app}"

if [[ "$ARCH" != "arm64" && "$ARCH" != "x86_64" ]]; then
  echo "Unsupported macOS architecture: $ARCH" >&2
  exit 1
fi
if [[ "$CHROMIUM" == *"/Contents/MacOS/Chromium" ]]; then
  CHROMIUM="${CHROMIUM%/Contents/MacOS/Chromium}"
fi
if [[ ! -x "$CHROMIUM/Contents/MacOS/Chromium" ]]; then
  echo "Chromium.app not found at: $CHROMIUM" >&2
  echo "Set MOLTSKIN_CHROMIUM_BINARY to the built Chromium.app." >&2
  exit 1
fi

STAGE_PARENT="$ROOT/dist"
STAGE="$STAGE_PARENT/Moltskin-${VERSION}-macOS-${ARCH}"
ZIP="$STAGE_PARENT/Moltskin-${VERSION}-macOS-${ARCH}.zip"
mkdir -p "$STAGE_PARENT"
rm -rf "$STAGE" "$ZIP" "$ZIP.sha256"
mkdir -p "$STAGE"

rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/moltskin" "$STAGE/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/chromium" "$STAGE/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/platforms" "$STAGE/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/web" "$STAGE/"
cp "$ROOT/run.py" "$ROOT/requirements.txt" "$ROOT/LICENSE" "$ROOT/NOTICE" "$STAGE/"
cp "$ROOT/README.md" "$ROOT/README.zh-CN.md" "$ROOT/CHANGELOG.md" "$STAGE/"
cp "$ROOT/docs/release-macos-alpha.md" "$STAGE/README-alpha.md"
ditto "$CHROMIUM" "$STAGE/Chromium.app"
"$ROOT/release/macos/make_icon.sh" "$STAGE/Chromium.app" "$ROOT/web/logo.png"

cat > "$STAGE/runtime.env" <<'ENV'
# Sourced by the launcher commands. The folder is relocatable; $HERE is set by
# each command before sourcing this file.
export MOLTSKIN_DATA_DIR="${MOLTSKIN_DATA_DIR:-$HOME/Library/Application Support/Moltskin}"
export MOLTSKIN_CHROMIUM_BINARY="${MOLTSKIN_CHROMIUM_BINARY:-$HERE/Chromium.app}"
export MOLTSKIN_OPEN_MANAGER="${MOLTSKIN_OPEN_MANAGER:-1}"
export MOLTSKIN_PORT="${MOLTSKIN_PORT:-8765}"
ENV

cat > "$STAGE/Setup.command" <<'SETUP'
#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
source ./runtime.env
PYTHON="${MOLTSKIN_PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.10+ is required. Install it from python.org or Homebrew and run this again."
  read -r -p "Press Return to close..."
  exit 1
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10+ is required. Current: $("$PYTHON" --version 2>&1)"
  read -r -p "Press Return to close..."
  exit 1
fi
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p "$MOLTSKIN_DATA_DIR"
echo
echo "Setup complete. Double-click 'Start Moltskin.command'."
echo "Data directory: $MOLTSKIN_DATA_DIR"
read -r -p "Press Return to close..."
SETUP

cat > "$STAGE/Start Moltskin.command" <<'START'
#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
source ./runtime.env
if [[ ! -x .venv/bin/python ]]; then
  echo "Run Setup.command first."
  read -r -p "Press Return to close..."
  exit 1
fi
mkdir -p "$MOLTSKIN_DATA_DIR"
exec .venv/bin/python "$HERE/chromium/open_panel.py" --show-errors
START

cat > "$STAGE/Install Moltskin.command" <<'INSTALL'
#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
source ./runtime.env
if [[ ! -x .venv/bin/python ]]; then
  echo "Run Setup.command first."
  read -r -p "Press Return to close..."
  exit 1
fi
.venv/bin/python "$HERE/chromium/install_panel_macos.py"
read -r -p "Press Return to close..."
INSTALL

chmod +x "$STAGE/Setup.command" "$STAGE/Start Moltskin.command" "$STAGE/Install Moltskin.command"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGE" -name '*.pyc' -delete

ditto -c -k --sequesterRsrc --keepParent "$STAGE" "$ZIP"
(cd "$STAGE_PARENT" && shasum -a 256 "$(basename "$ZIP")" | tee "$(basename "$ZIP").sha256")
echo
echo "Package: $ZIP"
du -sh "$STAGE" "$ZIP"
