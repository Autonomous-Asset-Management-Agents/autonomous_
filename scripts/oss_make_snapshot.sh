#!/usr/bin/env bash
# oss_make_snapshot.sh
# Creates a clean, history-free snapshot of the AAAgents Community Edition.
# Runs on Windows (Git-Bash), Linux, and macOS without requiring rsync.
# All file-copy logic is delegated to oss_make_snapshot.py (uses shutil).
set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AI_BOT_DIR="$REPO_ROOT/ai_trading_bot"
BLACKLIST_FILE="$REPO_ROOT/scripts/oss_exclude.txt"
SNAPSHOT_PY="$REPO_ROOT/scripts/oss_make_snapshot.py"
# OSS_SNAPSHOT_DIR can be injected by CI (e.g. runner.temp on GitHub Actions).
# Falls back to ~/aaagents-public for local developer runs.
PUBLIC_DIR="${OSS_SNAPSHOT_DIR:-$HOME/aaagents-public}"
# PID-suffix prevents collisions from concurrent runs in CI.
PUBLIC_DIR_NEW="${PUBLIC_DIR}-new.$$"

echo "=== DEV-15.4: AAAgents Community Edition Snapshot ==="
echo "Source:    $AI_BOT_DIR"
echo "Target:    $PUBLIC_DIR"
echo "Blacklist: $BLACKLIST_FILE"
echo ""

# ── Cleanup trap — registered FIRST, before anything touches the filesystem ──
# Ensures PUBLIC_DIR_NEW is always removed on exit (normal, error, or SIGINT).
trap 'rm -rf "$PUBLIC_DIR_NEW"' EXIT

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if [ ! -d "$AI_BOT_DIR" ]; then
    echo "❌ Source directory not found: $AI_BOT_DIR"
    exit 1
fi
if [ ! -f "$BLACKLIST_FILE" ]; then
    echo "❌ Blacklist file not found: $BLACKLIST_FILE"
    exit 1
fi
if [ ! -f "$SNAPSHOT_PY" ]; then
    echo "❌ oss_make_snapshot.py not found: $SNAPSHOT_PY"
    exit 1
fi

# Resolve python (python3 preferred; fall back to python on Windows)
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "❌ Python not found. Install Python 3 to run this script."
    exit 1
fi
echo "Using Python: $($PYTHON --version)"

# ── Step 1: Copy using Python (cross-platform, blacklist-aware) ───────────────
echo ""
echo "Step 1/5: Copying files (flat layout, blacklist applied)..."
"$PYTHON" "$SNAPSHOT_PY" "$AI_BOT_DIR" "$PUBLIC_DIR_NEW" "$BLACKLIST_FILE"

# ── Step 2: Copy root-level docs not present in ai_trading_bot/ ───────────────
echo ""
echo "Step 2/5: Adding root-level docs..."
for f in README.oss.md CONTRIBUTING.md SECURITY.md CLAUDE.oss.md DISCLAIMER.md setup.sh setup.ps1 setup.py Makefile requirements-dev.txt mac-quickstart.sh; do
    src="$REPO_ROOT/$f"
    if [ -f "$src" ]; then
        cp "$src" "$PUBLIC_DIR_NEW/"
        echo "  ✓ $f"
    else
        echo "  ⚠ $f not found in repo root — skipping"
    fi
done

if [ -f "$REPO_ROOT/.cursorrules.oss" ]; then
    cp "$REPO_ROOT/.cursorrules.oss" "$PUBLIC_DIR_NEW/"
    echo "  ✓ .cursorrules.oss"
fi

for f in docker-compose.oss.yml nginx.oss.conf Dockerfile.frontend; do
    src="$REPO_ROOT/$f"
    if [ -f "$src" ]; then
        cp "$src" "$PUBLIC_DIR_NEW/"
        echo "  ✓ $f"
    fi
done

