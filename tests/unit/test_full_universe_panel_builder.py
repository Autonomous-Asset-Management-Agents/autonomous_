# tests/unit/test_full_universe_panel_builder.py
# #1907 (MLR-7) Increment 2/3 — reproducible offline full-universe panel builder
# (plan §5.4, tests R11-R12).
#
# The builder is an OFFLINE script (scripts/build_full_universe_panel.py): it is never
# imported by the finance runtime, it iterates the per-day PIT universe over a coverage
# window, pulls bars through an injectable fetch function (production default: the
# existing HistoricalDataProvider, Databento-first), and materialises
#   * full_universe_panel.csv   — long format: date,symbol,open,high,low,close,volume
#   * panel_manifest.json       — reproducibility manifest (source, coverage,
#                                 adjust-flags, content hashes) — NO timestamps, so two
#                                 identical runs produce byte-identical artifacts.
#
# Determinism is the whole point: the panel is the data foundation for the #2388
# retrains, and a panel that cannot be reproduced from its manifest is not auditable.

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pandas as pd
import pytest

from scripts.build_full_universe_panel import build_panel

MEMBERSHIP = (
    "symbol,start_date,end_date\n"
    "AAPL,1982-11-30,\n"
    "PLTR,2021-09-20,\n"
    "SIVB,2018-03-12,2023-03-13\n"
)

REMOVALS_ONLY = "symbol,start_date,end_date\nSIVB,2018-03-12,2023-03-13\n"


@pytest.fixture
def membership_csv(tmp_path):
    p = tmp_path / "membership.csv"
    p.write_text(MEMBERSHIP, encoding="utf-8")
    return p


