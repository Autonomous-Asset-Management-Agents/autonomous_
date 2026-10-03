# tests/unit/test_eod_assessment.py
"""#2652 (Epic #2655, Weg 1) — EOD sector-constraint proposal + Ed25519 approval token.

Archon-audit rules under test: the proposal/token carry ONLY relative caps (an absolute $
payload is rejected as ``non_relative``); the token embeds a TTL and verification rejects
``expired``; the whole dispatch is dormant behind EOD_CONSTRAINT_ASSESSMENT_ENABLED (default
False) and delivery reuses the existing daily_report channels (no new mailer)."""

from unittest.mock import MagicMock, patch

import pytest

from core.engine import eod_assessment as eod
from core.governance.portfolio_constraints import load_pending_proposal


@pytest.fixture(autouse=True)
def _isolated_user_data(tmp_path, monkeypatch):
    # key file + constraint store live under USER_DATA — isolate per test
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    eod._last_dispatch_day = None
    yield


class TestProposal:
    def test_relative_headroom_heuristic(self):
        # weight + 5pp headroom, rounded UP to the next 5% step, hard-capped at 30%.
        p = eod.build_constraint_proposal({"Technology": 0.28, "Energy": 0.12})
        assert p == {"Technology": 0.30, "Energy": 0.20}

    def test_only_relative_inputs_survive(self):
        p = eod.build_constraint_proposal(
            {"Tech": 0.10, "Junk": "x", "Absolute": 5000, "Zero": 0}
        )
        assert p == {"Tech": 0.15}

    def test_empty_weights_empty_proposal(self):
        assert eod.build_constraint_proposal({}) == {}


class TestApprovalToken:
    def test_round_trip(self):
        token = eod.mint_approval_token({"Technology": 0.20})
        payload, err = eod.verify_approval_token(token)
        assert err is None
        assert payload["caps"] == {"Technology": 0.20}
        assert payload["exp"] > payload["iat"]

    def test_tampered_token_is_invalid(self):
        token = eod.mint_approval_token({"Technology": 0.20})
        payload_b64, sig_b64 = token.split(".")
        forged = (
            eod._b64e(eod._b64d(payload_b64).replace(b"0.2", b"0.9")) + "." + sig_b64
        )
        payload, err = eod.verify_approval_token(forged)
        assert payload is None and err == "invalid"

    def test_expired_token_is_rejected(self):
        token = eod.mint_approval_token({"Technology": 0.20}, ttl_hours=-1)
        payload, err = eod.verify_approval_token(token)
        assert payload is None and err == "expired"

    def test_absolute_caps_are_non_relative(self):
        # a token minted with absolute $ values must be refused (stale-proposal guard)
        token = eod.mint_approval_token({"Technology": 5000})
        payload, err = eod.verify_approval_token(token)
        assert payload is None and err == "non_relative"


class TestDispatch:
    def _cfg(self, enabled):
        cfg = MagicMock()
        cfg.EOD_CONSTRAINT_ASSESSMENT_ENABLED = enabled
        cfg.EOD_CONSTRAINT_TOKEN_TTL_HOURS = 12
        return cfg

    def test_disabled_flag_is_a_noop(self):
        with patch("config.get_config", return_value=self._cfg(False)):
            res = eod.maybe_run_eod_assessment({"Technology": 0.28})
        assert res == {"ran": False, "reason": "disabled"}
        assert load_pending_proposal() is None

    def test_no_sector_data_warns_and_skips(self, caplog):
        with patch("config.get_config", return_value=self._cfg(True)), caplog.at_level(
            "WARNING"
        ):
            res = eod.maybe_run_eod_assessment({})
        assert res["reason"] == "no_sector_data"
        assert any("sector-source brick pending" in m for m in caplog.messages)

    def test_enabled_stages_pending_and_dispatches_once_per_day(self):
        report_cfg = MagicMock(any_channel=True)
        with patch("config.get_config", return_value=self._cfg(True)), patch(
            "core.daily_report.dispatch_report", return_value={"webhook": True}
        ) as dispatch, patch("core.daily_report.load_config", return_value=report_cfg):
            res = eod.maybe_run_eod_assessment({"Technology": 0.28})
            again = eod.maybe_run_eod_assessment({"Technology": 0.28})
        assert res["ran"] is True and res["delivery"] == {"webhook": True}
        assert again == {"ran": False, "reason": "already_ran_today"}
        dispatch.assert_called_once()
        text = dispatch.call_args[0][0]
        assert "30%" in text and "Token:" in text  # relative caps + the approval token
        pending = load_pending_proposal()
        assert pending["caps"] == {"Technology": 0.30}
        assert pending["actor"] == "eod_assessment"
        assert pending["token_sha256"]
