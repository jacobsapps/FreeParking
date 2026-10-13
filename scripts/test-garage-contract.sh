#!/bin/bash
# Offline only: fake desktop, temporary recovery store, preview-isolated UI model.
set -euo pipefail
cd "$(dirname "$0")/.."
TEMP=$(mktemp -d)
trap 'rm -rf -- "$TEMP"' EXIT
python3 Rebuild/tests/garage_contract.py > "$TEMP/responses.json"
swiftc -D FREE_PARKING_DEMO -parse-as-library Sources/FreeParking/Models.swift Sources/FreeParking/PreviewFixtures.swift tests/GarageInteractionTests.swift -o "$TEMP/model-tests"
"$TEMP/model-tests" --backend-contract "$TEMP/responses.json"
