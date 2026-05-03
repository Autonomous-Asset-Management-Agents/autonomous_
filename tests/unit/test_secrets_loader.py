import importlib
import os
import sys
from unittest.mock import MagicMock, patch
import pytest


# Helper to mock the google.cloud.secretmanager stack
def mock_secret_manager(mock_value="test-api-key-value"):
    mock_client_class = MagicMock()
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client

    # A more robust chain for: response.payload.data.decode("utf-8").strip()
    mock_response = MagicMock()

    # We use a real string for the final value to ensure .strip() works and returns a string
    mock_payload = MagicMock()
    mock_data = MagicMock()
    mock_data.decode.return_value = mock_value

    mock_payload.data = mock_data
    mock_response.payload = mock_payload

    mock_client.access_secret_version.return_value = mock_response

    mock_sm_module = MagicMock()
    mock_sm_module.SecretManagerServiceClient = mock_client_class

    # Create the nested structure so "from google.cloud import secretmanager" works
    google = MagicMock()
    google_cloud = MagicMock()
    google.cloud = google_cloud
    google_cloud.secretmanager = mock_sm_module

    return {
        "google": google,
        "google.cloud": google_cloud,
        "google.cloud.secretmanager": mock_sm_module,
    }, mock_client


def test_no_op_without_project_id():
    """load_secrets() should be a no-op when GCP_PROJECT_ID is not set."""
    with patch.dict(os.environ, {}, clear=True):
        import secrets_loader

        importlib.reload(secrets_loader)

        with patch("secrets_loader.logger") as mock_log:
            secrets_loader.load_secrets(project_id=None)
            # Should skip early
            mock_log.debug.assert_any_call(
                "GCP_PROJECT_ID not set - skipping Secret Manager, using .env values."
            )


def test_no_op_when_library_missing():
    """load_secrets() should be a no-op when google-cloud-secret-manager is not installed."""
    with patch.dict(os.environ, {"GCP_PROJECT_ID": "test-project"}, clear=True):
        # Setting the module to None in sys.modules is the standard way to simulate a missing library
        # for 'from ... import ...' statements.
        with patch.dict(sys.modules, {"google.cloud.secretmanager": None}):
            # Ensure parent packages doesn't exist or don't have the attribute
            if "google" in sys.modules:
                del sys.modules["google"]
            if "google.cloud" in sys.modules:
                del sys.modules["google.cloud"]

            import secrets_loader

            importlib.reload(secrets_loader)

            with patch("secrets_loader.logger") as mock_log:
                secrets_loader.load_secrets()
                found = any(
                    "not installed" in str(call)
                    for call in mock_log.debug.call_args_list
                )
                assert found, "Expected debug log about missing library"


def test_secrets_injected_into_env():
    """load_secrets() should inject secrets as env vars when all goes well."""
    mock_val = "test-api-key-value"
    mocks, _ = mock_secret_manager(mock_val)

    with patch.dict(os.environ, {"GCP_PROJECT_ID": "test-project"}, clear=True):
        with patch.dict(sys.modules, mocks):
            import secrets_loader

            importlib.reload(secrets_loader)

            secrets_loader.load_secrets(project_id="test-project")

            # Check if ALPACA_API_KEY was set
            val = os.environ.get("ALPACA_API_KEY")
            assert val == mock_val, f"Expected {mock_val}, got {val}"


def test_not_found_does_not_raise():
    """load_secrets() should log at debug level and continue when a secret is NOT_FOUND."""
    mocks, mock_client = mock_secret_manager()
    mock_client.access_secret_version.side_effect = Exception(
        "NOT_FOUND: secret does not exist"
    )

    with patch.dict(os.environ, {"GCP_PROJECT_ID": "test-project"}, clear=True):
        with patch.dict(sys.modules, mocks):
            import secrets_loader

            importlib.reload(secrets_loader)

            # Must not raise
            secrets_loader.load_secrets(project_id="test-project")


def test_explicit_project_id_used():
    """load_secrets(project_id=...) should use the explicit argument over env var."""
    mocks, mock_client = mock_secret_manager("value")

    with patch.dict(os.environ, {}, clear=True):
        with patch.dict(sys.modules, mocks):
            import secrets_loader

            importlib.reload(secrets_loader)

            secrets_loader.load_secrets(project_id="explicit-project")

            # Verify calls used the explicit project_id
            for call in mock_client.access_secret_version.call_args_list:
                name = call.kwargs.get("request", {}).get("name", "")
                assert "explicit-project" in name
