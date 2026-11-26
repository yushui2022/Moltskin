#!/bin/bash
# Replace a Chromium.app icon with the Moltskin chameleon.
set -euo pipefail

APP="${1:?usage: make_icon.sh /path/to/Chromium.app [logo.png]}"
LOGO="${2:-$(cd "$(dirname "$0")/../.." && pwd)/web/logo.png}"

if [[ ! -d "$APP/Contents/Resources" ]]; then
  echo "Not a macOS app bundle: $APP" >&2
  exit 1
fi
if [[ ! -f "$LOGO" ]]; then
  echo "Logo not found: $LOGO" >&2
  exit 1
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/moltskin-icon.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
ICONSET="$WORK/Moltskin.iconset"
mkdir -p "$ICONSET"

for size in 16 32 128 256 512; do
  /usr/bin/sips -z "$size" "$size" "$LOGO" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  /usr/bin/sips -z "$double" "$double" "$LOGO" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done

/usr/bin/iconutil -c icns "$ICONSET" -o "$WORK/Moltskin.icns"
cp "$WORK/Moltskin.icns" "$APP/Contents/Resources/app.icns"
/usr/libexec/PlistBuddy -c 'Set :CFBundleIconFile app.icns' "$APP/Contents/Info.plist" 2>/dev/null || true
# Chromium ships an Assets.car AppIcon. Without removing CFBundleIconName,
# LaunchServices may keep showing the Chromium icon instead of app.icns.
/usr/libexec/PlistBuddy -c 'Delete :CFBundleIconName' "$APP/Contents/Info.plist" 2>/dev/null || true
# Refresh Finder/Dock's cached icon for a running bundle.
touch "$APP"
echo "Icon replaced: $APP/Contents/Resources/app.icns"
