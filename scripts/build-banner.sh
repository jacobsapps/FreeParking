#!/bin/bash
# Deterministic public artwork: no app model, helper, session data or screenshots.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .build/artwork
swiftc -O -parse-as-library -target "$(uname -m)-apple-macosx14.0" \
    Artwork/FreeParkingIcon.swift Artwork/FreeParkingBanner.swift \
    Sources/FreeParking/CarIllustration.swift Sources/FreeParking/DesignSystem.swift \
    scripts/render-banner.swift -o .build/artwork/render-banner
.build/artwork/render-banner Artwork/FreeParkingBanner.png
