# UXC-1 S2 (#3155, Epic #3151) — Registry-/Clamp-Modul core/trading_settings.py.
# Muster core/portfolio_shape.py: reine Funktionen, geklemmt statt abgelehnt,
# str-only Richtung Kette. Plan Rev. 2 (approved).

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import trading_settings as ts


def _cfg(monkeypatch, **overrides):
    import config

    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(**overrides))


# ---------------------------------------------------------------------------
# 1a/1e — Registry deckt die Ebenen 1+2 ab; Defaults == heutiges Engine-Verhalten
# ---------------------------------------------------------------------------

EXPECTED_DEFAULTS = {
    # Ebene 1 — Gewichte (Nähte agents.py/_consensus_weight)
    "MOMENTUM_AGENT_WEIGHT": "0.45",
    "LSTM_SIGNAL_WEIGHT": "0.40",
    "VIX_RISK_WEIGHT": "0.45",
    "SPECIALIST_ALPHA_WEIGHT": "0.40",
    "NEWS_SENTIMENT_WEIGHT": "0.35",
    "UPSIDE_SKEW_WEIGHT": "0.30",
    "RL_CONFIDENCE_WEIGHT": "0.00",
    "FUNDAMENTALS_AGENT_WEIGHT": "0.35",
    "VALUATION_AGENT_WEIGHT": "0.35",
    # Ebene 1 — Enable-Flags (S1 #3163 + Rescue #3168; UpsideSkew dark)
    "MOMENTUM_AGENT_ENABLED": "true",
    "LSTM_SIGNAL_AGENT_ENABLED": "true",
    "SPECIALIST_ALPHA_AGENT_ENABLED": "true",
    "NEWS_SENTIMENT_AGENT_ENABLED": "true",
    "VIX_RISK_AGENT_ENABLED": "true",
    "UPSIDE_SKEW_AGENT_ENABLED": "false",
    "DRAWDOWN_GUARD_AGENT_ENABLED": "true",
    "REGIME_DETECTION_AGENT_ENABLED": "true",
    "FUNDAMENTALS_AGENT_ENABLED": "true",
    "VALUATION_AGENT_ENABLED": "true",
    "RL_CONFIDENCE_AGENT_ENABLED": "true",
    # Ebene 2 — heute env-fähig (Anker im Plan verifiziert)
    "SIGNAL_BUY_THRESHOLD": "0.65",
    "SIGNAL_SELL_THRESHOLD": "0.35",
    "ROTATION_EXIT_ENABLED": "true",
    "ROTATION_MAX_EXITS_PER_CYCLE": "1",
    "ROTATION_MAX_EXITS_PER_SESSION": "2",
    "ROTATION_PANEL_MAX_AGE_DAYS": "3",
    "EXIT_POLICY_TRAILING_ENABLED": "true",
    "TRAILING_FROM_PEAK_PCT": "10.0",
    "EXIT_TRAIL_PROFILE": "midterm",
    "GLOBAL_BUY_COOLDOWN_MINUTES": "30",
    "MAX_BUYS_PER_HOUR": "2",
    "NO_BUY_OPENING_MINUTES": "30",
    "FULL_UNIVERSE_TRADING_ENABLED": "true",
    "ROUND_TABLE_TOP_K_EVAL": "30",
    # Ebene 2 — Nähte aus S1
    "STOP_LOSS_PCT": "7.0",
    "TAKE_PROFIT_PCT": "25.0",
    "MIN_POSITION_PERCENT": "0.05",
    "MAX_POSITION_PERCENT": "0.25",
    "MAX_POSITION_PERCENT_SIZING": "0.30",
    "MAX_TOTAL_EXPOSURE_PCT": "0.95",
    # Ebene 2 — #3199 Skew-Sizing-Tilt (dark: OFF / cap 0.0 => tilt 1.0, byte-identisch)
    "SKEW_SIZE_TILT_ENABLED": "false",
    "SKEW_SIZE_TILT_CAP": "0.00",
}


def test_registry_keys_and_shipped_defaults():
    assert set(ts.REGISTRY.keys()) == set(EXPECTED_DEFAULTS.keys())
    for key, want in EXPECTED_DEFAULTS.items():
        assert ts.canonical_text(key, ts.REGISTRY[key].default) == want, key


def test_current_settings_unset_config_equals_defaults(monkeypatch):
    """Leere Config ⇒ alle Defaults == heutiges Engine-Verhalten (byte-identisch)."""
    _cfg(monkeypatch)  # SimpleNamespace ohne Attribute → getattr-Defaults
    cur = ts.current_settings()
    assert cur == {k: ts.canonical_text(k, ts.REGISTRY[k].default) for k in ts.REGISTRY}


