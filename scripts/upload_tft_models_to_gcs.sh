#!/bin/bash
# =============================================================================
# upload_tft_models_to_gcs.sh — model-provenance Issue 3 (cloud provisioning)
#
# Uploads a STAGED per-symbol TFT serving tree (<SYM>/checkpoint.pt + metadata.json +
# the matched training_ds, produced by build_tft_serving_bundle.py) to gs://<bucket>/tft/,
# so Cloud Run instances sync the per-symbol checkpoints at boot.
#
# A dedicated <SYM>/-tree uploader — the existing upload_models_to_gcs.sh is a flat
# `data/` rsync with no per-symbol concept (W-3). Operator tool; never run at boot.
#
# Usage: upload_tft_models_to_gcs.sh <serving-tree-dir> [bucket-name]
# =============================================================================

set -euo pipefail

SRC="${1:?Usage: upload_tft_models_to_gcs.sh <serving-tree-dir> [bucket-name]}"
BUCKET_NAME="${2:-aaa-trading-bot-models}"
BUCKET_URI="gs://${BUCKET_NAME}"

echo "🚀 Uploading TFT serving tree '${SRC}' → ${BUCKET_URI}/tft/ ..."

if ! command -v gcloud &> /dev/null; then
    echo "❌ gcloud CLI is not installed or not in PATH." >&2
    exit 1
fi

if [ ! -d "$SRC" ]; then
    echo "❌ Source serving tree '$SRC' does not exist." >&2
    exit 1
fi

if ! gcloud storage ls "$BUCKET_URI" &> /dev/null; then
    echo "⚠️  Bucket $BUCKET_URI does not exist — creating it (europe-west3)..."
    gcloud storage buckets create "$BUCKET_URI" --location=europe-west3
else
    echo "✅ Bucket $BUCKET_URI found."
fi

# The SRC is the already-staged serving tree (build_tft_serving_bundle.py excludes seed
# checkpoints + _v2_train_logs); the exclude here is belt-and-suspenders so a hand-pointed
# raw training tree never leaks the non-serving artifacts.
echo "📂 Syncing per-symbol checkpoints into ${BUCKET_URI}/tft/ ..."
gcloud storage rsync "$SRC" "${BUCKET_URI}/tft/" -R \
    -x ".*/_v2_train_logs/.*|.*/checkpoint_v2_seed[0-9]+.*\.pt$"

echo "🎉 TFT upload complete."
echo "💡 Set GCS_DATA_BUCKET=${BUCKET_URI} so the Cloud Run engine syncs gs://.../tft/ at boot."
