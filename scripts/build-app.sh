#!/bin/bash
# Builds and optionally installs. Never launches apps, reads iTerm, or installs hooks.
set -euo pipefail
cd "$(dirname "$0")/.."
FLAGS=(-c release)
NAME="Free Parking"
INSTALL=false
if [[ $# -gt 1 ]]; then
    echo "Usage: bash scripts/build-app.sh [--preview | --install]" >&2
    exit 2
fi
case "${1:-}" in
    --preview)
        FLAGS+=(-Xswiftc -DFREE_PARKING_DEMO --scratch-path .build/preview)
        NAME="Free Parking Preview" ;;
    --install) INSTALL=true ;;
    "") ;;
    *) echo "Usage: bash scripts/build-app.sh [--preview | --install]" >&2; exit 2 ;;
esac

check_install_target() {
    # Read-only app identity check. Never quit an app or touch its terminals.
    if ! swift -e 'import AppKit; exit(NSRunningApplication.runningApplications(withBundleIdentifier: "com.jacobsapps.FreeParking").isEmpty ? 0 : 1)'; then
        echo "Quit Free Parking, then run --install again. Leave iTerm open." >&2
        exit 1
    fi
    if [[ -L "$DESTINATION" || ( -e "$DESTINATION" && ! -d "$DESTINATION" ) ]]; then
        echo "Install destination is not a regular app bundle; nothing replaced." >&2
        exit 1
    fi
    if [[ -d "$DESTINATION" ]]; then
        local identifier
        identifier=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$DESTINATION/Contents/Info.plist")
        if [[ "$identifier" != "com.jacobsapps.FreeParking" ]]; then
            echo "Install destination belongs to another app; nothing replaced." >&2
            exit 1
        fi
    fi
}

DESTINATION="/Applications/Free Parking.app"
if $INSTALL; then
    check_install_target
    if [[ ! -w /Applications ]]; then
        echo "Applications is not writable. Build without --install and copy the app there using Finder." >&2
        exit 1
    fi
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

if $INSTALL; then
    STAGING=$(mktemp -d /Applications/.free-parking-install.XXXXXX)
    cleanup_install() {
        if [[ -d "$STAGING/previous.app" && ! -e "$DESTINATION" ]]; then
            mv "$STAGING/previous.app" "$DESTINATION" || return
        fi
        rm -rf "$STAGING"
    }
    trap cleanup_install EXIT
    /usr/bin/ditto "$APP" "$STAGING/Free Parking.app"
    codesign --verify --deep --strict "$STAGING/Free Parking.app"
    check_install_target
    if [[ -d "$DESTINATION" ]]; then
        mv "$DESTINATION" "$STAGING/previous.app"
    fi
    mv "$STAGING/Free Parking.app" "$DESTINATION"
    printf 'Installed (not launched): %s\n' "$DESTINATION"
fi
