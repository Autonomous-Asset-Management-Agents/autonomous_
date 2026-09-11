# tests/unit/test_report_quality.py
# RPAR-1 (#1262) Abschluss / #1490 — the deterministic, bundle-free report-quality score.
from core.specialist.report import SpecialistReport
from core.specialist.report_quality import compute_report_quality, quality_label

_RICH = dict(
    company_summary="x" * 120,
    news_summary="y" * 100,
    investment_thesis="z" * 140,
    bull_case="b" * 80,
    bear_case="c" * 80,
)


def _report(**fields) -> SpecialistReport:
    return SpecialistReport(symbol="AAPL", **fields)


def test_label_thresholds_strong_fair_thin():
    assert quality_label(75) == "Strong"
    assert quality_label(74) == "Fair"
    assert quality_label(50) == "Fair"
    assert quality_label(49) == "Thin"


def test_thin_report_is_thin():
    score, label = compute_report_quality(_report())  # empty prose -> low grade
    assert label == "Thin"
    assert score < 50


def test_richer_report_scores_higher_and_is_at_least_fair():
    rich_score, rich_label = compute_report_quality(_report(**_RICH))
    thin_score, _ = compute_report_quality(_report())
    assert rich_score > thin_score
    assert rich_label in ("Strong", "Fair")


def test_degraded_path_docks_the_score():
    healthy, _ = compute_report_quality(_report(**_RICH))
    degraded, _ = compute_report_quality(_report(degraded=True, **_RICH))
    assert degraded < healthy


def test_score_is_bounded_0_100():
    for rpt in (_report(), _report(**_RICH), _report(degraded=True, **_RICH)):
        score, _ = compute_report_quality(rpt)
        assert 0 <= score <= 100
