#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BROWSER_STAGING_DIR="$ROOT_DIR/build/playwright-browsers"

ARCH="$(uname -m)"
case "$ARCH" in
  arm64)
    RELEASE_ARCH="arm64"
    ;;
  x86_64)
    RELEASE_ARCH="intel"
    ;;
  *)
    echo "Unsupported macOS architecture: $ARCH" >&2
    exit 1
    ;;
esac

uv sync --extra dev
rm -rf "$BROWSER_STAGING_DIR"
PLAYWRIGHT_BROWSERS_PATH="$BROWSER_STAGING_DIR" uv run playwright install chromium
uv run python packaging/playwright_browsers.py clear-package-local
uv run pyinstaller --noconfirm --clean packaging/160grab.spec
uv run python packaging/playwright_browsers.py sync-into-bundle \
  --source "$BROWSER_STAGING_DIR" \
  --bundle-root "$ROOT_DIR/dist/160Grab/_internal"
uv run python packaging/build_release.py --platform macos --arch "$RELEASE_ARCH"
