# tests/unit/test_report_no_fake_hold_badge.py
# The reader BADGE must not fabricate HALTEN over an abstaining note.
#
# THE DEFECT (verified by tracing the real path, not by reading):
# The desktop Reports reader shows a "HALTEN" pill in the header while the note's
# §Einschätzung / §Empfehlung both say "Für diesen Stichtag liegt keine
# Einschätzung vor." The pill is fed by SpecialistReport.recommendation, which in
# the report-only whole-universe path (StockSpecialistAgent.refresh_report_only)
# is NEVER computed — it stays at the dataclass default "hold" (report.py:65).
# api_routes._serialize_specialist_report's getattr(r, "recommendation", "hold")
# default never even fires: the attribute is always present. So the fabrication is
# the uncomputed decision-field default surfacing next to a note that abstained.
#
# THE FIX: the note already computes its verdict from the SINGLE validated field
# (the cross-sectional rank) via render._rec_parts and ABSTAINS to None with no
# rank. render.rec_code exposes exactly that verdict as a stable buy/hold/sell code
# (None on abstention), the report-only attach carries it as report_recommendation
# (a report-only field — the consensus `recommendation` is never touched), and the
# badge reads it. No rank -> None -> the pill shows nothing. A real mid-field
# HALTEN (rank present) still shows HALTEN.

from __future__ import annotations

from datetime import date

import pytest

from core.report.fact_set import Fact, FactSet
from core.specialist.report import SpecialistReport


def _fact(v, src="test"):
    return Fact(v, src)


def _factset(*, xsec_pct=None, xsec_rank=None, xsec_n=None, close=None):
    """A FactSet with the cross-sectional rank present or absent — everything else
    an honest gap (the shipped report-only default's shape)."""
    return FactSet(
        symbol="MSFT",
        as_of=date(2026, 7, 15),
        company=_fact(None, "company feed not wired"),
        close=_fact(close, "close"),
        pullback_series=_fact([], "bars"),
        pullback_pct=_fact(None, "bars"),
        ma20=_fact(None, "bars"),
        ma50=_fact(None, "bars"),
        ma100=_fact(None, "bars"),
        ma200=_fact(None, "bars"),
        high_52w=_fact(None, "bars"),
        low_52w=_fact(None, "bars"),
        high_1m=_fact(None, "bars"),
        low_1m=_fact(None, "bars"),
        rvol_20d=_fact(None, "bars"),
        rvol_63d=_fact(None, "bars"),
        lstm_score=_fact(None, "lstm_panel"),
        lstm_own_percentile=_fact(None, "lstm_panel"),
        lstm_xsec_percentile=_fact(xsec_pct, "lstm_panel cross-section @ 2026-07-15"),
        lstm_xsec_rank=_fact(xsec_rank, "lstm_panel cross-section @ 2026-07-15"),
        lstm_xsec_n=_fact(xsec_n, "lstm_panel cross-section @ 2026-07-15"),
        lstm_score_trend=_fact([], "lstm_panel"),
        lstm_hist_min=_fact(None, "lstm_panel"),
        lstm_hist_max=_fact(None, "lstm_panel"),
        lstm_hist_mean=_fact(None, "lstm_panel"),
        lstm_hist_std=_fact(None, "lstm_panel"),
        model_card=_fact({}, "model_card"),
        fundamentals=_fact(None, "not wired"),
        news_items=_fact([], "news"),
        pros=_fact([], "cards"),
        cons=_fact([], "cards"),
    )


# =========================================================================== #
# 1. render.rec_code: the note's own verdict as a stable code. None on abstain.
# =========================================================================== #
class TestRecCodeMirrorsTheNote:
    def test_no_rank_means_none_not_a_neutral_hold(self):
        from core.report.render import rec_code

        assert rec_code(_factset()) is None

    def test_a_top_ranked_stock_is_buy(self):
        from core.report.render import rec_code

        assert rec_code(_factset(xsec_pct=95.7, xsec_rank=3, xsec_n=486)) == "buy"

    def test_a_bottom_ranked_stock_is_sell(self):
        from core.report.render import rec_code

        assert rec_code(_factset(xsec_pct=2.1, xsec_rank=476, xsec_n=486)) == "sell"

    def test_a_real_mid_field_stock_is_hold(self):
        """The guard must not swallow the case it protects: a genuine mid-field
        HALTEN (rank present) still resolves to 'hold' and still shows the pill."""
        from core.report.render import rec_code

        assert rec_code(_factset(xsec_pct=50.0, xsec_rank=243, xsec_n=486)) == "hold"

    def test_rank_without_n_still_abstains(self):
        """The note abstains unless verb AND bucket AND rank AND n are all present
        (render._verdict_line). rec_code holds the same line."""
        from core.report.render import rec_code

        assert rec_code(_factset(xsec_pct=50.0, xsec_rank=243, xsec_n=None)) is None