# ---------------------------------------------------------------------------
# 1f — Leitplanken sind NICHT in der Registry, POST-Versuch wird abgelehnt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "guard",
    [
        "HARD_STOP_LOSS_PCT",
        "HITL_ENABLED",
        "RISK_FORBID_LEVERAGE",
        "COMPLIANCE_MAX_DAILY_TRADES",
        "TRAILING_STOP_PCT",
        "MAX_ORDER_VALUE",
    ],
)
def test_guardrail_keys_not_in_registry(guard):
    assert guard not in ts.REGISTRY


def test_unknown_key_raises():
    with pytest.raises(ts.UnknownSettingError) as exc:
        ts.apply_updates({"HARD_STOP_LOSS_PCT": -3})
    assert "HARD_STOP_LOSS_PCT" in str(exc.value)


# ---------------------------------------------------------------------------
# 1a/1c — jede Bound klemmt; unbrauchbare Eingaben → Default
# ---------------------------------------------------------------------------


def test_bounds_clamp_under_over_within(monkeypatch):
    _cfg(monkeypatch)
    new = ts.apply_updates(
        {
            "MOMENTUM_AGENT_WEIGHT": 9.9,  # über max 1.5
            "LSTM_SIGNAL_WEIGHT": 0.01,  # unter min_weight 0.15 (base_agent-Klemme)
            "SIGNAL_BUY_THRESHOLD": 0.70,  # innerhalb
            "MAX_BUYS_PER_HOUR": 99,
        }
    )
    assert new["MOMENTUM_AGENT_WEIGHT"] == "1.50"
    assert new["LSTM_SIGNAL_WEIGHT"] == "0.15"
    assert new["SIGNAL_BUY_THRESHOLD"] == "0.70"
    assert int(new["MAX_BUYS_PER_HOUR"]) <= 12


def test_unusable_inputs_fall_back_to_default(monkeypatch):
    _cfg(monkeypatch)
    new = ts.apply_updates(
        {
            "TRAILING_FROM_PEAK_PCT": "quatsch",
            "ROTATION_MAX_EXITS_PER_CYCLE": None,
            "SIGNAL_SELL_THRESHOLD": float("nan"),
            "ROTATION_EXIT_ENABLED": "true",
            "EXIT_TRAIL_PROFILE": "unbekanntes-profil",
        }
    )
    assert new["TRAILING_FROM_PEAK_PCT"] == "10.0"
    assert new["ROTATION_MAX_EXITS_PER_CYCLE"] == "1"
    assert new["SIGNAL_SELL_THRESHOLD"] == "0.35"
    assert new["ROTATION_EXIT_ENABLED"] == "true"
    assert new["EXIT_TRAIL_PROFILE"] == "midterm", "Enum außerhalb choices → Default"


def test_bool_accepts_canonical_texts(monkeypatch):
    _cfg(monkeypatch)
    new = ts.apply_updates({"DRAWDOWN_GUARD_AGENT_ENABLED": "false"})
    assert new["DRAWDOWN_GUARD_AGENT_ENABLED"] == "false"
    new = ts.apply_updates({"DRAWDOWN_GUARD_AGENT_ENABLED": True})
    assert new["DRAWDOWN_GUARD_AGENT_ENABLED"] == "true"


# ---------------------------------------------------------------------------
# 1b — die 5 Cross-Field-Clamps (deterministisch aufgelöst)
# ---------------------------------------------------------------------------


def test_cross_min_position_vs_book_slot(monkeypatch):
    """(a) 1/FULL_UNIVERSE_MAX_POSITIONS >= MIN_POSITION_PERCENT — Ist-Positionszahl
    wird zur Aufrufzeit gelesen (hier 20 → Slot 5% → MIN klemmt auf 0.05)."""
    _cfg(monkeypatch, FULL_UNIVERSE_MAX_POSITIONS=20)
    new = ts.apply_updates({"MIN_POSITION_PERCENT": 0.09})
    assert float(new["MIN_POSITION_PERCENT"]) <= 0.05 + 1e-9


def test_cross_position_chain(monkeypatch):
    """(b) MIN < MAX <= MAX_SIZING."""
    _cfg(monkeypatch)
    new = ts.apply_updates(
        {"MAX_POSITION_PERCENT": 0.40, "MAX_POSITION_PERCENT_SIZING": 0.20}
    )
    assert float(new["MAX_POSITION_PERCENT"]) <= float(
        new["MAX_POSITION_PERCENT_SIZING"]
    )
    assert float(new["MIN_POSITION_PERCENT"]) < float(new["MAX_POSITION_PERCENT"])