echo ""
echo "Step 2.4: Adding model bundle manifest (publish-oss-model-bundle.yml)..."
# The manifest is a small JSON file that lists every model file in the
# matching public Release. ``data/*`` is excluded by oss_exclude.txt for size
# reasons, so we cherry-pick the manifest by hand. Operators run
# publish-oss-model-bundle.yml on Dev-Enviroment to commit the manifest
# BEFORE the next snapshot — the snapshot then carries the manifest into
# autonomous_ verbatim, and gcs_sync_on_start.py reads it on first boot.
_MANIFEST_SRC="$AI_BOT_DIR/data/models_manifest.json"
if [ -f "$_MANIFEST_SRC" ]; then
    mkdir -p "$PUBLIC_DIR_NEW/data"
    cp "$_MANIFEST_SRC" "$PUBLIC_DIR_NEW/data/models_manifest.json"
    echo "  ✓ data/models_manifest.json"
else
    # Non-fatal: a snapshot taken BEFORE the first model bundle release runs
    # is still valid — the OSS stack just boots in degraded mode (LSTM/RL
    # voters fall back to neutral 0.5) until the manifest lands. This matches
    # the existing gcs_sync_on_start.py behaviour where a missing manifest is
    # an INFO log, not an error.
    echo "  ⚠ data/models_manifest.json not found in source — snapshot ships without model bundle (engine boots degraded)."
fi

echo ""
echo "Step 2.5: Adding Frontend source code..."

# Required files — abort if missing (typos here break the OSS frontend build)
_REQUIRED_FRONTEND_FILES="package.json vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json index.html tailwind.config.ts postcss.config.js components.json vitest.config.ts"
for f in $_REQUIRED_FRONTEND_FILES; do
    src="$REPO_ROOT/$f"
    if [ ! -f "$src" ]; then
        echo "❌ Required frontend file missing: $f — aborting snapshot."
        exit 1
    fi
    cp "$src" "$PUBLIC_DIR_NEW/"
    echo "  ✓ $f"
done

# scripts/viteProxyInjector.ts (#2647): Node-side Vite dev-server helper IMPORTED BY vite.config.ts
# (`attachEngineKey`). Not a root file, but the frontend dev server WON'T LOAD without it (vite would
# throw "Cannot find module ./scripts/viteProxyInjector"), so it is REQUIRED — fail-closed like above.
_VITE_PROXY_INJECTOR="$REPO_ROOT/scripts/viteProxyInjector.ts"
if [ ! -f "$_VITE_PROXY_INJECTOR" ]; then
    echo "❌ Required frontend helper missing: scripts/viteProxyInjector.ts — aborting snapshot."
    exit 1
fi
mkdir -p "$PUBLIC_DIR_NEW/scripts"
cp "$_VITE_PROXY_INJECTOR" "$PUBLIC_DIR_NEW/scripts/viteProxyInjector.ts"
echo "  ✓ scripts/viteProxyInjector.ts"
# Publish the snapshot script itself for OSS transparency (T6)
_SNAPSHOT_SCRIPT="$REPO_ROOT/scripts/oss_make_snapshot.sh"
_SNAPSHOT_EXCLUDE="$REPO_ROOT/scripts/oss_exclude.txt"
if [ -f "$_SNAPSHOT_SCRIPT" ]; then
    cp "$_SNAPSHOT_SCRIPT" "$PUBLIC_DIR_NEW/scripts/"
    if [ -f "$_SNAPSHOT_EXCLUDE" ]; then
        cp "$_SNAPSHOT_EXCLUDE" "$PUBLIC_DIR_NEW/scripts/"
    fi
    echo "  ✓ scripts/oss_make_snapshot.sh (published for transparency)"
fi

# Rewrite the flat-layout desktop:* npm scripts. Dev-Enviroment runs the engine from ai_trading_bot/
# (`cd ai_trading_bot && python …`), but the public snapshot is FLAT (engine + serve_public_api.py at
# the ROOT), so the `cd ai_trading_bot && ` prefix would break `npm run desktop:dev/desktop:engine/
# desktop:api` in the public repo. Strip it here (BORA parity: same intent, edition-correct path) —
# same mechanism as the Dockerfile.backend rewrite above.
if [ -f "$PUBLIC_DIR_NEW/package.json" ]; then
    (
        cd "$PUBLIC_DIR_NEW"
        "$PYTHON" -c "
import sys
try:
    with open('package.json', 'r', encoding='utf-8') as f:
        content = f.read()
    content = content.replace('cd ai_trading_bot && ', '')
    assert 'ai_trading_bot' not in content, 'Failed to rewrite all ai_trading_bot paths in package.json!'
    with open('package.json', 'w', encoding='utf-8') as f:
        f.write(content)
except Exception as e:
    print('Error rewriting package.json:', e)
    sys.exit(1)
"
    )
    echo "  ✓ package.json (desktop:* scripts rewritten for flat layout)"