# =========================================================================== #
# 2. The report-only attach carries the verdict WITHOUT touching the consensus
#    `recommendation` field (report-only contract, stock_specialist.py:431-434).
# =========================================================================== #
class TestAttachCarriesVerdictNotFabrication:
    def _attach_with_factset(self, monkeypatch, fs):
        from core.report.auditor import audit as _audit
        from core.report.pipeline import ReportBundle
        from core.stock_specialist import StockSpecialistAgent

        bundle = ReportBundle(
            fact_set=fs, markdown="# MSFT\n\n...", audit=_audit("", fs)
        )
        # build_report_bundle is imported lazily inside _attach_v2_report -> patch it
        # on its home module so the lazy import resolves to the fake.
        monkeypatch.setattr(
            "core.report.pipeline.build_report_bundle",
            lambda *a, **k: bundle,
        )
        agent = StockSpecialistAgent("MSFT", gemini_api_key="x")
        report = SpecialistReport(symbol="MSFT")
        # reader=object(): patched build_report_bundle ignores it, so no live engine
        # reader is constructed.
        agent._attach_v2_report(report, {}, reader=object(), narrate=False)
        return report

    def test_abstaining_note_carries_no_badge_and_leaves_consensus_untouched(
        self, monkeypatch
    ):
        report = self._attach_with_factset(monkeypatch, _factset())
        assert report.report_recommendation is None  # the pill shows nothing
        assert report.recommendation == "hold"  # consensus default UNTOUCHED

    def test_ranked_note_carries_its_verdict(self, monkeypatch):
        report = self._attach_with_factset(
            monkeypatch, _factset(xsec_pct=95.7, xsec_rank=3, xsec_n=486)
        )
        assert report.report_recommendation == "buy"


# =========================================================================== #
# 3. The serializer surfaces the badge-truth ONLY under the flag; abstain -> null.
# =========================================================================== #
class TestSerializerSurfacesTheBadgeTruth:
    def _serializer(self):
        try:
            from core.engine.api_routes import _serialize_specialist_report

            return _serialize_specialist_report
        except Exception as exc:  # pragma: no cover - local-only env guard
            pytest.skip(f"engine app deps unavailable in this environment: {exc}")

    def test_abstain_is_null_and_real_hold_survives_under_flag(self):
        import types as _types
        from unittest.mock import patch

        serialize = self._serializer()
        abstain = SpecialistReport(symbol="AAPL")  # report_recommendation defaults None
        real_hold = SpecialistReport(symbol="AAPL", report_recommendation="hold")

        cfg_on = _types.SimpleNamespace(REPORT_GENERATOR_V2_ENABLED=True)
        with patch("core.engine.api_routes.config.get_config", return_value=cfg_on):
            dto_abstain = serialize("AAPL", abstain)
            dto_real = serialize("AAPL", real_hold)

        assert (
            dto_abstain["report_recommendation"] is None
        )  # -> frontend null -> no pill
        assert (
            dto_real["report_recommendation"] == "hold"
        )  # real mid-field HALTEN survives

    def test_flag_off_is_byte_identical_key_absent(self):
        import types as _types
        from unittest.mock import patch

        serialize = self._serializer()
        real_hold = SpecialistReport(symbol="AAPL", report_recommendation="hold")
        # REPORT_GENERATOR_V2_ENABLED now DEFAULTS to True (config.py:1028-1030 /
        # config.oss.py:1014-1016, owner constellation activation). The OFF path is
        # still valid + env-reachable, so force the flag OFF explicitly rather than
        # relying on the (now flipped) default. Flag off -> additive key absent ->
        # BORA byte-identity holds.
        cfg_off = _types.SimpleNamespace(REPORT_GENERATOR_V2_ENABLED=False)
        with patch("core.engine.api_routes.config.get_config", return_value=cfg_off):
            dto_off = serialize("AAPL", real_hold)
        assert "report_recommendation" not in dto_off
