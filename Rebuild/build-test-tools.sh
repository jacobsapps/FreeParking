#!/bin/bash
# Builds only narrowly scoped test helpers; never launches UI or installs them.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .build/rebuild-module-cache
swiftc -module-cache-path .build/rebuild-module-cache \
  Rebuild/testing/CloseConfirmation.swift -o .build/rebuild-close-confirmation

swiftc -module-cache-path .build/rebuild-module-cache \
  Rebuild/testing/ScopedAppControls.swift -o .build/rebuild-scoped-app-controls