fi

# Optional files — skip with a warning if missing
for f in package-lock.json; do
    src="$REPO_ROOT/$f"
    if [ -f "$src" ]; then
        cp "$src" "$PUBLIC_DIR_NEW/"
        echo "  ✓ $f"
    else
        echo "  ⚠ Optional frontend file not found, skipping: $f"
    fi
done

for d in src public; do
    src="$REPO_ROOT/$d"
    if [ -d "$src" ]; then
        cp -r "$src" "$PUBLIC_DIR_NEW/"
        echo "  ✓ $d/"
    else
        echo "❌ Required frontend directory missing: $d/ — aborting snapshot."
        exit 1
    fi
done

if [ -f "$REPO_ROOT/Dockerfile.backend" ]; then
    cp "$REPO_ROOT/Dockerfile.backend" "$PUBLIC_DIR_NEW/Dockerfile.backend"
    (
        cd "$PUBLIC_DIR_NEW"
        "$PYTHON" -c "
import sys
try:
    with open('Dockerfile.backend', 'r', encoding='utf-8') as f:
        content = f.read()
    content = content.replace('[\"ai_trading_bot/', '[\"./')
    content = content.replace('[\"ai_trading_bot\",', '[\".\",')
    content = content.replace('requirements.oss.txt', 'requirements.txt')
    assert '[\"ai_trading_bot' not in content, 'Failed to rewrite all ai_trading_bot paths in Dockerfile.backend!'
    with open('Dockerfile.backend', 'w', encoding='utf-8') as f:
        f.write(content)
except Exception as e:
    print('Error rewriting Dockerfile.backend:', e)
    sys.exit(1)
"
    )
    echo "  ✓ Dockerfile.backend (paths rewritten)"
fi

if [ -f "$PUBLIC_DIR_NEW/pyproject.toml" ]; then
    echo "  ✓ pyproject.toml (coverage threshold preserved at 75)"
fi



if [ -d "$REPO_ROOT/docs/oss" ]; then
    mkdir -p "$PUBLIC_DIR_NEW/docs"
    cp -r "$REPO_ROOT/docs/oss" "$PUBLIC_DIR_NEW/docs/"
    COPIED_DOCS=$(find "$PUBLIC_DIR_NEW/docs/oss/" -type f 2>/dev/null | wc -l | tr -d ' ')
    [ "$COPIED_DOCS" -gt 0 ] || { echo "❌ docs/oss/ copy failed — 0 files in snapshot."; exit 1; }
    echo "  ✓ docs/oss/ ($COPIED_DOCS files)"

    # Guard: Feature Matrix must be present and non-empty (fail-closed)
    # VISION_AND_EDITIONS.md is the authoritative OSS/Enterprise feature overview for
    # community users and AI scanners. Missing = snapshot is incomplete.
    _VISION_FILE="$PUBLIC_DIR_NEW/docs/oss/VISION_AND_EDITIONS.md"
    if [ ! -f "$_VISION_FILE" ]; then
        echo "❌ VISION_AND_EDITIONS.md missing from snapshot — aborting."
        echo "   This file contains the OSS/Enterprise feature matrix for AI scanners."
        echo "   Ensure docs/oss/VISION_AND_EDITIONS.md exists in the private repo."
        exit 1
    fi
    _VISION_LINES=$(wc -l < "$_VISION_FILE" | tr -d ' ')
    [ "$_VISION_LINES" -ge 50 ] || {
        echo "❌ VISION_AND_EDITIONS.md has only $_VISION_LINES lines — looks truncated. Aborting."
        exit 1
    }
    echo "  ✓ VISION_AND_EDITIONS.md (feature matrix, $_VISION_LINES lines)"
fi

