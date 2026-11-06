# tests/unit/test_cost_model_honesty.py
# N1 — the backtest charges costs the broker does not.
#
# MEASURED: SLIPPAGE_PERCENT = 0.001 (10bps/side) and COMMISSION_PER_TRADE = 0.50, in
# both editions (config.py:247-248, config.oss.py:363-366) and hardcoded AGAIN as a
# fallback in core/simulation.py:24-25. Alpaca charges **$0** commission on US equities.
#
# Only core/simulation.py reads these — this is the BACKTEST, not the live order path. So
# no live behaviour changes. What changes is which world we measure, and every number we
# have quoted comes from that world.
#
# WHY IT MATTERS MORE THAN IT SOUNDS: the error is ~4-5x too high AND it runs in the
# direction that FLATTERS "reduce turnover". Correcting it reportedly moves top-quintile
# net Sharpe 0.758 -> ~1.35 and reverses the model_card's headline (top-Q would BEAT
# EqualWeight 0.96) with ZERO change to the strategy.
#
# ⚠ THAT IS ACCOUNTING, NOT IMPROVEMENT, and this file exists partly to make that
# impossible to forget. The corrected number is ALSO not a measurement: model_card.json:18
# cites corrected_benchmark_results.json, which does not exist in any ref. The honest
# claim after this fix is "the comparison is not currently decidable" — not "we win".
#
# What is factual vs. what is a judgement call:
#   * Commission $0 — FACT. Alpaca does not charge commission on US equities.
#   * Slippage — JUDGEMENT. 10bps/side is a plausible model for illiquid names or size;
#     it is not plausible for S&P 500 large caps in $1-10k clips. The replacement must
#     carry its basis in the code, or we have swapped one unsourced number for another.

from __future__ import annotations

import pytest


class TestTheBrokerChargesWhatTheBrokerCharges:
    def test_commission_is_not_invented(self):
        """Alpaca charges $0 commission on US equities. $0.50/side is not a conservative
        assumption — it is a wrong one, and it is charged on EVERY simulated fill."""
        from config import COMMISSION_PER_TRADE

        assert COMMISSION_PER_TRADE == 0.0, (
            "the backtest charges a commission the broker does not — every net figure "
            "we have quoted is understated by it"
        )

    def test_slippage_is_plausible_for_large_caps(self):
        """10bps/side on S&P 500 large caps in retail clips is ~5x the observable spread.
        This pins the magnitude, not a specific number: anything at or above 10bps is
        modelling a different market than the one we trade."""
        from config import SLIPPAGE_PERCENT

        assert SLIPPAGE_PERCENT < 0.001, (
            "slippage still models an illiquid market; S&P 500 large caps in $1-10k "
            "clips do not cost 10bps/side"
        )
        assert (
            SLIPPAGE_PERCENT > 0.0
        ), "zero slippage is the opposite error — the spread is real and must be paid"


class TestBothEditionsAgree:
    """A cost model that differs between Desktop and Enterprise means two different
    truths about the same strategy."""

    def test_no_drift(self):
        import importlib.util
        import pathlib

        import config

        oss_path = pathlib.Path(config.__file__).parent / "config.oss.py"
        spec = importlib.util.spec_from_file_location("_config_oss_probe", oss_path)
        oss = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(oss)

        assert oss.COMMISSION_PER_TRADE == config.COMMISSION_PER_TRADE
        assert oss.SLIPPAGE_PERCENT == pytest.approx(config.SLIPPAGE_PERCENT)


class TestTheFallbackCannotResurrectTheWrongNumbers:
    """⚠ core/simulation.py hardcodes the SAME wrong values in its ImportError fallback.
    A fix that only touches the configs leaves a second copy that silently reappears the
    moment the config import fails — and then the backtest quietly charges the old costs
    while every config says otherwise. That is worse than the original bug, because it is
    invisible."""

    def test_the_simulation_fallback_matches_config(self):
        import re
        from pathlib import Path

        import config
        import core.simulation as sim

        src = Path(sim.__file__).read_text(encoding="utf-8")
        m = re.search(r"^\s+SLIPPAGE_PERCENT\s*=\s*([0-9.]+)", src, re.M)
        c = re.search(r"^\s+COMMISSION_PER_TRADE\s*=\s*([0-9.]+)", src, re.M)
        assert m and c, "the ImportError fallback constants moved — re-check this test"
        assert float(m.group(1)) == pytest.approx(config.SLIPPAGE_PERCENT)
        assert float(c.group(1)) == config.COMMISSION_PER_TRADE


# ⚠ DELETED: TestTheBasisIsWrittenDown grepped config.py's SOURCE for "Alpaca" and
# "ADR-COST" to assert the numbers carry their basis. That is the same worthless shape as
# the fake equity test in test_gatekeeper_pdt_finra_accurate.py: it asserts on prose, not
# behaviour, and it breaks on reformatting rather than on regression. Whether a constant
# documents its basis is a REVIEW concern, not a unit test. The comment stays in config.py
# because it is worth reading — not because a test forces it.
