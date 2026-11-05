"""#3092 (Live-Safety, Part of #3086): e2e round-trip of the live on/off path.

`enable` → boot with ``PAPER_TRADING=false`` → ``/health`` confirms live →
`disable` → back to paper. Every seam is unit-tested in isolation today
(``test_live_enablement`` for the WORM records,
``test_engine_degrade_lifecycle`` for the degrade branches), but NO test
asserts the *chained* live/paper truth across a boot via the authoritative
``/health.paper_trading``. This closes that gap
(live-switch-safety-audit #06/#07/#14/#16).

Hard risk-stops (loss≥90/trailing≥85) stay a consensus-independent backstop and
are not exercised here — that independence is covered elsewhere.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_KEY = "test-engine-key-3092"


def _client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    return TestClient(api_routes.app)


def _headers():
    return {"X-Engine-Key": _KEY}


class LiveSwitchRoundTrip(unittest.TestCase):
    def setUp(self):
        import config
        import core.hitl_gate as hg
        import core.round_table.runner as runner
        from core.kill_switch import kill_switch

        self.config = config
        self.kill_switch = kill_switch
        self._hg = hg
        self._runner = runner
        self.tmp = tempfile.mkdtemp()

        # Preserve every global we mutate, restore in tearDown.
        self._prev_state = config._config_state
        self._prev_senate = runner._senate
        self._prev_env = {
            k: os.environ.get(k)
            for k in (
                "PATH",
                "PAPER_TRADING",
                "HITL_ENABLED",
                "DEPLOYMENT_MODE",
                "ALPACA_BASE_URL",
                "ENGINE_API_KEY",
                "SENATE_LOG_DIR",
                "AAA_USER_DATA_DIR",
            )
        }

        os.environ["ENGINE_API_KEY"] = _KEY
        os.environ["SENATE_LOG_DIR"] = self.tmp
        os.environ["AAA_USER_DATA_DIR"] = self.tmp
        # Fresh WORM fallback logger against tmp dir (mirror live_enablement).
        runner._senate = None
        hg._fallback_audit_logger = None

        # Start clean: paper, kill-switch un-tripped.
        self.kill_switch.reset()
        os.environ["PAPER_TRADING"] = "true"
        os.environ.pop("DEPLOYMENT_MODE", None)
        config._config_state = config.RuntimeConfigState()

    def tearDown(self):
        for k, v in self._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.config._config_state = self._prev_state
        self._runner._senate = self._prev_senate
        self._hg._fallback_audit_logger = None
        self.kill_switch.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _live(self):
        out = []
        for f in sorted(Path(self.tmp).glob("audit_log_*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    if e.get("event_type") == "live_enablement":
                        out.append(e)
        return out

    def _boot_live(self):
        """Simulate a live boot: flip env + rebuild config state (HITL on so
        the Art-14 gate passes; DEPLOYMENT_MODE not LOCAL so the gate enforces
        rather than silently re-papering)."""
        os.environ["PAPER_TRADING"] = "false"
        os.environ["HITL_ENABLED"] = "true"
        os.environ.pop("DEPLOYMENT_MODE", None)
        self.config._config_state = self.config.RuntimeConfigState()

    def test_enable_boot_live_disable_round_trip(self):
        client = _client()

        # 1) enable → 201 + a single WORM enable record on the chain.
        r = client.post(
            "/api/live/enable",
            headers=_headers(),
            json={
                "acknowledgment": "I accept live trading on my own account",
                "nonce": "sw-3092-1",
            },
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual([e["action"] for e in self._live()], ["enable"])

        # 2) boot live → the authoritative /health reports live (reconciled
        #    truth source the audit flagged as never read across a flip).
        self._boot_live()
        h = client.get("/health", headers=_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json().get("paper_trading"), False)

        # 3) disable → the live→paper leg, asserted on the three things the
        #    audit cares about and that ARE reproducible in-process: the
        #    kill-switch halt (LSR R1 — load-bearing stop), the broker being
        #    redirected to the PAPER endpoint (force_paper_trading), and a WORM
        #    `disable` on the SAME chain as the enable.
        r = client.post(
            "/api/live/disable",
            headers=_headers(),
            json={"acknowledgment": "off", "nonce": "sw-3092-2"},
        )
        self.assertEqual(r.status_code, 201)
        self.assertTrue(self.kill_switch.is_halted())
        self.assertIn("paper-api", os.environ.get("ALPACA_BASE_URL", ""))
        actions = [e["action"] for e in self._live()]
        self.assertEqual(actions, ["enable", "disable"])

        # NOTE (#3092 follow-up): asserting /health.paper_trading round-trip
        # back to True needs a PROCESS-level boot harness. In-process,
        # force_paper_trading rebuilds _config_state by re-reading os.environ,
        # which still carries the paper→live ALPACA keys the boot-live
        # validator swapped in (settings.py::force_paper_trading docstring) —
        # so config.PAPER_TRADING cannot be faithfully reset here. The halt +
        # paper-endpoint redirect + WORM disable above are the reproducible,
        # load-bearing evidence that the disable took effect.

    def test_terminal_boot_degrade_rearms_paper_chained(self):
        """After an armed enable, a TERMINAL live-boot degrade writes a
        compensating disable and leaves the process on paper — so the next
        boot re-arms paper, not silently live. This is the chained counterpart
        to the isolated ``test_engine_degrade_lifecycle`` (which mocks
        ``revoke_live``); here the enable is a real WORM record and the degrade
        heals the chain."""
        from core.engine import __main__ as engine_main
        from scripts.shadow_boot import CheckFailure, ShadowBootResult

        client = _client()
        client.post(
            "/api/live/enable",
            headers=_headers(),
            json={"acknowledgment": "x", "nonce": "sw-3092-term-1"},
        )
        self.assertEqual([e["action"] for e in self._live()], ["enable"])

        self._boot_live()
        self.assertFalse(self.config.PAPER_TRADING)

        engine_main._handle_live_degrade(
            ShadowBootResult(
                ok=False,
                failures=[
                    CheckFailure(
                        "Alpaca",
                        "Alpaca auth rejected (HTTP 401)",
                        terminal=True,
                    )
                ],
            )
        )

        # breadcrumb records a terminal, self-healed degrade
        bc = Path(self.tmp) / "live_boot_outcome.json"
        self.assertTrue(bc.exists())
        crumb = json.loads(bc.read_text(encoding="utf-8"))
        self.assertIs(crumb["terminal"], True)
        self.assertIs(crumb["disable_written"], True)

        # the compensating disable landed on the SAME WORM chain as the enable
        # — so next boot re-arms paper, not silently live (the chained heal).
        actions = [e["action"] for e in self._live()]
        self.assertEqual(actions, ["enable", "disable"])


if __name__ == "__main__":
    unittest.main()