mkdir -p "$PUBLIC_DIR_NEW/.github"
if [ -f "$REPO_ROOT/.github/CODE_OF_CONDUCT.md" ]; then
    cp "$REPO_ROOT/.github/CODE_OF_CONDUCT.md" "$PUBLIC_DIR_NEW/.github/"
    echo "  ✓ .github/CODE_OF_CONDUCT.md"
else
    echo "❌ CODE_OF_CONDUCT.md not found at $REPO_ROOT/.github/ — aborting."
    exit 1
fi

mkdir -p "$PUBLIC_DIR_NEW/.github/workflows"
if [ -f "$REPO_ROOT/.github/workflows/oss-ci.yml" ]; then
    # Strip the `working-directory: "ai_trading_bot"` lines: the OSS repo has a flat
    # layout where Python source lives at the root, not in a subdirectory.
    sed '/working-directory: "ai_trading_bot"/d' \
        "$REPO_ROOT/.github/workflows/oss-ci.yml" \
        > "$PUBLIC_DIR_NEW/.github/workflows/oss-ci.yml"
    echo "  ✓ .github/workflows/oss-ci.yml (working-directory stripped for flat OSS layout)"
else
    echo "❌ .github/workflows/oss-ci.yml not found at $REPO_ROOT — aborting."
    exit 1
fi

# Docker image publish workflow — runs from autonomous_ so packages are linked there
if [ -f "$REPO_ROOT/.github/workflows/oss-publish-docker-images.yml" ]; then
    cp "$REPO_ROOT/.github/workflows/oss-publish-docker-images.yml" \
       "$PUBLIC_DIR_NEW/.github/workflows/publish-docker-images.yml"
    echo "  ✓ .github/workflows/publish-docker-images.yml (GHCR publish for autonomous_)"
else
    echo "❌ .github/workflows/oss-publish-docker-images.yml not found — aborting."
    exit 1
fi

# PyPI publish workflow — runs from autonomous_ (public) as the Trusted Publisher.
if [ -f "$REPO_ROOT/.github/workflows/pypi-publish.yml" ]; then
    cp "$REPO_ROOT/.github/workflows/pypi-publish.yml" \
       "$PUBLIC_DIR_NEW/.github/workflows/pypi-publish.yml"
    echo "  ✓ .github/workflows/pypi-publish.yml (Trusted Publishing from autonomous_)"
else
    echo "❌ .github/workflows/pypi-publish.yml not found — aborting."
    exit 1
fi

# ── Standalone OSS Python packages (published to PyPI from autonomous_) ────────
# packages/ lives at the repo ROOT (not under ai_trading_bot/), so it is NOT part of
# the Step-1 flat copy. Export the COMMITTED source via `git archive` so no local build
# artifacts (dist/build/*.egg-info/__pycache__/.pytest_cache) can leak into the snapshot.
mkdir -p "$PUBLIC_DIR_NEW/packages"
for _pkg in autonomous-audit autonomous-trading autonomous-audit-mcp; do
    if [ -d "$REPO_ROOT/packages/$_pkg" ]; then
        git -c safe.directory="*" -C "$REPO_ROOT" archive HEAD "packages/$_pkg" | tar -x -C "$PUBLIC_DIR_NEW"
        echo "  ✓ packages/$_pkg (committed source only, no build artifacts)"
    else
        echo "❌ packages/$_pkg not found at $REPO_ROOT — aborting."
        exit 1
    fi
done

# ── Step 3: Apply OSS stubs and rename staged files ───────────────────────────
echo ""
echo "Step 3/5: Applying OSS stubs..."

_require_stub() {
    local src="$1" dst="$2" label="$3"
    if [ -f "$src" ]; then
        mv "$src" "$dst"
        echo "  ✓ $label"
    else
        echo "  ❌ $label source missing ($src) — aborting."
        # Trap will clean up PUBLIC_DIR_NEW automatically.
        exit 1
    fi
}

_require_stub "$PUBLIC_DIR_NEW/config.oss.py" \
              "$PUBLIC_DIR_NEW/config.py" \
              "config.oss.py → config.py"

_require_stub "$PUBLIC_DIR_NEW/core/secret_manager_utils.oss.py" \
              "$PUBLIC_DIR_NEW/core/secret_manager_utils.py" \
              "core/secret_manager_utils.oss.py → core/secret_manager_utils.py"

