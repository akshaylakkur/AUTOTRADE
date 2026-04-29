#!/usr/bin/env bash
# =============================================================================
# Project ÆON — Distribution builder
# =============================================================================
# Usage: ./dist.sh [release_base_url]
#
# Builds dist/install.sh and dist/aeon.tar.gz.
# Upload the entire dist/ folder to your hosting endpoint.
#
# Example:
#   ./dist.sh https://aeon.example.com
#   rsync -av dist/ user@host:/var/www/aeon/
#
# Then anyone can install with:
#   curl -fsSL https://aeon.example.com/install.sh | bash
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

RELEASE_BASE="${1:-https://aeon.example.com}"
DIST_DIR="dist"

echo "Building ÆON distribution for ${RELEASE_BASE}"
echo

# --- Clean ---
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

# --- Build tarball (project source) ---
STAGING="/tmp/aeon-dist-$$"
rm -rf "$STAGING"
mkdir -p "$STAGING/aeon"

for item in * .env.example .gitignore; do
    [[ -e "$item" ]] && cp -R "$item" "$STAGING/aeon/"
done

# Strip dev/build artifacts
rm -rf "$STAGING/aeon/.venv" "$STAGING/aeon/.git" "$STAGING/aeon/.claude" \
       "$STAGING/aeon/data" "$STAGING/aeon/cold_storage" "$STAGING/aeon/dist" \
       "$STAGING/aeon/__pycache__" "$STAGING/aeon/dist.sh"

find "$STAGING" -name '*.pyc' -delete
find "$STAGING" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGING" -name '.DS_Store' -delete

tar -czf "$DIST_DIR/aeon.tar.gz" -C "$STAGING" aeon
rm -rf "$STAGING"

# --- Build install.sh with baked-in release base ---
sed "s|AEON_RELEASE_BASE=\"\${AEON_RELEASE_BASE:-https://aeon.example.com}\"|AEON_RELEASE_BASE=\"\${AEON_RELEASE_BASE:-${RELEASE_BASE}}\"|" \
    install.sh > "$DIST_DIR/install.sh"
chmod +x "$DIST_DIR/install.sh"

# --- Checksums ---
if command -v shasum &>/dev/null; then
    shasum -a 256 "$DIST_DIR/aeon.tar.gz" "$DIST_DIR/install.sh" > "$DIST_DIR/checksums.sha256"
fi

# --- Report ---
echo "dist/"
for f in "$DIST_DIR"/*; do
    size=$(du -h "$f" | cut -f1)
    printf "  %-8s  %s\n" "$size" "$(basename "$f")"
done

echo
echo "Upload dist/ to your host, then:"
echo
echo "  curl -fsSL ${RELEASE_BASE}/install.sh | bash"
echo
