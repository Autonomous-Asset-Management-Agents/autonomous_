# tests/unit/test_fundamentals_report_adapter.py
# §6 reads a schema the feed has never emitted. 14 of 18 keys do not exist.
#
# THE DEFECT (verified by executing both sides):
#   derive_fundamentals() emits: revenue, gross_margin, net_margin, eps_yoy,
#                                operating_cash_flow, total_equity, total_liabilities …
#   render._section_fundamentals reads: revenue_bn, gross_margin_pct, net_margin_pct,
#                                       eps_yoy_pct, op_cash_flow_bn, equity_bn,
#                                       liabilities_bn, debt_to_equity, market_cap_bn …
#   OVERLAP: eps_diluted, filing_date, pe, ps. That is all. 4 of 18.
#
# So §6 renders "Umsatz | n/a Mrd USD | +n/a%" even when the derivation is PERFECT
# (revenue = 215,938,000,000, gross_margin = 0.7107). Filling the PIT cache for 500
# symbols would change NOTHING — it would produce 500 reports of n/a.
#
# WHY NOBODY SAW IT — the pattern of this whole codebase, and of my own bugs today:
# 61 report tests are green. Each side is tested against a HAND-WRITTEN dict in the
# shape it wants (test_report_render.py:39 FUND_FY2026 is written in render's schema;
# the feed's tests assert the feed's schema). No test has ever run derive -> render.
# And the auditor reports 0 flags precisely BECAUSE the data is absent — it verifies
# that numbers PRESENT are sourced, so a missing number is not a claim. "0 audit flags"
# is not evidence of correctness here.
#
# The "NVDA gold report" was rendered from fixtures, never from this pipeline.
#
# This file is the test that was missing: the REAL reader over the REAL committed
# fixture, straight into the REAL renderer, with nothing hand-written in between.

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile

import pytest

from core.report.financials_feed import CachedFundamentalsReader, to_report_fundamentals

_AS_OF = dt.date(2026, 5, 1)
_CLOSE = 198.45  # the as-of close of the committed NVDA fixture


def _real_derived(close=_CLOSE):
    """The REAL reader over the REAL committed fixture. No mocks, no hand-written dict.

    ⚠ The fixture is resolved relative to THIS file (parents[2] == ai_trading_bot/), not
    the CWD. The first version used a bare relative path and only passed when pytest
    happened to run from ai_trading_bot/ — from the repo root it died with
    FileNotFoundError. Papa caught the identical bug in my
    test_round_table_fundamentals_agents.py (review TDD-01, e110021); this is its twin,
    fixed to his pattern. A test that depends on the caller's CWD is not a test, it is a
    coin flip.
    """
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "report"
        / "nvda_financials_vx.json"
    )
    src = json.load(open(fixture, encoding="utf-8"))
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "NVDA.json"), "w") as fh:
        json.dump(src, fh)
    lookup = {"NVDA": close} if close else None
    return CachedFundamentalsReader(cache_dir=d, close_lookup=lookup).get_fundamentals(
        "NVDA", _AS_OF
    )


class TestTheAdapterSpeaksTheRenderersSchema:
    def test_every_key_section_six_reads_is_produced(self):
        """THE contract. §6 renders n/a for any key the adapter fails to emit, and
        renders it silently — no exception, no audit flag, just a hole."""
        import inspect
        import re

        from core.report.render import _section_fundamentals

        wanted = set(
            re.findall(r"g\('(\w+)'", inspect.getsource(_section_fundamentals))
        )
        wanted |= {"fiscal_label", "filing_date"}

        out = to_report_fundamentals(_real_derived())

        missing = sorted(k for k in wanted if k not in out)
        assert not missing, f"§6 reads keys the adapter does not emit: {missing}"

    def test_the_numbers_are_the_real_filing_not_a_fixture(self):
        """Values checked against NVDA's actual FY2026 10-K, so a unit error cannot
        hide behind a self-consistent stub."""
        out = to_report_fundamentals(_real_derived())

        assert out["revenue_bn"] == pytest.approx(215.94, abs=0.01)
        assert out["net_income_bn"] == pytest.approx(120.07, abs=0.01)
        assert out["eps_diluted"] == pytest.approx(4.90, abs=0.01)
        assert out["op_cash_flow_bn"] == pytest.approx(102.72, abs=0.01)
        assert out["equity_bn"] == pytest.approx(157.29, abs=0.01)
        assert out["liabilities_bn"] == pytest.approx(49.51, abs=0.01)