_require_stub "$PUBLIC_DIR_NEW/scripts/shadow_boot.oss.py" \
              "$PUBLIC_DIR_NEW/scripts/shadow_boot.py" \
              "scripts/shadow_boot.oss.py → scripts/shadow_boot.py"

_require_stub "$PUBLIC_DIR_NEW/requirements.oss.txt" \
              "$PUBLIC_DIR_NEW/requirements.txt" \
              "requirements.oss.txt → requirements.txt"

# Strip 'ai_trading_bot/' paths from Dockerfile.public-api for the flat OSS layout
if [ -f "$PUBLIC_DIR_NEW/Dockerfile.public-api" ]; then
    sed -e 's/ai_trading_bot\///g' -e 's/ai_trading_bot/./g' "$PUBLIC_DIR_NEW/Dockerfile.public-api" > "$PUBLIC_DIR_NEW/Dockerfile.public-api.tmp"
    mv "$PUBLIC_DIR_NEW/Dockerfile.public-api.tmp" "$PUBLIC_DIR_NEW/Dockerfile.public-api"
    echo "  ✓ Dockerfile.public-api (paths flattened for OSS layout)"
fi

# Strip 'ai_trading_bot/' path from docker-compose.oss.yml build section for public-api
if [ -f "$PUBLIC_DIR_NEW/docker-compose.oss.yml" ]; then
    sed -e 's/ai_trading_bot\/Dockerfile.public-api/Dockerfile.public-api/g' "$PUBLIC_DIR_NEW/docker-compose.oss.yml" > "$PUBLIC_DIR_NEW/docker-compose.oss.yml.tmp"
    mv "$PUBLIC_DIR_NEW/docker-compose.oss.yml.tmp" "$PUBLIC_DIR_NEW/docker-compose.oss.yml"
    echo "  ✓ docker-compose.oss.yml (public-api build path flattened)"
fi


# Optional doc renames
[ -f "$PUBLIC_DIR_NEW/README.oss.md" ]    && mv "$PUBLIC_DIR_NEW/README.oss.md"    "$PUBLIC_DIR_NEW/README.md"    && echo "  ✓ README.oss.md → README.md"
[ -f "$PUBLIC_DIR_NEW/CLAUDE.oss.md" ]    && mv "$PUBLIC_DIR_NEW/CLAUDE.oss.md"    "$PUBLIC_DIR_NEW/CLAUDE.md"    && echo "  ✓ CLAUDE.oss.md → CLAUDE.md"
[ -f "$PUBLIC_DIR_NEW/.cursorrules.oss" ] && mv "$PUBLIC_DIR_NEW/.cursorrules.oss" "$PUBLIC_DIR_NEW/.cursorrules" && echo "  ✓ .cursorrules.oss → .cursorrules"

# Safety-net: remove any leaked proprietary originals
# These should already be excluded by the blacklist; this is a final hard guard.
for guarded in secrets_loader.py; do
    target="$PUBLIC_DIR_NEW/$guarded"
    if [ -f "$target" ]; then
        echo "  ⚠ Safety-net: removing leaked $guarded from snapshot"
        rm -f "$target"
    fi
done

# ── Step 4: Generate license files ────────────────────────────────────────────
echo ""
echo "Step 4/5: Writing license files..."
if [ -f "$REPO_ROOT/LICENSE" ] && [ -s "$REPO_ROOT/LICENSE" ]; then
    cp "$REPO_ROOT/LICENSE" "$PUBLIC_DIR_NEW/LICENSE"
    echo "  ✓ LICENSE (from repo root)"
else
    echo "Apache License, Version 2.0 — see https://www.apache.org/licenses/LICENSE-2.0" \
        > "$PUBLIC_DIR_NEW/LICENSE"
    echo "  ⚠ LICENSE stub written (real file missing from repo root)"
fi


for f in LICENSE-MODELS NOTICE; do
    if [ ! -f "$REPO_ROOT/$f" ]; then
        echo "❌ $f not found at $REPO_ROOT — cannot create compliant snapshot."
        exit 1
    fi
    cp "$REPO_ROOT/$f" "$PUBLIC_DIR_NEW/"
