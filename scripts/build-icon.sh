#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .build/artwork
swiftc -O -parse-as-library -target "$(uname -m)-apple-macosx14.0" \
    Artwork/FreeParkingIcon.swift scripts/render-icon.swift \
    -o .build/artwork/render-icon
.build/artwork/render-icon .build/artwork/AppIcon.iconset Artwork/FreeParkingIcon.png
/usr/bin/iconutil -c icns .build/artwork/AppIcon.iconset -o Resources/AppIcon.icns
