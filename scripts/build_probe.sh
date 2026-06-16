#!/usr/bin/env bash
# scripts/build_probe.sh
# ---------------------------------------------------------------------------
# Dry-run wheel builder for seismograph-probe.
#
# Strategy
# --------
# The monorepo root already has a pyproject.toml covering the full server
# stack (gateway, engine, tests, etc.).  The probe-only build is configured
# in pyproject_probe.toml.
#
# This script:
#   1. Backs up pyproject.toml -> pyproject.toml.bak
#   2. Copies pyproject_probe.toml -> pyproject.toml
#   3. Runs `python -m build --wheel --no-isolation` to produce the wheel
#   4. Restores pyproject.toml from the backup
#   5. Reports the wheel contents and dependency metadata
#
# Usage
# -----
#   cd <repo-root>
#   bash scripts/build_probe.sh
#
# Prerequisites
# -------------
#   pip install hatchling build
#
# Output
# ------
#   dist/seismograph_probe-1.0.0-py3-none-any.whl
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DIST_DIR="$REPO_ROOT/dist"
BACKUP="$REPO_ROOT/pyproject.toml.bak"

# Guard: ensure build tools are present before touching any files.
python3 -c "import hatchling, build" 2>/dev/null || {
    echo "ERROR: hatchling and/or build not installed."
    echo "Run: pip install hatchling build"
    exit 1
}

echo "=== seismograph-probe wheel build ==="
echo "Repo root : $REPO_ROOT"
echo "Output dir: $DIST_DIR"
echo ""

# Step 1: back up original pyproject.toml
echo "[1/5] Backing up pyproject.toml -> pyproject.toml.bak"
cp "$REPO_ROOT/pyproject.toml" "$BACKUP"

# Ensure we always restore, even on error.
restore() {
    echo ""
    echo "[restore] Restoring pyproject.toml from backup..."
    mv "$BACKUP" "$REPO_ROOT/pyproject.toml"
    echo "[restore] Done."
}
trap restore EXIT

# Step 2: swap in probe build config
echo "[2/5] Copying pyproject_probe.toml -> pyproject.toml"
cp "$REPO_ROOT/pyproject_probe.toml" "$REPO_ROOT/pyproject.toml"

# Step 3: build the wheel
mkdir -p "$DIST_DIR"
echo "[3/5] Building wheel (no-isolation)..."
python3 -m build --wheel --no-isolation --outdir "$DIST_DIR"

# Step 4: restore is handled by the trap above.
echo "[4/5] Wheel built. Restoration queued via trap."

# Step 5: report wheel contents
echo ""
echo "[5/5] Wheel contents verification:"
WHEEL=$(ls -t "$DIST_DIR"/seismograph_probe-*.whl 2>/dev/null | head -1)
if [[ -z "$WHEEL" ]]; then
    echo "ERROR: no seismograph_probe-*.whl found in $DIST_DIR"
    exit 1
fi
echo "  Wheel: $WHEEL"
echo ""
echo "  --- Files inside wheel ---"
python3 -m zipfile -l "$WHEEL" | awk "NR>1" | sort

echo ""
echo "  --- Dependency metadata ---"
python3 -c "
import sys, zipfile
whl = sys.argv[1]
with zipfile.ZipFile(whl) as z:
    meta = next((n for n in z.namelist() if n.endswith('/METADATA')), None)
    if meta:
        for line in z.read(meta).decode().splitlines():
            if line.startswith(('Name:', 'Version:', 'Summary:',
                                 'Requires-Python:', 'Requires-Dist:',
                                 'Provides-Extra:')):
                print('   ', line)
" "$WHEEL"

echo ""
echo "  --- Probe-only check ---"
FOREIGN=$(python3 -m zipfile -l "$WHEEL" | awk 'NR>1 {print $NF}' \
    | grep -v '^probe/' | grep -v '^seismograph_probe' \
    | grep -v '\.dist-info/' | grep '\.' || true)
if [[ -z "$FOREIGN" ]]; then
    echo "  PASS: wheel contains only probe/ and .dist-info/ entries."
else
    echo "  FAIL: unexpected non-probe files in wheel:"
    echo "$FOREIGN"
    exit 1
fi

echo ""
echo "=== Build complete ==="