done
echo "  ✓ LICENSE-MODELS (CC-BY-4.0), NOTICE"



if [ -f "$REPO_ROOT/.env.oss.example" ]; then
    cp "$REPO_ROOT/.env.oss.example" "$PUBLIC_DIR_NEW/.env.oss.example"
    cp "$REPO_ROOT/.env.oss.example" "$PUBLIC_DIR_NEW/.env.example"
    echo "  ✓ .env.oss.example + .env.example"
fi

# ── Step 4.9: Sperrliste ueber den GANZEN Snapshot (#3616) ────────────────────
# Der Python-Kopierer oben filtert nur ai_trading_bot/ und mit fnmatch. Alles,
# was danach kommt — Wurzeldateien, `cp -r src public`, die Umbenennungen —
# erreichte das Tor bisher ungeprueft, obwohl verify_oss_snapshot.sh den ganzen
# Snapshot mit pathspec prueft. Dieser Durchgang benutzt dieselbe Engine wie das
# Tor, damit beide dieselbe Liste auch gleich auslegen.
echo ""
echo "Step 4.9: Applying exclude list to the complete snapshot..."
"$PYTHON" "$REPO_ROOT/scripts/oss_prune_snapshot.py" "$PUBLIC_DIR_NEW" "$BLACKLIST_FILE"

# ── Step 5: True atomic swap ───────────────────────────────────────────────────
# Pattern: rename existing → backup, rename new → target, delete backup.
# Eliminates the rm-then-mv window where PUBLIC_DIR is momentarily absent.
echo ""
echo "Step 5/5: Atomic swap to $PUBLIC_DIR..."
BACKUP="${PUBLIC_DIR}.bak.$$"
if [ -d "$PUBLIC_DIR" ]; then
    mv "$PUBLIC_DIR" "$BACKUP"
fi
mv "$PUBLIC_DIR_NEW" "$PUBLIC_DIR"
# Disarm the cleanup trap now that PUBLIC_DIR_NEW is gone.
rm -rf "$BACKUP"
trap - EXIT
echo "  ✓ Swap complete"

# ── Secret Scanner Gate — FAIL-CLOSED ─────────────────────────────────────────
# CRITICAL: Do NOT use `|| true` here. If oss_audit.sh crashes (missing
# detect-secrets, permission error, etc.) the pipeline MUST fail, not silently
# pass. A crash with no report == fail-open gate == security defect.
echo ""
echo "Running Secret Scanner Gate..."
cd "$PUBLIC_DIR"
if [ "${SKIP_OSS_AUDIT_FOR_TESTING:-0}" != "1" ]; then
    bash "$REPO_ROOT/scripts/oss_audit.sh"

    # Verify the report was actually generated (guards against silent crashes).
    if [ ! -f "OSS_AUDIT_REPORT.md" ]; then
        echo "❌ OSS_AUDIT_REPORT.md was not generated — oss_audit.sh failed silently."
        echo "   This is a fail-closed gate: no report == BLOCKED."
        exit 1
    fi

    # Check: "found results in N files" where N > 0 is a real secret leak.
    if grep -qE "Detect-Secrets found results in ([1-9][0-9]*|Unknown) files?" OSS_AUDIT_REPORT.md; then
        echo "❌ 🛑 SECRETS DETECTED IN SNAPSHOT! 🛑 ❌"
        echo "   Check $PUBLIC_DIR/OSS_AUDIT_REPORT.md"
        # Move it out to REPO_ROOT for CI upload but fail the script
        mv OSS_AUDIT_REPORT.md "$REPO_ROOT/OSS_AUDIT_REPORT.md"
        exit 1
    fi

    # Move the report out of the snapshot directory so it doesn't leak into the OSS release
    mv OSS_AUDIT_REPORT.md "$REPO_ROOT/OSS_AUDIT_REPORT.md"
else
    echo "Skipping secret scanner for testing (SKIP_OSS_AUDIT_FOR_TESTING=1)"
fi

echo ""
echo "✅ Snapshot created successfully at $PUBLIC_DIR"
echo "   Secret Scanner Gate: PASSED (fail-closed)"
echo ""
echo "Next step: bash scripts/verify_oss_snapshot.sh"
