# tests/unit/test_report_panel_too_small.py
# The report may only claim its rank is "systematisch getestet" on a panel the test
# actually covered.
#
# THE DEFECT — subtler than a wrong number, which is why it survives an audit:
# every number in the report is real and sourced. The model card's validation
# (cs_ic_mean 0.067, cs_ic_tstat 20.4) was measured across `universe_symbols: 486`.
# The head claims "Diese Rangfolge ist der einzige systematisch getestete Baustein"
# and §10 claims "Systematisch getestet ist an dieser Note nur die relative Rangfolge"
# — UNCONDITIONALLY, whatever the panel size.
#
# But the shipped desktop default is ENVIRONMENT=development -> DEFAULT_SYMBOLS: ten
# symbols, four of them ETFs, and market_scanner truncates to [:10] sorted by
# VOLATILITY on all four paths. So the live report says, verbatim:
#
#     "Unser Modell vergleicht an jedem Stichtag rund 10 Aktien miteinander"
#     "Wie sicher: Mittel. Diese Rangfolge ist der einzige systematisch getestete
#      Baustein der Einschätzung"
#
# Rank 1-of-10-most-volatile is not rank 3-of-486. It is a DIFFERENT ESTIMATOR ON A
# BIASED SAMPLE. The validation does not transfer, and citing it here is a category
# error dressed as a citation — the auditor cannot catch it, because the claim is
# about provenance-of-meaning, not provenance-of-digits.
#
# The floor reuses our own: _MIN_PANEL_FOR_VALIDATION (ADR-RT-LSTM-02), the same threshold at
# which LSTMSignalAgent._vote_on_rank already refuses to vote. If a panel is too thin
# for the board to vote on, it is too thin for the report to call tested.

from __future__ import annotations

import json
from datetime import date

from core.report.fact_set import InMemoryDataContext, build_fact_set
from core.report.render import _MIN_PANEL_FOR_VALIDATION, render


def _card():
    """The REAL shipped model card — the whole point is that its constants describe a
    486-name universe, so a hand-written stub would defeat the test."""
    import os

    import core.report as _report_pkg

    path = os.path.join(os.path.dirname(_report_pkg.__file__), "model_card.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _render_with_panel(n: int) -> str:
    """Render NVDA against a cross-section of exactly n symbols (NVDA ranked top)."""
    xsec = {f"S{i}": float(i) for i in range(n - 1)}
    xsec["NVDA"] = float(n + 10)
    ctx = InMemoryDataContext(
        bars=[(date(2025, 1, 1), 100.0 + i * 0.1) for i in range(300)],
        lstm_series=[(date(2026, 7, 15), 4.1)],
        lstm_cross_section=xsec,
        model_card=_card(),
    )
    fs = build_fact_set("NVDA", date(2026, 7, 15), ctx)
    assert fs.lstm_xsec_n.value == n, "fixture must produce the intended panel size"
    return render(fs, narration=None)


class TestThinPanelMustNotClaimValidation:
    def test_the_card_was_measured_on_a_broad_universe(self):
        """The premise, pinned: the validation is about ~486 names. If this ever
        changes, the whole rule below needs revisiting."""
        card = _card()
        assert card["universe_symbols"] >= 400
        assert card["cs_ic_tstat"] > 0

    def test_a_thin_panel_does_not_claim_the_rank_is_systematically_tested(self):
        """THE assertion. On 10 names the ~486-name validation does not apply."""
        md = _render_with_panel(10)

        assert "systematisch getestet" not in md, (
            "the report claimed its rank is systematically tested on a panel far below "
            "the universe the test was run on — the number is real, it is just not "
            "about this report"
        )

    def test_a_thin_panel_says_so_plainly(self):
        """No spin, and no silence either: the reader must learn WHY the confidence is
        withheld, not just notice that it is missing."""
        md = _render_with_panel(10)

        assert "10" in md
        assert "nicht vergleichbar" in md or "nicht belastbar" in md

    def test_a_thin_panel_still_reports_the_rank_itself(self):
        """The rank is a FACT — it stays. Only the claim ABOUT it is withdrawn. Hiding
        the number would be its own dishonesty."""
        md = _render_with_panel(10)
        assert "Platz 1 von 10" in md


class TestBroadPanelIsUnaffected:
    def test_a_broad_panel_keeps_the_validation_claim(self):
        """The guard must not swallow the case it exists to protect: at ~486 names the
        claim is exactly what the card measured."""
        md = _render_with_panel(_MIN_PANEL_FOR_VALIDATION + 20)

        assert "systematisch getestet" in md

    def test_the_floor_matches_the_one_the_board_uses(self):
        """Consistency, not a second opinion: if a panel is too thin for
        LSTMSignalAgent to vote on (ADR-RT-LSTM-02, PR #2150), it is too thin for the
        report to call tested. The two constants are duplicated across branches today —
        this pins the VALUE so a drift is caught here, and they must be unified into one
        definition when #2150 lands."""
        assert _MIN_PANEL_FOR_VALIDATION == 30

    def test_exactly_at_the_floor_the_claim_holds(self):
        md = _render_with_panel(_MIN_PANEL_FOR_VALIDATION)
        assert "systematisch getestet" in md

    def test_one_below_the_floor_it_does_not(self):
        md = _render_with_panel(_MIN_PANEL_FOR_VALIDATION - 1)
        assert "systematisch getestet" not in md
