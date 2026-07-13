# R6-4 (#1678): the orchestrator maps a PublicSnapshot dict to the renderer's data shape.
# Also asserts the compiler + orchestrator import cleanly (explicit imports, moviepy lazy).

import json

import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("PIL")

from scripts.generate_daily_shorts import load_snapshot_data


def test_load_snapshot_data_maps_public_snapshot(tmp_path):
    snap = {
        "generated_at": "2026-07-01T12:00:00Z",
        "equity": 105000.0,
        "cash": 5000.0,
        "day_pl_abs": 800.0,
        "day_pl_pct": 0.76,
        "ytd_pct": 5.0,
        "market_regime": "BULLISH",
        "vix": 14.2,
        "equity_curve": [{"date": "2026-06-30", "equity": 104200.0}],
        "decisions": [{"symbol": "AAPL", "action": "buy", "summary": "momentum"}],
    }
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap), encoding="utf-8")

    data = load_snapshot_data(str(p))

    assert data["date"] == "2026-07-01"
    assert data["total_equity"] == 105000.0
    assert data["market_regime"] == "BULLISH"
    assert data["chart_points"] == [("2026-06-30", 104200.0)]
    assert data["trades"][0]["symbol"] == "AAPL"
    assert data["trades"][0]["side"] == "BUY"


def test_compiler_imports_without_moviepy():
    # moviepy is imported lazily inside compile_final_video, so the module must import
    # without moviepy installed (import-smoke gate stays green).
    import scripts.shorts_generator.video_compiler  # noqa: F401