def test_cross_thresholds_ordered(monkeypatch):
    """(c) 0 < SELL < BUY < 1 — die Einzel-Bounds (0.25–0.45 / 0.55–0.75) erzwingen
    das bereits; ein Konflikt-Request wird deterministisch aufgelöst."""
    _cfg(monkeypatch)
    new = ts.apply_updates(
        {"SIGNAL_BUY_THRESHOLD": 0.30, "SIGNAL_SELL_THRESHOLD": 0.80}
    )
    assert (
        0.0
        < float(new["SIGNAL_SELL_THRESHOLD"])
        < float(new["SIGNAL_BUY_THRESHOLD"])
        < 1.0
    )


def test_cross_stop_inside_hard_stop(monkeypatch):
    """(d) STOP_LOSS_PCT strikt innerhalb |HARD_STOP_LOSS_PCT| = 8.0."""
    _cfg(monkeypatch)
    new = ts.apply_updates({"STOP_LOSS_PCT": 15.0})
    assert float(new["STOP_LOSS_PCT"]) < 8.0


def test_cross_top_k_headroom(monkeypatch):
    """(e) ROUND_TABLE_TOP_K_EVAL > FULL_UNIVERSE_MAX_POSITIONS (Displacement-Headroom)."""
    _cfg(monkeypatch, FULL_UNIVERSE_MAX_POSITIONS=20)
    new = ts.apply_updates({"ROUND_TABLE_TOP_K_EVAL": 12})
    assert int(new["ROUND_TABLE_TOP_K_EVAL"]) > 20


# ---------------------------------------------------------------------------
# 1d — describe_changes: nur echte Änderungen, alle Felder str, byte-stabil
# ---------------------------------------------------------------------------


def test_describe_changes_only_real_diffs(monkeypatch):
    _cfg(monkeypatch)
    current = ts.current_settings()
    new = dict(current)
    new["STOP_LOSS_PCT"] = "5.0"
    changes = ts.describe_changes(current, new)
    assert changes == [{"key": "STOP_LOSS_PCT", "from": "7.0", "to": "5.0"}]
    for c in changes:
        assert all(isinstance(v, str) for v in c.values())
    assert ts.describe_changes(current, dict(current)) == []


def test_canonical_text_is_byte_stable():
    assert ts.canonical_text("STOP_LOSS_PCT", 5) == "5.0"
    assert ts.canonical_text("STOP_LOSS_PCT", 5.00001) == "5.0"
    assert ts.canonical_text("MIN_POSITION_PERCENT", 0.05) == "0.05"
    assert ts.canonical_text("MAX_BUYS_PER_HOUR", 2.0) == "2"
    assert ts.canonical_text("ROTATION_EXIT_ENABLED", True) == "true"


# ---------------------------------------------------------------------------
# Agents-View für die S4-Karte (Vertrag aus dem Epic-/S2-Kommentar 02.09.)
# ---------------------------------------------------------------------------


def test_agents_view_full_roster_and_roles(monkeypatch):
    _cfg(monkeypatch)
    view = ts.agents_view()
    agents = {a["name"]: a for a in view["agents"]}
    assert len(agents) == 11
    assert view["implied_vol_forecast_enabled"] is True
    assert agents["DrawdownGuardAgent"]["role"] == "veto_guard"
    assert agents["DrawdownGuardAgent"]["role_title"] == "Risk Manager"
    assert agents["DrawdownGuardAgent"]["weight_key"] == ""
    assert agents["RegimeDetectionAgent"]["role"] == "conditioner"
    assert agents["VIXAwareRiskAgent"]["role"] == "size_input", "IV-Flag default true"
    assert agents["MomentumAgent"]["role"] == "mean_voter"
    assert agents["MomentumAgent"]["role_title"] == "Momentum Strategist"
    assert agents["MomentumAgent"]["min_weight"] == 0.0
    assert agents["LSTMSignalAgent"]["min_weight"] == 0.15
    assert agents["UpsideSkewAgent"]["enabled"] is False
    assert agents["UpsideSkewAgent"]["default_enabled"] is False
    assert agents["FundamentalsAgent"]["weight"] == 0.0


def test_agents_view_vix_mean_voter_when_iv_off(monkeypatch):
    _cfg(monkeypatch, IMPLIED_VOL_FORECAST_ENABLED=False)
    view = ts.agents_view()
    vix = next(a for a in view["agents"] if a["name"] == "VIXAwareRiskAgent")
    assert view["implied_vol_forecast_enabled"] is False
    assert vix["role"] == "mean_voter"


def test_agents_view_reflects_effective_env(monkeypatch):
    _cfg(monkeypatch, MOMENTUM_AGENT_WEIGHT=0.6, MOMENTUM_AGENT_ENABLED=False)
    agents = {a["name"]: a for a in ts.agents_view()["agents"]}
    assert agents["MomentumAgent"]["weight"] == pytest.approx(0.6)
    assert agents["MomentumAgent"]["enabled"] is False
    assert agents["MomentumAgent"]["default_weight"] == pytest.approx(0.45)
