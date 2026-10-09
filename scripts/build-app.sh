#!/bin/bash
# Build the fresh core. Installation never launches or operates on iTerm.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != --install ) ]]; then
  echo 'Usage: bash scripts/build-app.sh [--install]' >&2; exit 2
fi
DESTINATION="/Applications/Free Parking.app"
check_target() {
  if ! swift -e 'import AppKit; exit(NSRunningApplication.runningApplications(withBundleIdentifier: "com.jacobsapps.FreeParking").isEmpty ? 0 : 1)'; then
    echo 'Quit Free Parking before updating. Leave iTerm open.' >&2; exit 1
  fi
  if [[ -L "$DESTINATION" || ( -e "$DESTINATION" && ! -d "$DESTINATION" ) ]]; then
    echo 'Install target is not a regular app bundle.' >&2; exit 1
  fi
  if [[ -d "$DESTINATION" && "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$DESTINATION/Contents/Info.plist")" != com.jacobsapps.FreeParking ]]; then
    echo 'Install target belongs to another application.' >&2; exit 1
  fi
}
[[ "${1:-}" != --install ]] || check_target
bash Rebuild/build.sh --production
[[ "${1:-}" == --install ]] || exit 0
check_target
BACKUP="$HOME/Library/Application Support/Free Parking/Application Backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"
if [[ -d "$DESTINATION" ]]; then
  /usr/bin/ditto "$DESTINATION" "$BACKUP/Free Parking.app"
  codesign --verify --deep --strict "$BACKUP/Free Parking.app"
fi
# Legacy recovery remains in place, never imported by guessing conversation IDs.
printf 'Previous application backup: %s\nLegacy recovery unchanged: %s\n' "$BACKUP" "$HOME/Library/Application Support/Car Park"
STAGING=$(mktemp -d /Applications/.free-parking-install.XXXXXX)
cleanup() {
  if [[ -d "$STAGING/previous.app" && ! -e "$DESTINATION" ]]; then mv "$STAGING/previous.app" "$DESTINATION"; fi
  rm -rf -- "$STAGING"
}
trap cleanup EXIT
/usr/bin/ditto "$PWD/dist/Free Parking.app" "$STAGING/new.app"
codesign --verify --deep --strict "$STAGING/new.app"
check_target
if [[ -d "$DESTINATION" ]]; then mv "$DESTINATION" "$STAGING/previous.app"; fi
mv "$STAGING/new.app" "$DESTINATION"
codesign --verify --deep --strict "$DESTINATION"
printf 'Installed, not launched: %s\n' "$DESTINATION"
