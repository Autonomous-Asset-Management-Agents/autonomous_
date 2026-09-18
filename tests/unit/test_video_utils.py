# R6-3a (#1675): video_utils re-authored with explicit/lazy imports so it imports cleanly
# (PIL only) and the pure formatting helpers are unit-testable.

from scripts.shorts_generator.video_utils import (
    fmt_eur,
    fmt_pnl_eur,
    get_fallback_script,
)


def test_fmt_eur():
    assert fmt_eur(105000) == "€105,000.00"
    assert fmt_eur(0) == "€0.00"


def test_fmt_pnl_eur_positive():
    assert fmt_pnl_eur(800) == "+€800.00"


def test_fmt_pnl_eur_negative():
    out = fmt_pnl_eur(-800.5)
    assert "€800.50" in out
    assert out[0] in ("-", "−")  # ASCII or unicode minus


def test_get_fallback_script_has_all_scene_keys():
    data = {
        "date": "2026-07-01",
        "total_equity": 105000.0,
        "pnl_abs": 800.0,
        "pnl_pct": 0.76,
        "market_regime": "Bull",
        "vix": 14.2,
        "trades": [],
    }
    script = get_fallback_script(data)
    for k in (
        "scene2_voiceover",
        "scene2_caption",
        "scene3_voiceover",
        "scene3_caption",
        "scene4_voiceover",
        "scene4_caption",
        "scene5_voiceover",
        "scene5_caption",
    ):
        assert script.get(k)
