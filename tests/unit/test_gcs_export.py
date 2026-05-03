import sys
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

from scripts.gcs_compliance_export import (  # noqa: E402
    fetch_logs_for_date_async,
    upload_to_gcs_worm,
)


@pytest.mark.anyio
async def test_fetch_logs_for_date_async():
    mock_session = AsyncMock()
    mock_session_maker = MagicMock(return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session

    mock_result = MagicMock()
    mock_record = MagicMock()
    mock_record.id = "123"
    mock_record.event_time = datetime(2026, 3, 5, 12, 0, 0)
    mock_record.event_type = "test_type"
    mock_record.severity = "info"
    mock_record.message = "test trace"
    mock_record.trigger_value = None
    mock_record.threshold_value = None
    mock_record.equity_at_event = None
    mock_record.details_json = {}
    mock_record.user_id = None
    mock_record.is_simulation = False

    mock_result.scalars.return_value.all.return_value = [mock_record]
    mock_session.execute.return_value = mock_result

    result = await fetch_logs_for_date_async(mock_session_maker, "2026-03-05")

    assert len(result) == 1
    assert result[0]["id"] == "123"
    assert result[0]["message"] == "test trace"
    mock_session.execute.assert_called_once()


@patch("scripts.gcs_compliance_export.storage")
def test_upload_to_gcs_worm(mock_storage):
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_storage.Client().bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob
    mock_blob.exists.return_value = False

    test_data = [{"id": "xyz", "message": "compliance trace"}]
    upload_to_gcs_worm(test_data, "2026-03-05")

    # Verify bucket and blob interactions
    mock_bucket.blob.assert_called_with(
        "mifid_traces/2026/03/decision_log_2026-03-05.json"
    )
    mock_blob.upload_from_string.assert_called_once()
