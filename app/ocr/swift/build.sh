#!/bin/bash
# Build the Vision OCR shim.
#
#   bash app/ocr/swift/build.sh
#
# Output: app/ocr/swift/bin/vision_ocr  (a build artifact — do not commit it)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$HERE/bin"
OUT="$OUT_DIR/vision_ocr"

mkdir -p "$OUT_DIR"

# -target is pinned so the binary does not silently require a newer macOS than
# the one it was built on. Apple Silicon only; adjust if this ever runs on x86.
swiftc -O \
    -target arm64-apple-macos13.0 \
    -framework Vision \
    -framework ImageIO \
    -framework CoreGraphics \
    -framework Foundation \
    -o "$OUT" \
    "$HERE/VisionOCR.swift"

echo "built: $OUT"
