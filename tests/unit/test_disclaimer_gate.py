"""GTM-1 #1801 — the BaFin risk-disclaimer gate on live-arming (placeholder text, pending #1804).

Before the desktop arms LIVE trading the operator MUST have accepted the *current* BaFin
risk-disclaimer. We REUSE the existing first-run acceptance file
(``<AAA_USER_DATA_DIR>/eula_acceptance.json``, written by the desktop wizard and sealed onto the
WORM chain by ``core/eula_seal.py``) — no second acceptance file is invented.

The gate is fail-closed and LOCAL-only, exactly like the #1800 entitlement gate:
  * missing / unreadable file            -> NOT accepted (raise / HTTP 403)
  * recorded disclaimer version < required -> re-acceptance required (raise / HTTP 403)
  * recorded disclaimer version >= required -> accepted (live-enable proceeds)
  * DEPLOYMENT_MODE != LOCAL              -> no-op (cloud/enterprise BYOC handles its own
                                            compliance; byte-identical to before).

TDD note: written FIRST (RED) — the module + wiring under test do not yet exist.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _gate():
    from core.disclaimer import assert_disclaimer_accepted

    return assert_disclaimer_accepted


def _required_version() -> str:
    from core.disclaimer import REQUIRED_DISCLAIMER_VERSION

    return REQUIRED_DISCLAIMER_VERSION


def _error():
    from core.disclaimer import DisclaimerNotAcceptedError

    return DisclaimerNotAcceptedError


def _write_acceptance(tmp: Path, disclaimer_version: str | None) -> None:
    """Write an ``eula_acceptance.json`` recording the given *disclaimer* version.

    Mirrors the real desktop record (see desktop/electron/eula.cjs): all fields are strings.
    ``disclaimer_version`` is the dedicated field the gate reads; ``None`` omits it so the test
    can exercise the missing-field (fail-closed) path.
    """
    data = {
        "document": "eula",
        "version": "1.0.0",
        "text_sha256": "abc123",
        "acceptedAt": "2026-07-09T10:00:00+00:00",
        "app_version": "2.4.0",
        "actor": "operator",
    }
    if disclaimer_version is not None:
        data["disclaimer_version"] = disclaimer_version
    (tmp / "eula_acceptance.json").write_text(json.dumps(data), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Unit: assert_disclaimer_accepted() (LOCAL, fail-closed)
# --------------------------------------------------------------------------- #
def test_no_acceptance_file_raises(tmp_path):
    """No eula_acceptance.json → NOT accepted → raise (fail-closed)."""
    with patch.dict(
        os.environ,
        {"DEPLOYMENT_MODE": "LOCAL", "AAA_USER_DATA_DIR": str(tmp_path)},
    ):
        with pytest.raises(_error()):
            _gate()()


def test_old_version_raises(tmp_path):
    """Acceptance recorded for an OLDER disclaimer version (< required) → re-acceptance
    required after a major update → raise."""
    _write_acceptance(
        tmp_path, disclaimer_version="-old"
    )  # sorts before any real version
    with patch.dict(
        os.environ,
        {"DEPLOYMENT_MODE": "LOCAL", "AAA_USER_DATA_DIR": str(tmp_path)},
    ):
        with pytest.raises(_error()):
            _gate()()


def test_missing_disclaimer_version_field_raises(tmp_path):
    """An acceptance file WITHOUT a disclaimer_version field (e.g. a pre-#1801 record) →
    NOT accepted → raise (fail-closed). The operator must re-accept once the gate ships.
    """
    _write_acceptance(tmp_path, disclaimer_version=None)
    with patch.dict(
        os.environ,
        {"DEPLOYMENT_MODE": "LOCAL", "AAA_USER_DATA_DIR": str(tmp_path)},
    ):
        with pytest.raises(_error()):
            _gate()()


def test_current_version_passes(tmp_path):
    """Acceptance recorded for the required disclaimer version → accepted → no raise."""
    _write_acceptance(tmp_path, disclaimer_version=_required_version())
    with patch.dict(
        os.environ,
        {"DEPLOYMENT_MODE": "LOCAL", "AAA_USER_DATA_DIR": str(tmp_path)},
    ):
        _gate()()  # must NOT raise


def test_unreadable_file_raises_fail_closed(tmp_path):
    """A corrupt / unreadable acceptance file is treated as NOT accepted (fail-closed) —
    a read error must NEVER be silently swallowed into a bypass."""
    (tmp_path / "eula_acceptance.json").write_text(
        "{ this is not json", encoding="utf-8"
    )
    with patch.dict(
        os.environ,
        {"DEPLOYMENT_MODE": "LOCAL", "AAA_USER_DATA_DIR": str(tmp_path)},
    ):
        with pytest.raises(_error()):
            _gate()()


def test_non_local_is_noop(tmp_path):
    """DEPLOYMENT_MODE != LOCAL (cloud/enterprise) → the gate is a no-op even with NO
    acceptance file. A regulated BYOC fund handles its own compliance; cloud is unchanged.
    """
    with patch.dict(
        os.environ,
        {"DEPLOYMENT_MODE": "CLOUD_RUN", "AAA_USER_DATA_DIR": str(tmp_path)},
    ):
        _gate()()  # must NOT raise


def test_deployment_mode_unset_is_noop(tmp_path):
    """DEPLOYMENT_MODE unset → not LOCAL → no-op (dev/CI unaffected)."""
    env = {k: v for k, v in os.environ.items() if k != "DEPLOYMENT_MODE"}
    env["AAA_USER_DATA_DIR"] = str(tmp_path)
    with patch.dict(os.environ, env, clear=True):
        _gate()()  # must NOT raise


# --------------------------------------------------------------------------- #
# API-block: /api/live/enable honours the disclaimer gate (LOCAL, fail-closed → 403)
# --------------------------------------------------------------------------- #
_ENGINE_KEY = "test-engine-key-disclaimer"


def _client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    return TestClient(api_routes.app, raise_server_exceptions=False)


def _pro_entitlement():
    """A live-allowing entitlement so the #1800 tier check passes and we isolate the
    disclaimer gate (which sits right after it)."""
    from core.entitlement.tier import Entitlement, Tier

    return Entitlement(
        tier=Tier.PRO,
        agent_names=tuple("A" for _ in range(9)),
        allow_live=True,
        backtest_months=None,
        xai_enabled=False,
        max_order_value=10000.0,
    )


def test_api_live_enable_403_when_disclaimer_not_accepted(tmp_path):
    """LOCAL + no acceptance file → POST /api/live/enable is refused with 403 (fail-closed),
    before any WORM write, even though the tier allows live."""
    with patch.dict(
        os.environ,
        {
            "ENGINE_API_KEY": _ENGINE_KEY,
            "DEPLOYMENT_MODE": "LOCAL",
            "AAA_USER_DATA_DIR": str(tmp_path),
            "SENATE_LOG_DIR": str(tmp_path),
        },
    ), patch("core.entitlement.resolve_entitlement", return_value=_pro_entitlement()):
        client = _client()
        r = client.post(
            "/api/live/enable",
            headers={"X-Engine-Key": _ENGINE_KEY},
            json={"acknowledgment": "x", "nonce": "n-nodisc"},
        )
        assert r.status_code == 403
        assert "disclaimer" in r.json()["detail"].lower()


def test_api_live_enable_403_for_old_disclaimer_version(tmp_path):
    """LOCAL + acceptance for an OLD disclaimer version → 403 (re-acceptance required
    after a major disclaimer update, e.g. once #1804 lands the real text)."""
    _write_acceptance(tmp_path, disclaimer_version="-old")
    with patch.dict(
        os.environ,
        {
            "ENGINE_API_KEY": _ENGINE_KEY,
            "DEPLOYMENT_MODE": "LOCAL",
            "AAA_USER_DATA_DIR": str(tmp_path),
            "SENATE_LOG_DIR": str(tmp_path),
        },
    ), patch("core.entitlement.resolve_entitlement", return_value=_pro_entitlement()):
        client = _client()
        r = client.post(
            "/api/live/enable",
            headers={"X-Engine-Key": _ENGINE_KEY},
            json={"acknowledgment": "x", "nonce": "n-old"},
        )
        assert r.status_code == 403


def test_api_live_enable_proceeds_when_disclaimer_accepted(tmp_path):
    """LOCAL + acceptance for the required disclaimer version → the disclaimer gate passes and
    /api/live/enable proceeds to the WORM write (201)."""
    _write_acceptance(tmp_path, disclaimer_version=_required_version())
    with patch.dict(
        os.environ,
        {
            "ENGINE_API_KEY": _ENGINE_KEY,
            "DEPLOYMENT_MODE": "LOCAL",
            "AAA_USER_DATA_DIR": str(tmp_path),
            "SENATE_LOG_DIR": str(tmp_path),
        },
    ), patch("core.entitlement.resolve_entitlement", return_value=_pro_entitlement()):
        import core.hitl_gate as hg
        import core.round_table.runner as runner

        old_senate = runner._senate
        runner._senate = None
        hg._fallback_audit_logger = None
        try:
            client = _client()
            r = client.post(
                "/api/live/enable",
                headers={"X-Engine-Key": _ENGINE_KEY},
                json={"acknowledgment": "x", "nonce": "n-ok"},
            )
            assert r.status_code == 201
        finally:
            runner._senate = old_senate
            hg._fallback_audit_logger = None


def test_api_live_enable_not_blocked_by_disclaimer_on_cloud(tmp_path):
    """Non-LOCAL (cloud) → the disclaimer gate is a no-op; /api/live/enable is NOT blocked by
    it (the existing tier/SIP gates still govern cloud). Byte-identical to pre-#1801 behaviour.
    """
    with patch.dict(
        os.environ,
        {
            "ENGINE_API_KEY": _ENGINE_KEY,
            "DEPLOYMENT_MODE": "CLOUD_RUN",
            "SENATE_LOG_DIR": str(tmp_path),
        },
        clear=False,
    ), patch("core.entitlement.resolve_entitlement", return_value=_pro_entitlement()):
        # AAA_USER_DATA_DIR intentionally left as-is / irrelevant: on cloud the gate never reads it.
        import core.hitl_gate as hg
        import core.round_table.runner as runner

        old_senate = runner._senate
        runner._senate = None
        hg._fallback_audit_logger = None
        try:
            client = _client()
            r = client.post(
                "/api/live/enable",
                headers={"X-Engine-Key": _ENGINE_KEY},
                json={"acknowledgment": "x", "nonce": "n-cloud"},
            )
            assert r.status_code == 201
        finally:
            runner._senate = old_senate
            hg._fallback_audit_logger = None


# --------------------------------------------------------------------------- #
# #1804 drop-in: the legal-approved wording + golden byte-identity with the desktop copy
# --------------------------------------------------------------------------- #
def test_disclaimer_text_is_the_ug_wording_golden():
    """The dropped-in #1804 wording names the registered UG (not 'GmbH'), carries the expected
    version, and its SHA-256 is the golden that the desktop copy (desktop/electron/eula.cjs) must
    match byte-for-byte — any drift between engine gate and desktop consent breaks CI on both sides.
    """
    from core.disclaimer import (
        DISCLAIMER_TEXT,
        REQUIRED_DISCLAIMER_VERSION,
        disclaimer_text_sha256,
    )

    assert REQUIRED_DISCLAIMER_VERSION == "1-bafin-execution-only-2026-en-v2"
    assert (
        "Autonomous Asset Management Agents UG (haftungsbeschränkt)" in DISCLAIMER_TEXT
    )
    assert "GmbH" not in DISCLAIMER_TEXT
    assert (
        disclaimer_text_sha256()
        == "28233e5118f9886985a13fed68ab7afe119cc5884dd6991bf0296fd57a11e30b"
    )
