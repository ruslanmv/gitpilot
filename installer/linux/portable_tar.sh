#!/usr/bin/env bash
# Create portable tar.gz archive for Linux
set -euo pipefail

VERSION="${1:-dev}"

echo "Creating Linux portable archive (version: $VERSION)..."
python installer/build/package_portable.py --version "$VERSION"
echo "Done!"
