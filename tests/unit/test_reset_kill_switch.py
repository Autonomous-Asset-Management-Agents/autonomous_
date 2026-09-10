# tests/unit/test_reset_kill_switch.py

import os
import runpy
import sys
from unittest.mock import MagicMock, patch

import pytest


def test_reset_kill_switch_no_redis(monkeypatch, capsys):
    monkeypatch.setenv("REDIS_URL", "")
    script_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../scripts/reset_kill_switch.py")
    )
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(script_path, run_name="__main__")

    assert exc_info.value.code == 1
    out, err = capsys.readouterr()
    assert "REDIS_URL is not set" in out


def test_reset_kill_switch_success(monkeypatch, capsys):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    script_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../scripts/reset_kill_switch.py")
    )

    mock_redis_client = MagicMock()
    mock_redis = MagicMock()
    mock_redis_client.get_sync_redis.return_value = mock_redis

    # Since run_path creates a new module namespace, we need to patch sys.modules or use a different patching strategy.
    # The script imports RedisClient from core.redis_client. Let's patch it there.
    with patch("core.redis_client.RedisClient", mock_redis_client):
        runpy.run_path(script_path, run_name="__main__")

    out, err = capsys.readouterr()
    assert "KillSwitch has been reset" in out
    mock_redis.delete.assert_called_with("system_halted")