class TestTheUnitTrapThatAlreadyBitOnce:
    """The feed emits FRACTIONS (gross_margin = 0.7107); §6 prints percent. Getting
    this backwards renders a 71.1 % margin as 0.71 % — a real number, sourced, and
    wrong by 100x. The same trap killed my FundamentalsAgent this morning."""

    def test_ratios_become_percent(self):
        out = to_report_fundamentals(_real_derived())

        assert out["gross_margin_pct"] == pytest.approx(71.07, abs=0.05)
        assert out["net_margin_pct"] == pytest.approx(55.60, abs=0.05)
        assert out["op_margin_pct"] == pytest.approx(60.38, abs=0.05)

    def test_growth_becomes_percent(self):
        out = to_report_fundamentals(_real_derived())

        assert out["revenue_yoy_pct"] == pytest.approx(65.47, abs=0.05)
        assert out["net_income_yoy_pct"] == pytest.approx(64.75, abs=0.05)
        assert out["eps_yoy_pct"] == pytest.approx(66.67, abs=0.05)

    def test_the_feed_really_does_emit_fractions(self):
        """Pin the premise. If the feed ever switches to percent, every conversion
        above silently inflates 100x — and every number stays 'sourced'."""
        raw = _real_derived()
        assert 0.0 < raw["gross_margin"] < 1.0
        assert 0.0 < raw["net_margin"] < 1.0


class TestDerivedAndAbsentValues:
    def test_debt_to_equity_is_derived_not_read(self):
        """The feed emits no debt_to_equity — it emits both sides. Reading the key
        returns None forever; that is exactly how the FundamentalsAgent shipped dead."""
        raw = _real_derived()
        assert "debt_to_equity" not in raw

        out = to_report_fundamentals(raw)
        assert out["debt_to_equity"] == pytest.approx(0.31, abs=0.01)

    def test_fiscal_label_is_composed_from_the_filing(self):
        out = to_report_fundamentals(_real_derived())
        assert "2026" in out["fiscal_label"]
        assert out["filing_date"] == "2026-02-25"

    def test_market_cap_is_honest_about_its_unit(self):
        """The §6 line reads 'Marktkapitalisierung ~{market_cap_bn} Bio USD' — a key
        named BILLIONS feeding a label that says TRILLIONS. NVDA is ~4.87 Bio, so the
        rendered value must be ~4.87, not ~4870. Pinned because a 1000x error here is
        a real, sourced-looking number."""
        out = to_report_fundamentals(_real_derived())
        assert out["market_cap_bn"] == pytest.approx(4.87, abs=0.05)

    def test_no_price_means_no_invented_valuation(self):
        """Without a close the feed cannot form pe/ps/market cap. The adapter must
        pass the gap through as None — §6 renders 'n/a', which is true."""
        out = to_report_fundamentals(_real_derived(close=None))

        assert out["pe"] is None
        assert out["ps"] is None
        assert out["market_cap_bn"] is None
        # …while the price-independent facts survive
        assert out["revenue_bn"] == pytest.approx(215.94, abs=0.01)

    def test_none_input_is_none_output(self):
        """A cold cache stays an honest gap — never an empty dict that §6 would render
        as a table full of n/a while claiming a filing behind it."""
        assert to_report_fundamentals(None) is None


class TestEndToEndThroughTheRealRenderer:
    """The test that was missing. derive -> adapt -> render, nothing hand-written."""

    def test_section_six_shows_real_numbers(self):
        from datetime import date

        from core.report.fact_set import InMemoryDataContext, build_fact_set
        from core.report.render import render

        ctx = InMemoryDataContext(
            bars=[(date(2025, 1, 1), 100.0 + i * 0.1) for i in range(300)],
            lstm_series=[(_AS_OF, 4.1)],
            lstm_cross_section={"NVDA": 4.1},
            fundamentals=to_report_fundamentals(_real_derived()),
        )
        md = render(build_fact_set("NVDA", _AS_OF, ctx), narration=None)
        # B5: bis zur naechsten Ueberschrift, nicht bis "## 7." — der alte
        # Terminator schnitt nach der Umnummerierung zwei Abschnitte zu viel mit.
        sec = md.split("## 4. Fundamentaldaten")[1].split("\n## ")[0]

        assert "215.94" in sec, "§6 still renders n/a for a perfectly derived filing"
        assert "71.1" in sec
        assert "55.6" in sec
        assert "4.90" in sec
        assert "n/a" not in sec, "a fully populated filing must leave no holes in §6"
