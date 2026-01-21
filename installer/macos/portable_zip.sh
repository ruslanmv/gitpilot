#!/usr/bin/env bash
# Create portable zip archive for macOS
set -euo pipefail

VERSION="${1:-dev}"

echo "Creating macOS portable archive (version: $VERSION)..."
python installer/build/package_portable.py --version "$VERSION"
echo "Done!"
