"""H1 (launch-gate DD): the emergency mass-cancel must target the LIVE broker
account on a live boot — not the paper-defaulted endpoint the singleton captured
from os.getenv at import time.

Root cause: on a desktop LIVE boot os.environ['ALPACA_BASE_URL'] is never set to
the live endpoint (native-engine-manager sets PAPER_TRADING but not the URL;
config.oss.py writes the live URL/keys only into config MODULE GLOBALS). So the
import-captured self.alpaca_base_url/self.alpaca_api_key are PAPER, and the
"flatten everything" emergency stop silently cancels the paper account while live
resting/in-flight orders survive.

The fix resolves the broker target from `config` at TRIP time (edition-neutral via
config.get_secret_str, present in both config.py and config.oss.py).
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeResp:
    status_code = 200
    text = ""


class KillSwitchLiveCancelTarget(unittest.TestCase):
    def setUp(self):
        from core.kill_switch import kill_switch

        self.ks = kill_switch
        self.ks.reset()
        self.addCleanup(self.ks.reset)
        # Simulate the import-time capture on a desktop LIVE boot: env still PAPER.
        self.ks.alpaca_base_url = "https://paper-api.alpaca.markets"
        self.ks.alpaca_api_key = "PAPER_KEY"
        self.ks.alpaca_secret_key = "PAPER_SECRET"
        p = patch("core.kill_switch.send_slack_alert", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _run_cancel_capturing_delete(self):
        captured = {}

        async def _delete(url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return _FakeResp()

        fake_client = MagicMock()
        fake_client.delete = _delete
        fake_ctx = MagicMock()
        fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
        fake_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("core.kill_switch.httpx.AsyncClient", return_value=fake_ctx):
            asyncio.run(self.ks.async_mass_cancel())
        return captured

    def test_live_boot_mass_cancel_targets_live_account(self):
        import config

        with patch.object(
            config, "ALPACA_BASE_URL", "https://api.alpaca.markets"
        ), patch.object(config, "ALPACA_API_KEY", "LIVE_KEY"), patch.object(
            config, "ALPACA_SECRET_KEY", "LIVE_SECRET"
        ):
            captured = self._run_cancel_capturing_delete()

        self.assertEqual(
            captured.get("url"),
            "https://api.alpaca.markets/v2/orders",
            "emergency mass-cancel must hit the LIVE account on a live boot",
        )
        self.assertEqual(captured["headers"]["APCA-API-KEY-ID"], "LIVE_KEY")
        self.assertEqual(captured["headers"]["APCA-API-SECRET-KEY"], "LIVE_SECRET")

    def test_paper_boot_still_targets_paper_account(self):
        import config

        with patch.object(
            config, "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        ), patch.object(config, "ALPACA_API_KEY", "PAPER_KEY"), patch.object(
            config, "ALPACA_SECRET_KEY", "PAPER_SECRET"
        ):
            captured = self._run_cancel_capturing_delete()

        self.assertEqual(
            captured.get("url"), "https://paper-api.alpaca.markets/v2/orders"
        )
        self.assertEqual(captured["headers"]["APCA-API-KEY-ID"], "PAPER_KEY")


if __name__ == "__main__":
    unittest.main()
