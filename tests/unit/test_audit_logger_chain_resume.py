import json
from pathlib import Path

import pytest

from core.round_table.senate_log import LocalJSONAuditLogger


@pytest.fixture
def temp_senate_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "oss_audit_logs"
    monkeypatch.setenv("SENATE_LOG_DIR", str(log_dir))
    return log_dir


def _write_log(log_dir: Path, date_str: str, lines: list[str]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    f = log_dir / f"audit_log_{date_str}.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def test_fresh_logger_starts_with_null_hash(temp_senate_dir):
    logger = LocalJSONAuditLogger()
    assert logger._last_hash == "0" * 64


def test_logger_resumes_chain_from_existing_file(temp_senate_dir):
    _write_log(
        temp_senate_dir,
        "2026-07-20",
        [
            json.dumps({"hash": "1" * 64}),
            json.dumps({"hash": "2" * 64}),
            json.dumps({"hash": "3" * 64}),
        ],
    )
    logger = LocalJSONAuditLogger()
    assert logger._last_hash == "3" * 64


def test_logger_resumes_across_day_boundary(temp_senate_dir):
    _write_log(temp_senate_dir, "2026-07-19", [json.dumps({"hash": "1" * 64})])
    _write_log(temp_senate_dir, "2026-07-20", [json.dumps({"hash": "2" * 64})])

    logger = LocalJSONAuditLogger()
    assert logger._last_hash == "2" * 64


def test_corrupt_last_line_falls_back_to_previous(temp_senate_dir):
    _write_log(
        temp_senate_dir,
        "2026-07-20",
        [
            json.dumps({"hash": "1" * 64}),
            json.dumps({"hash": "2" * 64}),
            "{corrupt_json: true",
        ],
    )
    logger = LocalJSONAuditLogger()
    assert logger._last_hash == "2" * 64


def test_empty_log_file_returns_null_hash(temp_senate_dir):
    f = temp_senate_dir / "audit_log_2026-07-20.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("")

    logger = LocalJSONAuditLogger()
    assert logger._last_hash == "0" * 64


def test_chain_continuity_across_two_instances(temp_senate_dir):
    # This is tested by the existing file test, but let's simulate append.
    _write_log(
        temp_senate_dir,
        "2026-07-20",
        [
            json.dumps({"hash": "A" * 64}),
        ],
    )
    logger1 = LocalJSONAuditLogger()
    assert logger1._last_hash == "A" * 64

    # simulate appending
    with open(
        temp_senate_dir / "audit_log_2026-07-20.jsonl", "a", encoding="utf-8"
    ) as f:
        f.write(json.dumps({"hash": "B" * 64}) + "\n")

    logger2 = LocalJSONAuditLogger()
    assert logger2._last_hash == "B" * 64


def test_non_date_files_ignored(temp_senate_dir):
    _write_log(temp_senate_dir, "2026-07-20", [json.dumps({"hash": "2" * 64})])

    archive_file = temp_senate_dir / "audit_log_archive.jsonl"
    archive_file.write_text(json.dumps({"hash": "9" * 64}) + "\n")

    logger = LocalJSONAuditLogger()
    # It should read from the date file, not the archive file
    assert logger._last_hash == "2" * 64


def test_logger_resumes_across_day_boundary_with_empty_today_file(  # noqa: E501
    temp_senate_dir,
):
    # Yesterday's log has data
    _write_log(
        temp_senate_dir,
        "2026-07-19",
        [json.dumps({"hash": "1" * 64}), json.dumps({"hash": "2" * 64})],
    )
    # Today's log was just created (e.g. at midnight) but is empty
    _write_log(temp_senate_dir, "2026-07-20", [])

    logger = LocalJSONAuditLogger()
    # Fall back to yesterday's last valid hash instead of returning null-hash
    assert logger._last_hash == "2" * 64
