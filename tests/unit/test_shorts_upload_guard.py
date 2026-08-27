# R6-2b (#1673): idempotency guard for the YouTube uploader. A scheduler retry or a
# manual+scheduled double-run must NOT publish a duplicate public video. The guard
# persists a per-key marker after a successful upload and short-circuits if it exists.

from scripts.shorts_upload_guard import already_uploaded, marker_path, record_upload


def test_no_marker_means_not_uploaded(tmp_path):
    assert already_uploaded(str(tmp_path), "2026-07-01") is None


def test_record_then_detect_returns_video_id(tmp_path):
    record_upload(str(tmp_path), "2026-07-01", "vid123")
    assert already_uploaded(str(tmp_path), "2026-07-01") == "vid123"


def test_keys_are_independent(tmp_path):
    record_upload(str(tmp_path), "2026-07-01", "vid123")
    assert already_uploaded(str(tmp_path), "2026-07-02") is None


def test_corrupt_marker_is_fail_closed(tmp_path):
    p = marker_path(str(tmp_path), "somekey")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not-json", encoding="utf-8")
    assert already_uploaded(str(tmp_path), "somekey") == "unknown"
