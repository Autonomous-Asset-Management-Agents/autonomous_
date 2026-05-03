import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from core.kill_switch import KillSwitch, kill_switch


# Resets singleton state for isolated testing
@pytest.fixture(autouse=True)
def reset_kill_switch():
    # Store initial state
    was_initialized = getattr(KillSwitch._instance, "_initialized", False)
    KillSwitch._instance._initialized = False

    yield

    # Clean up state
    if KillSwitch._instance:
        KillSwitch._instance.reset()
        KillSwitch._instance._initialized = was_initialized


class TestKillSwitch:
    def test_singleton_initialization(self):
        """Tests that KillSwitch acts as a singleton."""
        ks1 = KillSwitch()
        ks2 = KillSwitch()
        assert ks1 is ks2
        assert ks1 is kill_switch

    @patch("core.kill_switch.RedisClient.get_sync_redis")
    def test_initialization_redis_failure_fallback(self, mock_redis):
        """Tests that KillSwitch falls back to local state if Redis fails."""
        mock_redis.side_effect = Exception("Redis connection error")

        # Reset initialized flag to trigger __init__ logic
        kill_switch._initialized = False
        kill_switch.__init__()

        assert kill_switch.redis_client is None
        assert kill_switch._initialized is True

    def test_local_halt_and_reset(self):
        """Tests tripping and resetting the kill switch locally."""
        # Ensure clear state
        kill_switch.redis_client = None
        kill_switch.reset()

        assert not kill_switch.is_halted()

        # Trip globally
        with patch("core.kill_switch.send_slack_alert") as mock_slack:
            with patch.object(kill_switch, "_run_async_mass_cancel"):
                kill_switch.trip("Emergency test")

                assert kill_switch.is_halted()
                assert kill_switch._local_halted is True
                mock_slack.assert_called_once()

        # Check exception raising
        with pytest.raises(Exception, match="System is HALTED by Kill Switch"):
            kill_switch.check_halt()

        # Reset globally
        kill_switch.reset()
        assert not kill_switch.is_halted()

    def test_user_halt_and_reset(self):
        """Tests tripping and resetting for a specific user."""
        kill_switch.redis_client = None
        kill_switch.reset()

        user_id = "user_123"

        with patch("core.kill_switch.send_slack_alert"):
            with patch.object(kill_switch, "_run_async_mass_cancel"):
                kill_switch.trip("User specific emergency", user_id=user_id)

                # Should be halted for user_123
                assert kill_switch.is_halted(user_id=user_id)

                # Should NOT be halted globally
                assert not kill_switch.is_halted()

                # Reset user
                kill_switch.reset(user_id=user_id)
                assert not kill_switch.is_halted(user_id=user_id)

    def test_redis_halt_synchronization(self):
        """Tests reading halt state from Redis."""
        mock_redis = MagicMock()
        kill_switch.redis_client = mock_redis
        kill_switch._local_halted = False

        # Scenario 1: Redis returns "true" for system_halted
        mock_redis.get.return_value = "true"
        assert kill_switch.is_halted() is True
        assert kill_switch._local_halted is True

        # Scenario 2: Redis returns "false" globally, but "true" for user
        kill_switch.reset()
        mock_redis.get.return_value = None

        def mock_redis_get(key, *args, **kwargs):
            if key == "system_halted":
                return None
            if key == "system_halted:user_456":
                return "true"
            return None

        mock_redis.get.side_effect = mock_redis_get

        # Explicitly ensure state is clean
        kill_switch._local_halted = False

        assert kill_switch.is_halted() is False
        assert kill_switch.is_halted("user_456") is True

        # Scenario 3: Redis throws Exception
        kill_switch.reset()
        mock_redis.get.side_effect = Exception("Redis connection lost")

        # Should gracefully return False instead of crashing
        assert kill_switch.is_halted() is False

    @pytest.mark.anyio
    async def test_async_mass_cancel_success(self):
        """Tests successful async mass cancel with httpx mock."""
        kill_switch.alpaca_api_key = "test_key"
        kill_switch.alpaca_secret_key = "test_secret"

        mock_response = MagicMock()
        mock_response.status_code = 200

        # Create a mock async client that returns the mock response
        mock_client_instance = AsyncMock()
        mock_client_instance.delete.return_value = mock_response

        # Support async context manager pattern
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            with patch("core.kill_switch.send_slack_alert") as mock_slack:
                await kill_switch.async_mass_cancel()

                # Should not alert on success
                mock_slack.assert_not_called()
                mock_client_instance.delete.assert_called_once()

    @pytest.mark.anyio
    async def test_async_mass_cancel_failure(self):
        """Tests async mass cancel failure handling."""
        kill_switch.alpaca_api_key = "test_key"
        kill_switch.alpaca_secret_key = "test_secret"

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client_instance = AsyncMock()
        mock_client_instance.delete.return_value = mock_response
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            with patch("core.kill_switch.send_slack_alert") as mock_slack:
                await kill_switch.async_mass_cancel()

                # Should alert on failure
                mock_slack.assert_called_once_with("❌ Mass-cancel failed! Status: 500")

    @pytest.mark.anyio
    async def test_async_mass_cancel_user_token(self):
        """Tests async mass cancel with specific user access token."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client_instance = AsyncMock()
        mock_client_instance.delete.return_value = mock_response
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            await kill_switch.async_mass_cancel(access_token="oauth_token_123")

            # Check if token was used in headers
            call_args = mock_client_instance.delete.call_args
            assert call_args is not None
            headers = call_args.kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer oauth_token_123"
