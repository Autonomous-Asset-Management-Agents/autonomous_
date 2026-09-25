# tests/unit/test_coverage_selector.py — dynamic specialist-report coverage (Epic #1998, sub #2630).
# Task 1 asserts the config flags via the get_config() interface the engine actually uses
# (config.py -> RuntimeConfigState field; config.oss.py -> SimpleNamespace(**globals())).
# Task 2 covers the pure selector.
import config as _cfg
from core.engine.coverage_selector import select_specialist_coverage

BASE = ["AAPL", "MSFT"]


def test_coverage_flags_via_get_config_defaults():
    c = _cfg.get_config()
    # #2839: default True = shipped desktop launcher profile (desktop is master).
    assert c.SPECIALIST_COVERAGE_DYNAMIC is True
    assert c.SPECIALIST_TOP_N_CONVICTION == 20
    assert c.SPECIALIST_COVER_POSITIONS is True


def test_positions_always_covered():
    out = select_specialist_coverage(["BNY", "WDAY"], [], BASE, top_n=3)
    assert set(out) >= {"BNY", "WDAY", "AAPL", "MSFT"}


def test_top_n_by_consensus():
    scores = [
        {"symbol": "PCG", "consensus_score": 0.9},
        {"symbol": "HOOD", "consensus_score": 0.8},
        {"symbol": "XOM", "consensus_score": 0.7},
        {"symbol": "KO", "consensus_score": 0.6},
    ]
    out = select_specialist_coverage([], scores, BASE, top_n=3)
    assert {"PCG", "HOOD", "XOM"} <= set(out)
    assert "KO" not in out


def test_dedup_and_bounded():
    scores = [
        {"symbol": "AAPL", "consensus_score": 0.9},
        {"symbol": "PCG", "consensus_score": 0.8},
    ]
    out = select_specialist_coverage(["AAPL"], scores, BASE, top_n=2)
    assert out.count("AAPL") == 1
    assert len(out) <= len(BASE) + 1 + 2


def test_cover_positions_off():
    out = select_specialist_coverage(["BNY"], [], BASE, top_n=1, cover_positions=False)
    assert "BNY" not in out


def test_malformed_entries_skipped():
    scores = [
        {"symbol": "PCG"},
        {"consensus_score": 0.9},
        {"symbol": "HOOD", "consensus_score": 0.8},
    ]
    out = select_specialist_coverage([], scores, BASE, top_n=5)
    assert "HOOD" in out  # only the well-formed entry ranks
