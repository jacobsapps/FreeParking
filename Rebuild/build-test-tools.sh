#!/bin/bash
# Builds only the narrowly scoped disposable-window confirmation helper.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .build/rebuild-module-cache
swiftc -module-cache-path .build/rebuild-module-cache \
  Rebuild/testing/CloseConfirmation.swift -o .build/rebuild-close-confirmation