def fake_bars(symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Deterministic synthetic daily bars — a pure function of (symbol, date)."""
    idx = pd.bdate_range(pd.Timestamp(start), pd.Timestamp(end))
    seed = sum(ord(c) for c in symbol)
    base = float(50 + (seed % 100))
    return pd.DataFrame(
        {
            "open": [base + i * 0.5 for i in range(len(idx))],
            "high": [base + i * 0.5 + 1.0 for i in range(len(idx))],
            "low": [base + i * 0.5 - 1.0 for i in range(len(idx))],
            "close": [base + i * 0.5 + 0.25 for i in range(len(idx))],
            "volume": [1000 + seed + i for i in range(len(idx))],
        },
        index=idx,
    )


DEFAULT_START = dt.date(2023, 3, 1)
DEFAULT_END = dt.date(2023, 3, 31)


def _run(membership_csv, out_dir, start=DEFAULT_START, end=DEFAULT_END):
    return build_panel(
        membership_csv=membership_csv,
        start=start,
        end=end,
        out_dir=out_dir,
        bars_fn=fake_bars,
    )


# =========================================================================== #
# 1. R11 — two runs on the identical window are byte-identical (panel AND
#    manifest).
# =========================================================================== #
class TestReproducibility:
    def test_two_runs_produce_identical_artifacts(self, membership_csv, tmp_path):
        out_a = tmp_path / "run_a"
        out_b = tmp_path / "run_b"
        res_a = _run(membership_csv, out_a)
        res_b = _run(membership_csv, out_b)

        panel_a = (out_a / "full_universe_panel.csv").read_bytes()
        panel_b = (out_b / "full_universe_panel.csv").read_bytes()
        assert panel_a == panel_b, "panel must be deterministic (R11)"

        manifest_a = (out_a / "panel_manifest.json").read_bytes()
        manifest_b = (out_b / "panel_manifest.json").read_bytes()
        assert manifest_a == manifest_b, "manifest must be deterministic (R11)"
        assert res_a["panel_sha256"] == res_b["panel_sha256"]

    def test_manifest_carries_no_wall_clock_timestamp(self, membership_csv, tmp_path):
        """Determinism by construction: the manifest pins content (hashes,
        coverage), never the build moment."""
        _run(membership_csv, tmp_path / "out")
        manifest = json.loads(
            (tmp_path / "out" / "panel_manifest.json").read_text(encoding="utf-8")
        )
        dumped = json.dumps(manifest).lower()
        assert "built_at" not in dumped and "timestamp" not in dumped


# =========================================================================== #
# 2. R12 — the manifest states source, coverage, and the adjust policy.
# =========================================================================== #
class TestManifestProvenance:
    def test_manifest_fields(self, membership_csv, tmp_path):
        _run(membership_csv, tmp_path / "out")
        manifest = json.loads(
            (tmp_path / "out" / "panel_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["coverage_start"] == "2023-03-01"
        assert manifest["coverage_end"] == "2023-03-31"
        assert manifest["price_adjustment"]  # adjust policy is stated (R12)
        expected_sha = hashlib.sha256(membership_csv.read_bytes()).hexdigest()
        assert manifest["membership_sha256"] == expected_sha

        panel_bytes = (tmp_path / "out" / "full_universe_panel.csv").read_bytes()
        assert manifest["panel_sha256"] == hashlib.sha256(panel_bytes).hexdigest()

    def test_manifest_is_gate_compatible(self, membership_csv, tmp_path, monkeypatch):
        """The stale gate (core/panel_stale_gate.py) judges panel age by the
        manifest's coverage_end — the builder's manifest must satisfy it."""
        import types

        import core.panel_stale_gate as psg

        _run(membership_csv, tmp_path / "out")
        monkeypatch.setattr(
            psg,
            "get_config",
            lambda: types.SimpleNamespace(
                PANEL_STALE_GATE_ENABLED=True, PANEL_MAX_AGE_DAYS=5
            ),
        )
        allowed, _ = psg.panel_gate_check(
            tmp_path / "out" / "panel_manifest.json", today=dt.date(2023, 4, 3)
        )
        assert allowed is True


# =========================================================================== #
# 3. The panel respects the PIT universe — no bars for names outside their
#    membership window.
# =========================================================================== #
class TestPanelIsPointInTime:
    def test_post_dated_entrant_has_no_rows(self, membership_csv, tmp_path):
        """PLTR joined 2021-09-20 — a March-2023 window includes it; but SIVB left
        2023-03-13, so its rows must stop there, and a pre-2021 window must not
        contain PLTR at all."""
        _run(
            membership_csv,
            tmp_path / "early",
            start=dt.date(2020, 3, 2),
            end=dt.date(2020, 3, 31),
        )
        early = pd.read_csv(tmp_path / "early" / "full_universe_panel.csv")
        assert "PLTR" not in set(early["symbol"]), "look-ahead entrant in the panel"
        assert "SIVB" in set(early["symbol"])
        assert "AAPL" in set(early["symbol"])

    def test_departed_member_rows_stop_at_end_date(self, membership_csv, tmp_path):
        _run(membership_csv, tmp_path / "out")
        panel = pd.read_csv(tmp_path / "out" / "full_universe_panel.csv")
        sivb_dates = sorted(panel.loc[panel["symbol"] == "SIVB", "date"])
        assert sivb_dates, "SIVB was a member until 2023-03-13 — rows expected"
        assert max(sivb_dates) <= "2023-03-13"
        aapl_dates = sorted(panel.loc[panel["symbol"] == "AAPL", "date"])
        assert max(aapl_dates) > "2023-03-13"  # open-ended member runs on


# =========================================================================== #
# 4. Fail-closed: a removals-only table cannot feed the builder.
# =========================================================================== #
class TestRemovalsOnlyTableIsRefused:
    def test_builder_refuses_non_pit_capable_table(self, tmp_path):
        p = tmp_path / "removals_only.csv"
        p.write_text(REMOVALS_ONLY, encoding="utf-8")
        with pytest.raises(ValueError, match="removals-only"):
            _run(p, tmp_path / "out")
