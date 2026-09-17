#!/bin/bash
# Builds only. Never launches Free Parking, reads iTerm, or installs hooks.
set -euo pipefail
cd "$(dirname "$0")/.."
FLAGS=(-c release)
NAME="Free Parking"
if [[ "${1:-}" == "--preview" ]]; then
    FLAGS+=(-Xswiftc -DFREE_PARKING_DEMO --scratch-path .build/preview)
    NAME="Free Parking Preview"
fi
if [[ ! -f Resources/AppIcon.icns || Artwork/FreeParkingIcon.swift -nt Resources/AppIcon.icns || scripts/render-icon.swift -nt Resources/AppIcon.icns ]]; then
    bash scripts/build-icon.sh
fi
swift build "${FLAGS[@]}"
BIN_DIR="$(swift build --show-bin-path "${FLAGS[@]}")"
APP="$PWD/dist/$NAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/FreeParking" "$APP/Contents/MacOS/FreeParking"
cp Resources/Info.plist "$APP/Contents/Info.plist"
cp Resources/AppIcon.icns "$APP/Contents/Resources/"
if [[ "$NAME" == "Free Parking Preview" ]]; then
    /usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier com.jacobsapps.FreeParking.Preview' "$APP/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c 'Set :CFBundleName Free Parking Preview' "$APP/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c 'Set :CFBundleDisplayName Free Parking Preview' "$APP/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c 'Delete :NSAppleEventsUsageDescription' "$APP/Contents/Info.plist"
    # No terminal-control resources or Automation entitlement in this app.
    rm -f "$APP/Contents/Resources/freeparking.py" "$APP/Contents/Resources/iterm.js" "$APP/Contents/Resources/iterm_api.py"
    codesign --force --sign - "$APP"
else
    cp Resources/freeparking.py Resources/iterm.js Resources/iterm_api.py "$APP/Contents/Resources/"
    codesign --force --sign - --entitlements Resources/FreeParking.entitlements "$APP"
fi
printf '\nBuilt (not launched): %s\n' "$APP"
