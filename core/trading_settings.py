"""#3155 (UXC-1 S2) — generische Settings-Registry: Grenzen klemmen, Änderungen beschreiben.

Reine Funktionen ohne Seiteneffekte, Vorlage ``core/portfolio_shape.py`` (#3132) — und
aus demselben Grund SERVERSEITIG: die Regler in der Console sind Bequemlichkeit, nicht
die Absicherung; ein direkter API-Aufruf muss dieselben Grenzen sehen.

**Was hier drinsteht — und was nie.** Die Registry deckt exakt die Epic-Ebenen 1+2 ab
(#3151): Agenten-Gewichte und -Enable-Flags (Nähte aus S1 #3163/#3168) plus die heute
env-fähigen Handelsparameter. Compliance-Leitplanken (PDT/FINRA,
``RISK_FORBID_LEVERAGE``, ``HARD_STOP_LOSS_PCT``, ``HITL_ENABLED``,
``TRAILING_STOP_PCT``) sind bewusst NICHT enthalten —
ein Request mit so einem Key wird abgelehnt (422 im Endpoint), nie still ignoriert.

**Ausnahme seit #3220/#3221 (Owner-Entscheid 05.09.2026):** ``COMPLIANCE_MAX_ORDER_VALUE``
und ``COMPLIANCE_MAX_DAILY_TRADES`` sind jetzt einstellbar — nach demselben Muster, das
bei HITL längst gilt: die LEITPLANKE (dass ein Deckel existiert und das in #1599
ratifizierte Ceiling hält) bleibt unantastbar, das BETRIEBSLIMIT darunter ist einstellbar
und WORM-auditiert. Bei HITL sind aus demselben Grund fünf der sechs Werte verstellbar,
während ``HITL_ENABLED`` selbst boot-erzwungen bleibt. Ein Wert über dem Ceiling wird
geklemmt, nicht abgelehnt — die Antwort trägt den tatsächlich angewandten Wert.
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

# #3220/#3221: die Registry-Obergrenzen für die beiden Compliance-Betriebslimits sind
# die in #1599 RATIFIZIERTEN Ceilings — nie eine hier erfundene Zahl. Import an dieser
# Stelle, damit eine Änderung am Ceiling automatisch die Registry-Grenze mitzieht.
from core.governance.iron_dome_policy import (
    MAX_DAILY_TRADES_CEILING as _MAX_DAILY_TRADES_CEILING,
)
from core.governance.iron_dome_policy import (
    MAX_ORDER_VALUE_CEILING as _MAX_ORDER_VALUE_CEILING,
)

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
    _f("TREND_AGENT_WEIGHT", 0.0, 0.0, 1.5),  # #3250 TrendAgent 0.0/1.50 (dark)
    _f("VOLUME_CONFIRM_AGENT_WEIGHT", 0.0, 0.0, 1.5),  # #3250 VolumeConfirm dark
    _f("RL_CONFIDENCE_WEIGHT", 0.0, 0.0, 1.5),  # gemutet (Default 0.0, config.py)
    _f("FUNDAMENTALS_AGENT_WEIGHT", 0.35, 0.0, 1.5),  # active 0.35 (owner 2026-09-03)
    _f("VALUATION_AGENT_WEIGHT", 0.35, 0.0, 1.5),  # active 0.35 (owner 2026-09-03)
    _f(
        "QUALITY_AGENT_WEIGHT", 0.30, 0.10, 1.5
    ),  # #3275 QualityAgent 0.10/1.50 (dark via flag)
    # ── Ebene 1: per-Agent-Enable-Flags (Abstain-Semantik; S1 #3163 + #3168).
    _b("MOMENTUM_AGENT_ENABLED", True),
    _b("LSTM_SIGNAL_AGENT_ENABLED", True),
    _b("SPECIALIST_ALPHA_AGENT_ENABLED", True),
    _b("NEWS_SENTIMENT_AGENT_ENABLED", True),
    _b("VIX_RISK_AGENT_ENABLED", True),
    _b("UPSIDE_SKEW_AGENT_ENABLED", False),  # dark (#3095)
    _b("TREND_AGENT_ENABLED", False),  # dark (#3250)
    _b("VOLUME_CONFIRM_AGENT_ENABLED", False),  # dark (#3250)
    _b("QUALITY_AGENT_ENABLED", False),  # dark (#3275)
    _b("DRAWDOWN_GUARD_AGENT_ENABLED", True),  # Owner 02.09.: Veto schaltbar (#3168)
    _b("REGIME_DETECTION_AGENT_ENABLED", True),
    _b("FUNDAMENTALS_AGENT_ENABLED", True),
    _b("VALUATION_AGENT_ENABLED", True),
    _b("RL_CONFIDENCE_AGENT_ENABLED", True),
    # ── Ebene 2: Konsens-Schwellen (settings.py::SIGNAL_BUY_THRESHOLD; Bounds Epic B11 —
    #    getrennte Bänder erzwingen SELL < BUY strukturell, vgl. consensus.py:36-56).
    _f("SIGNAL_BUY_THRESHOLD", 0.65, 0.55, 0.75),
    _f("SIGNAL_SELL_THRESHOLD", 0.35, 0.25, 0.45),
    # ── Ebene 2: Rotation (settings.py::ROTATION_EXIT_ENABLED; Obergrenzen = kleine ganzzahlige
    #    Vielfache der Defaults — Rotation ist Churn-Hebel, #3006/#2698).
    _b("ROTATION_EXIT_ENABLED", False),
    _i("ROTATION_MAX_EXITS_PER_CYCLE", 1, 0, 5),
    _i("ROTATION_MAX_EXITS_PER_SESSION", 2, 0, 10),
    _i("ROTATION_PANEL_MAX_AGE_DAYS", 3, 1, 10),
    # ── Ebene 2: Verdrängung (#3291) — Master-Schalter + geteilter Konsens-Retention-Gate.
    #    DISPLACEMENT_ENABLED default ON (byte-identisch); CONSENSUS_RETENTION_THRESHOLD 0.0 =
    #    off (schützt Namen mit Live-Konsens ≥ Schwelle vor Rotation/Verdrängung/Overflow).
    _b("DISPLACEMENT_ENABLED", True),
    #    #3418: Sitzungsdeckel, Geschwister von ROTATION_MAX_EXITS_PER_SESSION (2).
    #    Obergrenze 10 wie dort; 0 = unbegrenzt bleibt env-only (UI-Minimum 1).
    _i("DISPLACEMENT_MAX_PER_SESSION", 2, 0, 10),
    _f("CONSENSUS_RETENTION_THRESHOLD", 0.0, 0.0, 0.65),
    # ── Ebene 2: Exit-Politik (#3632, settings.py): eine Exit-Autoritaet, der
    #    Intelligent Exit. Das Profil waehlt dessen Gewinn-Stufen; die Verlust-Stufen
    #    stehen unter Stops. EXIT_POLICY_TRAILING_ENABLED / TRAILING_FROM_PEAK_PCT
    #    entfielen mit Smart Exit.
    _e("EXIT_TRAIL_PROFILE", "midterm", ("midterm", "legacy")),
    # ── Ebene 2: Frequenz-Guards (settings.py::GLOBAL_BUY_COOLDOWN_MINUTES; Churn via Frequenz, nicht
    #    Ordergröße — reference_order_floor_protects_small_accounts).
    _i("GLOBAL_BUY_COOLDOWN_MINUTES", 30, 0, 240),
    _i("MAX_BUYS_PER_HOUR", 2, 1, 12),
    _i("NO_BUY_OPENING_MINUTES", 30, 0, 120),
    # -- Ebene 2: Wiedereinstiegs-Sperre (#3604; settings.py). Handelstage, die ein
    #    vollstaendig verkaufter Titel nicht neu gekauft wird. 0 = aus. Auslieferung 1.
    _i("REENTRY_LOCKOUT_DAYS", 1, 0, 10),
    # -- Ebene 2: Platz-Sperre nach Stop-Loss-Verkauf (#3655; settings.py). Handelstage,
    #    die der befreite Platz fuer neue Titel gesperrt bleibt. 0 = aus. Auslieferung 1.
    _i("STOP_EXIT_SLOT_HOLD_DAYS", 1, 0, 10),
    # ── Ebene 2: Earnings-Proximity-Guard (#3349, Epic #2963; config.py). BUY-only
    #    Einstiegs-Sperre im Melde-Fenster (EDGAR 8-K 2.02). Default OFF/dark ⇒
    #    byte-identisch. POST exakt; PRE (0=aus) heuristisch bis Vorwärtskalender.
    _b("EARNINGS_GUARD_ENABLED", False),
    _i("EARNINGS_GUARD_POST_DAYS", 2, 0, 10),
    _i("EARNINGS_GUARD_PRE_DAYS", 0, 0, 10),
    # ── Ebene 2: Regime-Throttle (#3361, Epic #2963; config.py). Credit-geführtes
    #    Risk-off-Signal (core/engine/regime_signal.py) verkleinert NEUE Käufe um den
    #    Faktor, sobald der Tageswert im/über dem eingestellten Perzentil seiner eigenen
    #    Historie liegt. Default OFF/dark ⇒ byte-identisch; Faktor 1.00 = ohne Wirkung.
    _b("REGIME_THROTTLE_ENABLED", False),
    _i("REGIME_RISKOFF_PERCENTILE", 75, 50, 95),
    _f("REGIME_THROTTLE_SIZE_FACTOR", 0.5, 0.1, 1.0),
    # ── Ebene 2: Universum/Round-Table (settings.py::FULL_UNIVERSE_TRADING_ENABLED,
    #    settings.py::ROUND_TABLE_TOP_K_EVAL; TOP_K-Band #2781).
    _b("FULL_UNIVERSE_TRADING_ENABLED", True),
    _i("ROUND_TABLE_TOP_K_EVAL", 30, 10, 60),
    # ── Ebene 2: Stops/Sizing (Nähte aus S1 #3163; settings.py::STOP_LOSS_PCT).
    #    STOP_LOSS-Obergrenze strikt innerhalb |HARD_STOP_LOSS_PCT|=8.0
    #    (settings.py::HARD_STOP_LOSS_PCT,
    #    Leitplanke A6) — zusätzlich zur Laufzeit gegen den Ist-Wert geklemmt.
    #    #3632: STOP_LOSS_PCT ist der Broker-Backstop (ADR-020); BROKER_STOPS_ENABLED
    #    schaltet ihn (Auslieferung AN, Owner-Freigabe 24.09.2026). Die Verlust-Stufen
    #    des Intelligent Exit sind Settings; Bounds strikt innerhalb |HARD_STOP| = 8.0,
    #    Klemme (d) ordnet WATCH > CUT > ESCALATION > HARD_STOP. TAKE_PROFIT_PCT entfiel.
    _b("BROKER_STOPS_ENABLED", True),
    _f("STOP_LOSS_PCT", 7.0, 1.0, 7.9, decimals=1),
    #    #3662: Panik-Schutz des Intelligent Exit (im Fenster nur Hard Stop) als
    #    Schalter + Fenster; an / 2,0 h = Auslieferungsstand (settings.py).
    _b("PANIC_PROTECTION_ENABLED", True),
    _f("PANIC_PROTECTION_HOURS", 2.0, 0.5, 4.0, decimals=1),
    _f("LOSS_WATCH_PCT", -2.0, -7.9, -0.5, decimals=1),
    _f("LOSS_CUT_PCT", -4.0, -7.9, -1.0, decimals=1),
    _f("LOSS_ESCALATION_PCT", -6.0, -7.9, -2.0, decimals=1),
    _f("MIN_POSITION_PERCENT", 0.05, 0.01, 0.10),
    _f("MAX_POSITION_PERCENT", 0.25, 0.05, 0.40),
    _f("MAX_POSITION_PERCENT_SIZING", 0.30, 0.05, 0.50),
    _f("MAX_TOTAL_EXPOSURE_PCT", 0.95, 0.50, 1.00),
    # ── Ebene 2: #3199 Skew-Sizing-Tilt (25Δ-RR-Schiefe → Größe; risk_manager.skew_size_tilt).
    #    Cap-Obergrenze 0.20 = die per-Order-Klemme des Tilts [1-cap, 1+cap]; Default OFF /
    #    cap 0.0 ⇒ tilt 1.0 ⇒ byte-identisch. Promote erst nach Paper/Prod-Messung (#3207).
    _b("SKEW_SIZE_TILT_ENABLED", False),
    _f("SKEW_SIZE_TILT_CAP", 0.0, 0.0, 0.20),
    # ── Ebene 2: #3619 Staerke des Vol-Einflusses auf die Groesse (settings.py). 0 = jede
    #    Position 1/N, 1 = volle Vol-Targeting-Wirkung (heute). Auslieferung 1.0 =>
    #    byte-identisch. Auf der Round-Table-Karte der VIXAware-Regler, solange die IV die
    #    Groesse steuert (agents_view); VIX_RISK_WEIGHT bleibt das Konsens-Gewicht des
    #    IV-aus-Pfads.
    _f("VIX_SIZE_INFLUENCE", 1.0, 0.0, 1.0),
    # ── Ebene 2: #3210 Coverage-Vorsichts-Sizing-Abschlag (√coverage, Prudenz —
    #    KEIN Alpha; risk_manager.coverage_size_discount). Stärke lambda ∈ [0,1]:
    #    discount = 1 - lambda*(1 - sqrt(coverage)). Default 0.0 ⇒ discount 1.0 ⇒
    #    byte-identisch. Promote erst nach Paper/Prod-Messung (ADR-R17, #3210).
    _f("COVERAGE_SIZING_STRENGTH", 0.0, 0.0, 1.0),
    # ── Ebene 2: #3284 Sizing-VERFAHREN (welcher Zielgewicht-Pfad; risk_manager.calculate_position_size).
    #    "off" = 7-Faktor-Conviction (Legacy-Rückfall) · "a" = striktes 1/N · "b" = 1/N × VIXAware-Vol
    #    (Default, Owner-Waiver 2026-09-09, Kapitalerhalt-vor-Rendite). Der Default MUSS dem
    #    config-Quell-Default entsprechen (test_trading_settings_registry_parity). Echtgeld bleibt
    #    hinter dem WORM-Live-Consent; Punkt 3 (Audit sizing_mode) ist Blocker davor.
    _e("CLEAN_WEIGHT_SIZING", "b", ("off", "a", "b")),
    # ── Ebene 2: Betriebslimits INNERHALB der ratifizierten Leitplanken (#3220/#3221).
    #    Owner-Entscheid 05.09.2026 nach dem HITL-Muster: die Leitplanke selbst — dass ein
    #    Deckel existiert und das ratifizierte Ceiling hält — bleibt unantastbar; der
    #    Betriebswert darunter ist einstellbar und WORM-auditiert. Genau so sind fünf der
    #    sechs HITL-Werte längst kundenseitig verstellbar, während HITL_ENABLED selbst
    #    boot-erzwungen bleibt.
    #    Obergrenzen sind KEINE erfundenen Zahlen, sondern die in #1599 ratifizierten
    #    Ceilings aus governance/iron_dome_policy.py — jeder Wert darüber wird geklemmt.
    #    Wirksam werden sie über load_policy(): config = Betriebs-Default, gespeicherte
    #    Policy = Admin-Übersteuerung, Ceiling = harte Grenze.
    _f(
        "COMPLIANCE_MAX_ORDER_VALUE",
        10_000.0,
        1.0,
        _MAX_ORDER_VALUE_CEILING,
        decimals=0,
    ),
    _i("COMPLIANCE_MAX_DAILY_TRADES", 10, 1, _MAX_DAILY_TRADES_CEILING),
)

REGISTRY: Dict[str, Setting] = {s.key: s for s in _SETTINGS}

# Leitplanken, die per GET sichtbar sind, aber NIE über diesen Endpoint erreichbar
# (Epic-Leitplanke 3 / A3 / B9 / A6). Nur Anzeige — die Registry kennt sie nicht.
_GUARDRAIL_VIEW_KEYS: Tuple[Tuple[str, float, int], ...] = (
    (
        "HARD_STOP_LOSS_PCT",
        -8.0,
        1,
    ),  # settings.py::HARD_STOP_LOSS_PCT (ADR — env-only Backstop)
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
    #     #3632: Verlust-Stufen des Intelligent Exit. Der Loss-Cut ist der Anker (er
    #     verkauft); die Eskalation wird darunter, die Beobachtung darueber gezogen;
    #     alles bleibt strikt ueber dem Hard Stop. Geklemmt, nie abgelehnt (sichtbar).
    cut = max(float(vals["LOSS_CUT_PCT"]), -hard + 0.2)
    vals["LOSS_CUT_PCT"] = cut
    vals["LOSS_ESCALATION_PCT"] = max(
        min(float(vals["LOSS_ESCALATION_PCT"]), cut - 0.1), -hard + 0.1
    )
    vals["LOSS_WATCH_PCT"] = max(float(vals["LOSS_WATCH_PCT"]), cut + 0.1)
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
    # #3250: TA-Feature-Voter — mean_voter, dark (OFF / weight 0.0), lesen nur
    # state["features"]-Skalare (macd_hist bzw. vol_ratio_5_20).
    {
        "name": "TrendAgent",
        "role_title": "Trend Analyst",
        "enable_flag": "TREND_AGENT_ENABLED",
        "weight_key": "TREND_AGENT_WEIGHT",
        "default_enabled": False,
        "default_weight": 0.0,
        "min_weight": 0.0,
        "max_weight": 1.5,
    },
    {
        "name": "VolumeConfirmationAgent",
        "role_title": "Volume Analyst",
        "enable_flag": "VOLUME_CONFIRM_AGENT_ENABLED",
        "weight_key": "VOLUME_CONFIRM_AGENT_WEIGHT",
        "default_enabled": False,
        "default_weight": 0.0,
        "min_weight": 0.0,
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
    # #3275: QualityAgent — mean_voter, dark (OFF / weight 0.0). Composite-Quality aus
    # dem PIT-Fundamentals-Feed (Gross-Profitability/ROA/Accruals/Finanzstärke).
    {
        "name": "QualityAgent",
        "role_title": "Quality Analyst",
        "enable_flag": "QUALITY_AGENT_ENABLED",
        "weight_key": "QUALITY_AGENT_WEIGHT",
        "default_enabled": False,
        "default_weight": 0.30,
        "min_weight": 0.10,
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
    # #3199 Skew-Sizing-Tilt — deterministischer Sizing-Input (per-Titel RR-Schiefe →
    # Größe), Geschwister zu VIXAware (IV-Niveau → Größe). KEIN direktionaler Vote:
    # fixe Rolle ``size_input`` ⇒ Position-Sizing-Stufe der S4-Karte, direkt unter VIX.
    # Der „weight"-Slider ist hier der Cap (max. Größen-Auslenkung ±cap, min 0.0/max 0.20).
    # Dark by default (OFF / cap 0.0 ⇒ tilt 1.0). Config: SKEW_SIZE_TILT_* (S2-Registry).
    {
        "name": "SkewSizeTilt",
        "role_title": "Options-Skew Sizer",
        "enable_flag": "SKEW_SIZE_TILT_ENABLED",
        "weight_key": "SKEW_SIZE_TILT_CAP",
        "default_enabled": False,
        "default_weight": 0.0,
        "min_weight": 0.0,
        "max_weight": 0.20,
        "role": "size_input",
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
        if spec["name"] == "VIXAwareRiskAgent" and iv_on:
            # #3619: while the IV drives the SIZE, the card's slider is the strength of
            # that size influence (0..1, ships 1.0) — the consensus weight would be a
            # dead control here (the agent is excluded from the mean). IV off => the
            # consensus weight VIX_RISK_WEIGHT as before.
            spec = dict(
                spec,
                weight_key="VIX_SIZE_INFLUENCE",
                default_weight=1.0,
                min_weight=0.0,
                max_weight=1.0,
            )
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
