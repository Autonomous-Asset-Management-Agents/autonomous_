#!/bin/bash
# =============================================================================
# gcs_sync_on_start.sh — Cloud Run startup data sync
# Lädt ML-Modelle und persistierte Daten von GCS herunter bevor die Engine
# startet. Auf Cloud Run ist das Dateisystem ephemer; GCS ist die Datenquelle.
#
# Aufgerufen von: Dockerfile CMD (vor python -m core.engine)
# Umgebungsvariablen:
#   GCS_DATA_BUCKET  — GCS-Bucket-Pfad, z. B. gs://aaa-trading-bot-models
#                      Wenn nicht gesetzt: Sync wird übersprungen (lokaler Betrieb)
# =============================================================================

set -euo pipefail

GCS_BUCKET="${GCS_DATA_BUCKET:-}"
DATA_DIR="${DATA_DIR:-data}"

if [ -z "$GCS_BUCKET" ]; then
  echo "[gcs_sync] GCS_DATA_BUCKET nicht gesetzt — kein Sync (lokaler Betrieb)"
  exit 0
fi

echo "[gcs_sync] Syncing data/ von ${GCS_BUCKET}/data/ ..."

# Erstelle data/-Verzeichnis falls es nicht existiert
mkdir -p "$DATA_DIR"

# Sync von GCS → lokal (nur existierende Dateien herunterladen, nicht löschen)
# rsync-Modus: GCS ist die Quelle, lokal ist das Ziel
if gsutil -m rsync -r "${GCS_BUCKET}/data/" "./${DATA_DIR}/" 2>/dev/null; then
  echo "[gcs_sync] ✅ Sync erfolgreich ($(ls -1 ${DATA_DIR}/ | wc -l) Dateien)"
else
  echo "[gcs_sync] ⚠️  GCS Sync fehlgeschlagen oder Bucket leer — Engine startet ohne vorherige Daten"
  echo "[gcs_sync]    (Erster Start oder Bucket noch nicht befüllt — ist OK)"
fi
