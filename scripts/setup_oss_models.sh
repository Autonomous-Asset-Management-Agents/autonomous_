#!/usr/bin/env bash
# setup_oss_models.sh
# Downloads the "Community Baseline" ML models for the Open Source / self-host version.
#
# Files are pulled from the public GitHub Release listed in
# `data/models_manifest.json`, verified against the manifest's SHA256 hashes,
# and written to `data/` — the directory the engine actually reads from
# (matching what core/strategies/rl_strategy.py expects, NOT a `data/models/`
# subfolder which the engine never reads).
#
# Usage:
#   bash scripts/setup_oss_models.sh
#
# To verify the live release matches the manifest at any time:
#   python scripts/build_models_manifest.py --verify data/models_manifest.json

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${REPO_ROOT}/data"
MANIFEST="${DATA_DIR}/models_manifest.json"
SYNC_SCRIPT="${REPO_ROOT}/scripts/gcs_sync_on_start.py"

echo "=== aaagents-oss Community Baseline Model Setup ==="
echo "Manifest: ${MANIFEST}"
echo "Target:   ${DATA_DIR}"
echo

if [[ ! -f "${MANIFEST}" ]]; then
  echo "❌ Manifest not found: ${MANIFEST}" >&2
  echo "   Expected the file to ship with the repo." >&2
  exit 1
fi

if [[ ! -f "${SYNC_SCRIPT}" ]]; then
  echo "❌ Sync script not found: ${SYNC_SCRIPT}" >&2
  exit 1
fi

# Run the sync script in OSS-mode: GCS_DATA_BUCKET unset triggers the
# GitHub-Release path which reads the manifest, downloads each file with
# SHA256 verification + size cap + URL allow-list, and writes to DATA_DIR.
unset GCS_DATA_BUCKET
DATA_DIR="${DATA_DIR}" python3 "${SYNC_SCRIPT}"

echo
echo "✅ Setup complete. Model files in ${DATA_DIR}/:"
ls -lh "${DATA_DIR}"/*.{pth,zip,pkl,json} 2>/dev/null \
  | grep -E "lstm_model_v2|rl_agent_v5|scaler|model_metadata_v2|rl_stats_v5" || true

echo
echo "Next: docker compose -f docker-compose.oss.yml up -d"
