"""#3155 (UXC-1 S2) — generische Settings-Registry: Grenzen klemmen, Änderungen beschreiben.

Reine Funktionen ohne Seiteneffekte, Vorlage ``core/portfolio_shape.py`` (#3132) — und
aus demselben Grund SERVERSEITIG: die Regler in der Console sind Bequemlichkeit, nicht
die Absicherung; ein direkter API-Aufruf muss dieselben Grenzen sehen.

**Was hier drinsteht — und was nie.** Die Registry deckt exakt die Epic-Ebenen 1+2 ab
(#3151): Agenten-Gewichte und -Enable-Flags (Nähte aus S1 #3163/#3168) plus die heute
env-fähigen Handelsparameter. Compliance-Leitplanken (ESMA-Order-Cap, PDT/FINRA,
``RISK_FORBID_LEVERAGE``, ``HARD_STOP_LOSS_PCT``, ``HITL_ENABLED``,
``COMPLIANCE_MAX_DAILY_TRADES``, ``TRAILING_STOP_PCT``) sind bewusst NICHT enthalten —
ein Request mit so einem Key wird abgelehnt (422 im Endpoint), nie still ignoriert.
Die drei #3132-Parameter (Positions/Haltefrist/Vol-Target) bleiben beim bestehenden
``/api/portfolio-shape``-Endpoint (Abgrenzung B8, kein Doppeln).

**Warum alles als Text herauskommt.** Der WORM-Eintrag geht auf dieselbe SHA-256-Kette
wie ``LiveEnableEvent``; die Vorlage muss byte-identisch sein, wenn der JS-Verifizierer
(``audit-chain.cjs``) sie nachrechnet. Ein Float kann zwischen ``json.dumps`` und
``JSON.stringify`` abweichen — deshalb erreicht kein Float die Kette
(``core/portfolio_shape.py:14-18``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "REGISTRY",
    "Setting",
    "UnknownSettingError",
    "agents_view",
    "apply_updates",
    "canonical_text",
    "current_settings",
    "describe_changes",
    "guardrails_view",
    "settings_meta",
]


class UnknownSettingError(ValueError):
    """Request enthält Keys außerhalb der Registry (auch Leitplanken-Keys)."""

    def __init__(self, keys: List[str]):
        self.keys = sorted(keys)
        super().__init__(
            "Nicht über diesen Endpoint einstellbar: " + ", ".join(self.keys)
        )


@dataclass(frozen=True)
class Setting:
    """Ein einstellbarer Engine-Key: Typ, Grenzen, Auslieferungs-Default, Textformat."""

    key: str
    kind: str  # "float" | "int" | "bool" | "enum"
    default: Any
    lo: float = 0.0
    hi: float = 0.0
    decimals: int = 2
    choices: Tuple[str, ...] = ()


def _f(key: str, default: float, lo: float, hi: float, decimals: int = 2) -> Setting:
    return Setting(
        key=key, kind="float", default=default, lo=lo, hi=hi, decimals=decimals
    )


def _i(key: str, default: int, lo: int, hi: int) -> Setting:
    return Setting(key=key, kind="int", default=default, lo=lo, hi=hi)


def _b(key: str, default: bool) -> Setting:
    return Setting(key=key, kind="bool", default=default)


def _e(key: str, default: str, choices: Tuple[str, ...]) -> Setting:
    return Setting(key=key, kind="enum", default=default, choices=choices)


# ---------------------------------------------------------------------------
# Registry — jede Grenze mit Quelle, keine erfunden.
# ---------------------------------------------------------------------------

_SETTINGS: Tuple[Setting, ...] = (
    # ── Ebene 1: Konsens-Gewichte (Nähte agents.py `_consensus_weight`, S1/#3164).
    #    Bounds = per-Agent min_weight/max_weight (base_agent-Klemme, Epic B1/B2) —
    #    die Untergrenze liegt NIE unterhalb min_weight, sonst klemmt der Agent selbst.
    _f("MOMENTUM_AGENT_WEIGHT", 0.45, 0.0, 1.5),  # agents.py MomentumAgent 0.00/1.50
    _f("LSTM_SIGNAL_WEIGHT", 0.40, 0.15, 1.5),  # LSTMSignalAgent 0.15/1.50
    _f("VIX_RISK_WEIGHT", 0.45, 0.10, 1.5),  # VIXAwareRiskAgent 0.10/1.50
    _f("SPECIALIST_ALPHA_WEIGHT", 0.40, 0.0, 2.0),  # SpecialistAlphaAgent 0.0/2.0
    _f("NEWS_SENTIMENT_WEIGHT", 0.35, 0.10, 1.5),  # NewsSentimentAgent 0.10/1.50
    _f("UPSIDE_SKEW_WEIGHT", 0.30, 0.10, 1.5),  # UpsideSkewAgent 0.10/1.50
    _f("RL_CONFIDENCE_WEIGHT", 0.0, 0.0, 1.5),  # gemutet (Default 0.0, config.py)
    _f("FUNDAMENTALS_AGENT_WEIGHT", 0.35, 0.0, 1.5),  # active 0.35 (owner 2026-09-03)
    _f("VALUATION_AGENT_WEIGHT", 0.35, 0.0, 1.5),  # active 0.35 (owner 2026-09-03)
    # ── Ebene 1: per-Agent-Enable-Flags (Abstain-Semantik; S1 #3163 + #3168).
    _b("MOMENTUM_AGENT_ENABLED", True),
    _b("LSTM_SIGNAL_AGENT_ENABLED", True),
    _b("SPECIALIST_ALPHA_AGENT_ENABLED", True),
    _b("NEWS_SENTIMENT_AGENT_ENABLED", True),
    _b("VIX_RISK_AGENT_ENABLED", True),
    _b("UPSIDE_SKEW_AGENT_ENABLED", False),  # dark (#3095)
    _b("DRAWDOWN_GUARD_AGENT_ENABLED", True),  # Owner 02.09.: Veto schaltbar (#3168)
    _b("REGIME_DETECTION_AGENT_ENABLED", True),
    _b("FUNDAMENTALS_AGENT_ENABLED", True),
    _b("VALUATION_AGENT_ENABLED", True),
    _b("RL_CONFIDENCE_AGENT_ENABLED", True),
    # ── Ebene 2: Konsens-Schwellen (config.py:1544-1548; Bounds Epic B11 —
    #    getrennte Bänder erzwingen SELL < BUY strukturell, vgl. consensus.py:36-56).
    _f("SIGNAL_BUY_THRESHOLD", 0.65, 0.55, 0.75),
    _f("SIGNAL_SELL_THRESHOLD", 0.35, 0.25, 0.45),
    # ── Ebene 2: Rotation (config.py:531-582; Obergrenzen = kleine ganzzahlige
    #    Vielfache der Defaults — Rotation ist Churn-Hebel, #3006/#2698).
    _b("ROTATION_EXIT_ENABLED", True),
    _i("ROTATION_MAX_EXITS_PER_CYCLE", 1, 0, 5),
    _i("ROTATION_MAX_EXITS_PER_SESSION", 2, 0, 10),
    _i("ROTATION_PANEL_MAX_AGE_DAYS", 3, 1, 10),
    # ── Ebene 2: Exit-Politik (config.py:561, 2012-2028; Trailing-Band um den
    #    kalibrierten 10%-Default, #3006-Kontext).
    _b("EXIT_POLICY_TRAILING_ENABLED", True),
    _f("TRAILING_FROM_PEAK_PCT", 10.0, 3.0, 25.0, decimals=1),
    _e("EXIT_TRAIL_PROFILE", "midterm", ("midterm", "legacy")),
    # ── Ebene 2: Frequenz-Guards (config.py:711-717; Churn via Frequenz, nicht
    #    Ordergröße — reference_order_floor_protects_small_accounts).
    _i("GLOBAL_BUY_COOLDOWN_MINUTES", 30, 0, 240),
    _i("MAX_BUYS_PER_HOUR", 2, 1, 12),
    _i("NO_BUY_OPENING_MINUTES", 30, 0, 120),
    # ── Ebene 2: Universum/Round-Table (config.py:1699, 1725; TOP_K-Band #2781).
    _b("FULL_UNIVERSE_TRADING_ENABLED", True),
    _i("ROUND_TABLE_TOP_K_EVAL", 30, 10, 60),
    # ── Ebene 2: Stops/Sizing (Nähte aus S1 #3163; config.py:181-200, 474-477, 681).
    #    STOP_LOSS-Obergrenze strikt innerhalb |HARD_STOP_LOSS_PCT|=8.0 (config.py:588,
    #    Leitplanke A6) — zusätzlich zur Laufzeit gegen den Ist-Wert geklemmt.
    _f("STOP_LOSS_PCT", 7.0, 1.0, 7.9, decimals=1),
    _f("TAKE_PROFIT_PCT", 25.0, 5.0, 100.0, decimals=1),
    _f("MIN_POSITION_PERCENT", 0.05, 0.01, 0.10),
    _f("MAX_POSITION_PERCENT", 0.25, 0.05, 0.40),
    _f("MAX_POSITION_PERCENT_SIZING", 0.30, 0.05, 0.50),
    _f("MAX_TOTAL_EXPOSURE_PCT", 0.95, 0.50, 1.00),
)

REGISTRY: Dict[str, Setting] = {s.key: s for s in _SETTINGS}

# Leitplanken, die per GET sichtbar sind, aber NIE über diesen Endpoint erreichbar
# (Epic-Leitplanke 3 / A3 / B9 / A6). Nur Anzeige — die Registry kennt sie nicht.
_GUARDRAIL_VIEW_KEYS: Tuple[Tuple[str, float, int], ...] = (
    ("HARD_STOP_LOSS_PCT", -8.0, 1),  # config.py:588 (ADR — env-only Backstop)
)


def _cfg(cfg: Optional[Any]) -> Optional[Any]:
    if cfg is not None:
        return cfg
    try:
        from config import get_config

        return get_config()
    except Exception:  # noqa: BLE001 — unlesbare Config ⇒ Auslieferungsstand
        return None


def _getattr(cfg: Optional[Any], name: str, default: Any) -> Any:
    return getattr(cfg, name, default) if cfg is not None else default


def _parse(setting: Setting, value: Any) -> Any:
    """Eine brauchbare Eingabe oder der Default — wirft nie, rät nie (portfolio_shape._num)."""
    if setting.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        return bool(setting.default)
    if setting.kind == "enum":
        v = str(value).strip().lower() if isinstance(value, str) else ""
        return v if v in setting.choices else setting.default
    # int/float — bool ist in Python eine 1 und wäre eine stumme Fehlinterpretation.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(str(value))
        except (TypeError, ValueError):
            return setting.default
    v = float(value)
    if not math.isfinite(v):
        return setting.default
    v = max(setting.lo, min(setting.hi, v))
    return int(round(v)) if setting.kind == "int" else v


def canonical_text(key: str, value: Any) -> str:
    """Einheitliche Textform je Feld — die Kette darf keine Float-Repräsentation sehen."""
    s = REGISTRY[key]
    v = _parse(s, value)
    if s.kind == "bool":
        return "true" if v else "false"
    if s.kind == "enum":
        return str(v)
    if s.kind == "int":
        return str(int(v))
    return f"{float(v):.{s.decimals}f}"


def current_settings(cfg: Optional[Any] = None) -> Dict[str, str]:
    """Der wirksame Engine-Ist-Stand aller Registry-Keys, zur Aufrufzeit gelesen
    (nie eingefroren, nie UI-State) — BORA-neutral via ``getattr`` wie
    ``trading_control_seal._effective_controls``."""
    c = _cfg(cfg)
    return {
        s.key: canonical_text(s.key, _getattr(c, s.key, s.default)) for s in _SETTINGS
    }


def _cross_clamp(vals: Dict[str, Any], cfg: Optional[Any]) -> Dict[str, Any]:
    """Die 5 Cross-Field-Regeln, deterministisch aufgelöst (geklemmt, nicht abgelehnt).

    Reihenfolge ist Teil des Vertrags: erst werden die Ober-Kappen gesetzt, dann die
    davon abhängigen Werte darunter gezogen — so ist das Ergebnis für jeden Request
    eindeutig und im Response sichtbar.
    """
    c = _cfg(cfg)
    # (a) 1/Positionszahl >= MIN_POSITION_PERCENT (POSITION_BOOK_CAP_TUNING.md;
    #     Ist-Positionszahl zur Aufrufzeit — der Key selbst liegt bei #3132).
    positions = int(_getattr(c, "FULL_UNIVERSE_MAX_POSITIONS", 10) or 10)
    slot = 1.0 / max(1, positions)
    vals["MIN_POSITION_PERCENT"] = min(float(vals["MIN_POSITION_PERCENT"]), slot)
    # (b) MIN < MAX <= MAX_SIZING — Kappe zuerst, dann darunter ziehen.
    vals["MAX_POSITION_PERCENT"] = min(
        float(vals["MAX_POSITION_PERCENT"]), float(vals["MAX_POSITION_PERCENT_SIZING"])
    )
    if float(vals["MIN_POSITION_PERCENT"]) >= float(vals["MAX_POSITION_PERCENT"]):
        vals["MIN_POSITION_PERCENT"] = max(
            REGISTRY["MIN_POSITION_PERCENT"].lo,
            float(vals["MAX_POSITION_PERCENT"]) - 0.01,
        )
    # (c) 0 < SELL < BUY < 1 (consensus.py:36-56) — die getrennten Bänder erzwingen
    #     das bereits; der Guard hält die Invariante auch bei künftigen Band-Änderungen.
    if float(vals["SIGNAL_SELL_THRESHOLD"]) >= float(vals["SIGNAL_BUY_THRESHOLD"]):
        vals["SIGNAL_SELL_THRESHOLD"] = float(vals["SIGNAL_BUY_THRESHOLD"]) - 0.10
    # (d) STOP_LOSS strikt innerhalb |HARD_STOP_LOSS_PCT| (A6; Registry-Bound 7.9 +
    #     Laufzeit-Guard, falls der env-only Backstop enger konfiguriert ist).
    hard = abs(float(_getattr(c, "HARD_STOP_LOSS_PCT", -8.0) or -8.0))
    vals["STOP_LOSS_PCT"] = min(float(vals["STOP_LOSS_PCT"]), hard - 0.1)
    # (e) TOP_K > Positionszahl (Displacement-Headroom, #2698/#2781).
    if int(vals["ROUND_TABLE_TOP_K_EVAL"]) <= positions:
        vals["ROUND_TABLE_TOP_K_EVAL"] = positions + 1
    return vals


def apply_updates(updates: Dict[str, Any], cfg: Optional[Any] = None) -> Dict[str, str]:
    """Partielle Updates auf den Ist-Stand anwenden: parsen, klemmen, Cross-Regeln.

    Unbekannte Keys (auch Leitplanken) ⇒ :class:`UnknownSettingError` — kein stilles
    Ignorieren. Ergebnis ist der VOLLE neue Stand in kanonischer Textform.
    """
    unknown = [k for k in updates if k not in REGISTRY]
    if unknown:
        raise UnknownSettingError(unknown)
    c = _cfg(cfg)
    vals: Dict[str, Any] = {
        s.key: _parse(s, _getattr(c, s.key, s.default)) for s in _SETTINGS
    }
    for key, raw in updates.items():
        vals[key] = _parse(REGISTRY[key], raw)
    vals = _cross_clamp(vals, cfg)
    return {k: canonical_text(k, v) for k, v in vals.items()}


def describe_changes(
    current: Dict[str, str], new: Dict[str, str]
) -> List[Dict[str, str]]:
    """Nur die tatsächlich veränderten Werte, als reine Textfelder (#3132-Muster).

    Leere Liste heißt: nichts zu tun — der Aufrufer schreibt dann KEINEN WORM-Eintrag
    (Kettenrauschen-Regel, ``hitl_gate``)."""
    out: List[Dict[str, str]] = []
    for s in _SETTINGS:
        before, after = current.get(s.key), new.get(s.key)
        if before is None or after is None or before == after:
            continue
        out.append({"key": s.key, "from": before, "to": after})
    return out


def settings_meta() -> List[Dict[str, Any]]:
    """Bounds + Auslieferungs-Defaults je Key — für die Karten (S4/S5) und den
    Defaults-Abgleich im Live-Consent (S3, Epic-Leitplanke 6)."""
    out: List[Dict[str, Any]] = []
    for s in _SETTINGS:
        entry: Dict[str, Any] = {
            "key": s.key,
            "kind": s.kind,
            "default": canonical_text(s.key, s.default),
        }
        if s.kind in ("float", "int"):
            entry["min"] = s.lo
            entry["max"] = s.hi
        if s.kind == "enum":
            entry["choices"] = list(s.choices)
        out.append(entry)
    return out


def guardrails_view(cfg: Optional[Any] = None) -> Dict[str, str]:
    """Read-only-Leitplanken für die Anzeige — sichtbar, nie editierbar (A6)."""
    c = _cfg(cfg)
    return {
        key: f"{float(_getattr(c, key, default)):.{dec}f}"
        for key, default, dec in _GUARDRAIL_VIEW_KEYS
    }


# ---------------------------------------------------------------------------
# Agents-View — Vertrag der S4-Karte (Epic-/S2-Kommentar 02.09.2026):
# Roster, Rollen, role_title, Bounds und Effektiv-Werte kommen vom SERVER.
# ---------------------------------------------------------------------------

_AGENTS: Tuple[Dict[str, Any], ...] = (
    # name, role_title (Owner 02.09., Epic #3151), enable_flag, weight_key,
    # default_enabled, default_weight, min/max (base_agent-Klemme), fixed_role?
    {
        "name": "MomentumAgent",
        "role_title": "Momentum Strategist",
        "enable_flag": "MOMENTUM_AGENT_ENABLED",
        "weight_key": "MOMENTUM_AGENT_WEIGHT",
        "default_enabled": True,
        "default_weight": 0.45,
        "min_weight": 0.0,
        "max_weight": 1.5,
    },
    {
        "name": "LSTMSignalAgent",
        "role_title": "Quantitative Analyst",
        "enable_flag": "LSTM_SIGNAL_AGENT_ENABLED",
        "weight_key": "LSTM_SIGNAL_WEIGHT",
        "default_enabled": True,
        "default_weight": 0.40,
        "min_weight": 0.15,
        "max_weight": 1.5,
    },
    {
        "name": "SpecialistAlphaAgent",
        "role_title": "Equity Research Analyst",
        "enable_flag": "SPECIALIST_ALPHA_AGENT_ENABLED",
        "weight_key": "SPECIALIST_ALPHA_WEIGHT",
        "default_enabled": True,
        "default_weight": 0.40,
        "min_weight": 0.0,
        "max_weight": 2.0,
    },
    {
        "name": "NewsSentimentAgent",
        "role_title": "Sentiment Analyst",
        "enable_flag": "NEWS_SENTIMENT_AGENT_ENABLED",
        "weight_key": "NEWS_SENTIMENT_WEIGHT",
        "default_enabled": True,
        "default_weight": 0.35,
        "min_weight": 0.10,
        "max_weight": 1.5,
    },
    {
        "name": "UpsideSkewAgent",
        "role_title": "Risk-Reward Analyst",
        "enable_flag": "UPSIDE_SKEW_AGENT_ENABLED",
        "weight_key": "UPSIDE_SKEW_WEIGHT",
        "default_enabled": False,
        "default_weight": 0.30,
        "min_weight": 0.10,
        "max_weight": 1.5,
    },
    {
        "name": "FundamentalsAgent",
        "role_title": "Fundamental Analyst",
        "enable_flag": "FUNDAMENTALS_AGENT_ENABLED",
        "weight_key": "FUNDAMENTALS_AGENT_WEIGHT",
        "default_enabled": True,
        "default_weight": 0.0,
        "min_weight": 0.0,
        "max_weight": 1.5,
    },
    {
        "name": "ValuationAgent",
        "role_title": "Valuation Analyst",
        "enable_flag": "VALUATION_AGENT_ENABLED",
        "weight_key": "VALUATION_AGENT_WEIGHT",
        "default_enabled": True,
        "default_weight": 0.0,
        "min_weight": 0.0,
        "max_weight": 1.5,
    },
    {
        "name": "RLConfidenceAgent",
        "role_title": "Systematic Strategist",
        "enable_flag": "RL_CONFIDENCE_AGENT_ENABLED",
        "weight_key": "RL_CONFIDENCE_WEIGHT",
        "default_enabled": True,
        "default_weight": 0.0,
        "min_weight": 0.0,
        "max_weight": 1.5,
    },
    # Overlay: Gewichte inert (#3084) ⇒ weight_key "" — die Karte rendert keinen Slider.
    {
        "name": "DrawdownGuardAgent",
        "role_title": "Risk Manager",
        "enable_flag": "DRAWDOWN_GUARD_AGENT_ENABLED",
        "weight_key": "",
        "default_enabled": True,
        "default_weight": 0.0,
        "min_weight": 0.0,
        "max_weight": 0.0,
        "role": "veto_guard",
    },
    {
        "name": "RegimeDetectionAgent",
        "role_title": "Macro Strategist",
        "enable_flag": "REGIME_DETECTION_AGENT_ENABLED",
        "weight_key": "",
        "default_enabled": True,
        "default_weight": 0.0,
        "min_weight": 0.0,
        "max_weight": 0.0,
        "role": "conditioner",
    },
    {
        "name": "VIXAwareRiskAgent",
        "role_title": "Volatility Strategist",
        "enable_flag": "VIX_RISK_AGENT_ENABLED",
        "weight_key": "VIX_RISK_WEIGHT",
        "default_enabled": True,
        "default_weight": 0.45,
        "min_weight": 0.10,
        "max_weight": 1.5,
    },
)


def agents_view(cfg: Optional[Any] = None) -> Dict[str, Any]:
    """Der volle 11-Agenten-Roster mit Rollen und Effektiv-Werten (Engine-Ist).

    Rollen-Zuordnung ist SERVER-Aufgabe (nie UI): Overlay-Rollen sind fix; VIXAware ist
    ``size_input``, solange ``IMPLIED_VOL_FORECAST_ENABLED`` (Default true, #3094) — nur
    bei deaktivierter IV-Prognose stimmt er im Mittel (``consensus_exclusions()``).
    """
    c = _cfg(cfg)
    iv_on = bool(_getattr(c, "IMPLIED_VOL_FORECAST_ENABLED", True))
    agents: List[Dict[str, Any]] = []
    for spec in _AGENTS:
        role = spec.get("role")
        if role is None:
            if spec["name"] == "VIXAwareRiskAgent":
                role = "size_input" if iv_on else "mean_voter"
            else:
                role = "mean_voter"
        weight = (
            float(_getattr(c, spec["weight_key"], spec["default_weight"]))
            if spec["weight_key"]
            else 0.0
        )
        agents.append(
            {
                "name": spec["name"],
                "role": role,
                "role_title": spec["role_title"],
                "enabled": bool(
                    _getattr(c, spec["enable_flag"], spec["default_enabled"])
                ),
                "default_enabled": spec["default_enabled"],
                "weight": weight,
                "default_weight": spec["default_weight"],
                "min_weight": spec["min_weight"],
                "max_weight": spec["max_weight"],
                "enable_flag": spec["enable_flag"],
                "weight_key": spec["weight_key"],
            }
        )
    return {"agents": agents, "implied_vol_forecast_enabled": iv_on}
