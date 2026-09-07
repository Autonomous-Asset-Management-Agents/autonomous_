"""Idempotency guard for the daily YouTube Shorts upload (R6-2b, #1673).

A Windows Task Scheduler retry after a transient failure, or a manual run that overlaps
the scheduled one, must NOT publish a second identical video. We persist a per-key marker
after a *successful* upload and short-circuit if it already exists.

No third-party (google) dependency here on purpose, so the guard is unit-testable in
isolation from the upload/OAuth modules.
"""

import json
import pathlib
from datetime import datetime, timezone


def marker_path(root: str, key: str) -> pathlib.Path:
    """Path to the per-key 'already uploaded' marker file."""
    return pathlib.Path(root) / "snapshots" / ".uploaded" / f"{key}.json"


def already_uploaded(root: str, key: str):
    """Return the recorded video_id if ``key`` was already uploaded, else ``None``.

    Fail-closed: a present-but-corrupt marker returns ``"unknown"`` (still truthy) so a
    re-run is blocked rather than risking a duplicate upload.
    """
    marker = marker_path(root, key)
    if not marker.exists():
        return None
    try:
        return (
            json.loads(marker.read_text(encoding="utf-8")).get("video_id") or "unknown"
        )
    except Exception:
        return "unknown"


def record_upload(root: str, key: str, video_id: str) -> None:
    """Persist the per-key marker after a successful upload."""
    marker = marker_path(root, key)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "video_id": video_id,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
