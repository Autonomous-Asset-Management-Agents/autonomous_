from unittest.mock import patch

import pytest

try:
    from core.sim.safety import assert_not_sim
except ImportError:
    from ai_trading_bot.core.sim.safety import assert_not_sim


SAFETY_MOD = assert_not_sim.__module__


def test_assert_not_sim_raises_when_sim_mode_true():
    with patch(f"{SAFETY_MOD}.get_config") as mock_config:
        mock_config.return_value.SIM_MODE = True

        with pytest.raises(
            RuntimeError,
            match="CRITICAL: Attempted to construct real Alpaca Client while in SIM_MODE.",
        ):
            assert_not_sim()


def test_assert_not_sim_passes_when_sim_mode_false():
    with patch(f"{SAFETY_MOD}.get_config") as mock_config:
        mock_config.return_value.SIM_MODE = False
        assert_not_sim()  # Should not raise
        assert_not_sim()
