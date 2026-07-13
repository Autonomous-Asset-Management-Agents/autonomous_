"""G0b (#1050 / AUDIT-008, INV-03): engine host default must be loopback.

Pre-existing gap: `core/engine/__main__.py` defaulted to ``0.0.0.0`` — on a
desktop machine this exposes the trading engine API to the entire LAN (any
device could reach `/panic-sell`-class routes). The audit noted there was NO
test pinning the binding behavior; this file is that test.

Cloud safety of the flip was verified BEFORE the change (the brick's built-in
verification step): every cloud deploy artifact already sets the value
explicitly — `Dockerfile.backend:17` (``ENV ENGINE_HOST=0.0.0.0``),
`cloudbuild-backend-deploy.yaml:41`, `ai_trading_bot/cloudbuild.yaml:74`,
`ai_trading_bot/cloudbuild-engine-only.yaml:51`, `docker-compose*.yml` —
so no deployment relies on the old implicit default.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class HostResolution(unittest.TestCase):
    def test_default_is_loopback(self):
        from core.engine.__main__ import _resolve_host

        env = {k: v for k, v in os.environ.items() if k != "ENGINE_HOST"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_resolve_host(), "127.0.0.1")

    def test_explicit_env_wins_cloud(self):
        from core.engine.__main__ import _resolve_host

        with patch.dict(os.environ, {"ENGINE_HOST": "0.0.0.0"}):
            self.assertEqual(_resolve_host(), "0.0.0.0")

    def test_no_implicit_all_interfaces_default_in_source(self):
        # Source contract: the dangerous implicit default must not return.
        src = (_ROOT / "core" / "engine" / "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn(
            '"ENGINE_HOST", "0.0.0.0"',
            src,
            "0.0.0.0 must never be the implicit default again (AUDIT-008)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
