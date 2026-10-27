#!/bin/bash
# Builds only; scripts/build-app.sh controls installation. Never touches iTerm.
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="Free Parking Rebuild"
IDENTIFIER="com.jacobsapps.FreeParking.Rebuild"
if [[ "${1:-}" == "--production" ]]; then
  NAME="Free Parking"; IDENTIFIER="com.jacobsapps.FreeParking"
elif [[ $# -ne 0 ]]; then
  echo 'Usage: bash Rebuild/build.sh [--production]' >&2; exit 2
fi
APP="$PWD/dist/$NAME.app"
# Generated output only: a clean bundle must not retain the legacy executable.
rm -rf -- "$APP"
mkdir -p .build/rebuild-module-cache "$APP/Contents/MacOS" "$APP/Contents/Resources/rebuild/parking"
swift build -c release
BIN_DIR="$(swift build -c release --show-bin-path)"
cp "$BIN_DIR/FreeParking" "$APP/Contents/MacOS/FreeParking"
cp Rebuild/backend.py Rebuild/garage_backend.py "$APP/Contents/Resources/rebuild/"
cp Rebuild/parking/*.py Rebuild/parking/terminal.js "$APP/Contents/Resources/rebuild/parking/"
SDK_FINGERPRINT="$(shasum -a 256 Rebuild/iterm-api-requirements.txt | awk '{print $1}')"
if [[ ! -f .build/iterm-api/.ready || "$(cat .build/iterm-api/.ready)" != "$SDK_FINGERPRINT" ]]; then
  rm -rf .build/iterm-api
  /usr/bin/python3 -m pip install --disable-pip-version-check --no-compile --target .build/iterm-api -r Rebuild/iterm-api-requirements.txt
  printf '%s' "$SDK_FINGERPRINT" > .build/iterm-api/.ready
fi
ditto .build/iterm-api "$APP/Contents/Resources/rebuild/vendor"
cp Rebuild/THIRD-PARTY.txt Rebuild/ITERM-API-LICENSE.txt "$APP/Contents/Resources/rebuild/"
rm -f "$APP/Contents/Resources/rebuild/parking/confirmation_testing.py" "$APP/Contents/Resources/rebuild/parking/codex_testing.py" "$APP/Contents/Resources/rebuild/parking/agent_testing.py" "$APP/Contents/Resources/rebuild/parking/testing.py" "$APP/Contents/Resources/rebuild/parking/lab.py"
cp Resources/AppIcon.icns "$APP/Contents/Resources/"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>FreeParking</string>
<key>CFBundleIdentifier</key><string>$IDENTIFIER</string>
<key>CFBundleName</key><string>$NAME</string>
<key>CFBundleDisplayName</key><string>$NAME</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.5.1</string>
<key>CFBundleVersion</key><string>2026102705</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSAppleEventsUsageDescription</key><string>Save and restore your iTerm windows when you choose Park my windows.</string>
<key>NSDocumentsFolderUsageDescription</key><string>Access your saved session folders in Documents to reopen Claude and Codex in the same place.</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - --entitlements Resources/FreeParking.entitlements "$APP"
codesign --verify --deep --strict "$APP"

printf 'Built, not installed or launched: %s\n' "$APP"
