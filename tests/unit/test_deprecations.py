import warnings
from datetime import datetime
from unittest.mock import MagicMock, patch

import allure
import pytest

from core.ai_components import _using_new_genai_sdk
from core.compliance import ComplianceGuardian


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_using_new_genai_sdk():
    """Assert that the new google.genai SDK is being used."""
    assert (
        _using_new_genai_sdk is True
    ), "The system should be using the new google.genai SDK."


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_datetime_utcnow_deprecation():
    """Assert that calling _log_audit does not raise a DeprecationWarning for datetime.utcnow()."""
    with patch("core.compliance.get_cloud_logger") as mock_get_logger:
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger
        guardian = ComplianceGuardian()

        order = {
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1,
            "price": 150.0,
            "strategy_id": "test_strat",
            "timestamp": datetime.now().timestamp(),
            "is_simulation": True,
        }

        # We explicitly catch DeprecationWarnings to ensure none are raised
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", DeprecationWarning)
            guardian._log_audit(order, True, "Test reason", 0.0)

            # Check if any DeprecationWarning was raised related to datetime.utcnow
            utcnow_warnings = [
                item
                for item in w
                if issubclass(item.category, DeprecationWarning)
                and "utcnow" in str(item.message)
            ]
            assert (
                len(utcnow_warnings) == 0
            ), f"DeprecationWarning for utcnow() was raised: {utcnow_warnings}"
