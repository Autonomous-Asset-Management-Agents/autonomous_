# core/intelligent_exit.py
# Epic 2.4 — Intelligent Exit System: 5-Tier Kapitalschutz
# Ersetzt smart_exit.py als primären Exit-Mechanismus.
# Policy: CODING_POLICY.md §1 Compliance-First, §11.5 TDD
#
# Kernprinzip: Asymmetrische Behandlung von Verlierern vs Gewinnern
#   Verlierer: 5-stufiges Verlustmanagement (-2/-4/-6/-8% Hard Stop) + zeitbasierte Eskalation
#   Gewinner:  Dynamischer Trailing Stop (+2/+5/+10/+20/+35% Tiers, je weiter je höher)

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


def _config(name: str, default: float) -> float:
    """Get config value with fallback to default."""
    try:
        import config

        return getattr(config, name, default)
    except ImportError:
        return default


# === LOSS MANAGEMENT (Verlierer schneller schneiden) ===
LOSS_TIER_1_PCT = _config("LOSS_TIER_1_PCT", -2.0)  # Nach -2%: Beobachtungsmodus
LOSS_TIER_2_PCT = _config("LOSS_TIER_2_PCT", -4.0)  # Nach -4%: Erhöhte Wachsamkeit
LOSS_TIER_3_PCT = _config("LOSS_TIER_3_PCT", -6.0)  # Nach -6%: Aggressiver Exit
HARD_STOP_LOSS_PCT = _config("HARD_STOP_LOSS_PCT", -8.0)  # Absoluter Stop

# Zeit-basierte Verschärfung für Verlierer
LOSS_TIME_TIER_1_HOURS = 4.0  # Nach 4h bei Verlust: kritischer prüfen
LOSS_TIME_TIER_2_HOURS = 24.0  # Nach 24h bei Verlust: sehr kritisch
LOSS_TIME_TIER_3_HOURS = 72.0  # Nach 72h bei Verlust: ultimativ kritisch

# === WINNER MANAGEMENT (Gewinner laufen lassen) ===
# Dynamischer Trailing Stop: Je höher der Gewinn, desto weiter der Stop
TRAIL_PROFIT_TIERS = [
    # (min_profit_pct, trailing_stop_pct, min_hold_hours)
    (2.0, 1.5, 1.0),  # +2-5%: Trailing 1.5% (eng, Gewinn sichern)
    (5.0, 2.5, 2.0),  # +5-10%: Trailing 2.5%
    (10.0, 4.0, 4.0),  # +10-20%: Trailing 4% (weiter, Raum geben)
    (20.0, 6.0, 8.0),  # +20-35%: Trailing 6% (viel Raum)
    (35.0, 8.0, 12.0),  # +35%+: Trailing 8% (maximaler Raum)
]

# ADR-EXIT-02 (#2681): Mittelfrist-Trailing-Profil.
# Basis: Live-Churn-Befund 05.08.2026 — die legacy-Stufe (2.0, 1.5, 1.0) verkaufte
# HPE 30 Minuten nach der Aufstockung auf einen Rücksetzer von 1,6 % („TRAILING
# STOP: Drawdown 1.6% vom High") und PCG auf 2,2 %; beide wurden anschließend
# teurer zurückgekauft. Bei Einzelaktien-Tagesvolatilität ist ein 1,5-%-Trailing
# ab +2 % Gewinn nach 1 Stunde Rauschen, kein Gewinnschutz.
# Rationale der Werte: 6 % ist die kleinste Schwelle, die die Tages-Rücksetzer der
# belegten Februar-Gewinner (WDC +166 %, STX +163 % über 4 Monate) nicht auslöst;
# die Mindesthaltedauern (72–120 h) binden an den ~5-Tage-LSTM-Signalhorizont
# (#1952-Bake-off: ~5-Tage-Hold net Sharpe ~1,56 vs. daily-naive 0,758) und an das
# belegte Erfolgsmuster „Selektion + mittelfristiges Halten" (#1957/#2401).
# Die VERLUST-Seite (LOSS_TIER_*, HARD_STOP_LOSS_PCT) ist bewusst NICHT Teil des
# Profils — Verlierer werden unverändert geschnitten. Jährliche Review.
TRAIL_PROFIT_TIERS_MIDTERM = [
    # (min_profit_pct, trailing_stop_pct, min_hold_hours)
    (2.0, 6.0, 72.0),  # +2-5%: 6% Trailing, min. 3 Tage
    (5.0, 8.0, 72.0),  # +5-10%: 8%
    (10.0, 10.0, 96.0),  # +10-20%: 10%, min. 4 Tage
    (20.0, 12.0, 120.0),  # +20-35%: 12%, min. 5 Tage
    (35.0, 15.0, 120.0),  # +35%+: 15% (maximaler Raum für Mehrfach-Bagger)
]

_TRAIL_PROFILES = {
    "legacy": TRAIL_PROFIT_TIERS,
    "midterm": TRAIL_PROFIT_TIERS_MIDTERM,
}

# #3006 — the sub-lowest-tier band: a winner whose gain is BELOW the lowest tier's +2%
# threshold. #2681 widened the >= +2% tiers but left the pre-loop 1.5% / 1.0h default in
# place, so near-flat winners (0..+2%) were still trailing-exited on ~1.5% of intraday
# noise — the buy-high/sell-low churn measured live (IFF/PM/MU). The band now follows the
# active profile, keyed off the SAME EXIT_TRAIL_PROFILE:
#   legacy  -> (1.5, 1.0)  == the historical default, byte-identical rollback.
#   midterm -> None        == no trailing exit below +2% (a +0.5% gain is not a gain to
#                             protect). Loss side (LOSS_TIER_*, HARD_STOP_LOSS_PCT) untouched.
_TRAIL_SUBTIER = {
    "legacy": (1.5, 1.0),
    "midterm": None,
}


def _active_subtier():
    """The sub-lowest-tier rule for the active profile, resolved at call time.

    Returns ``(trailing_pct, min_hold)`` for a sub-+2% winner, or ``None`` when the
    profile prescribes no trailing exit in that band. Unknown profile → legacy's
    ``(1.5, 1.0)``; ``_active_trail_tiers`` already WARNs on an unknown value, so this
    stays silent to avoid a duplicate warning in the same evaluation.
    """
    name = _config_str("EXIT_TRAIL_PROFILE", "legacy").strip().lower()
    return _TRAIL_SUBTIER.get(name, (1.5, 1.0))


def _config_str(name: str, default: str) -> str:
    """String-Variante von ``_config`` — gleicher Modul-Attribut-Seam."""
    try:
        import config

        return str(getattr(config, name, default))
    except ImportError:
        return default


def _active_trail_tiers() -> list:
    """Die aktive Trailing-Tier-Tabelle — **zur Aufrufzeit** aufgelöst (#2681).

    Warum Call-Time und nicht Import-Time: die Modul-Konstanten oben werden beim
    Import fixiert; ein später gesetzter Env-/Config-Wert erreichte den
    Entscheidungspfad sonst nie (dieselbe Falle wie bei den Desktop-Env-Arms).

    ``EXIT_TRAIL_PROFILE`` = ``legacy`` (Default) → exakt die heutige Tabelle,
    byte-identisch. ``midterm`` → das Mittelfrist-Profil. Unbekannter Wert →
    fail-safe ``legacy`` **mit WARNING** (§5.6: nie stillschweigend).
    """
    name = _config_str("EXIT_TRAIL_PROFILE", "legacy").strip().lower()
    tiers = _TRAIL_PROFILES.get(name)
    if tiers is None:
        logging.warning(
            "EXIT_TRAIL_PROFILE=%r ist unbekannt — fällt zurück auf 'legacy' "
            "(gültig: %s).",
            name,
            ", ".join(sorted(_TRAIL_PROFILES)),
        )
        return TRAIL_PROFIT_TIERS
    return tiers


# Momentum-Bestätigung für Gewinner
MOMENTUM_SELL_THRESHOLD = _config(
    "MOMENTUM_SELL_THRESHOLD", -0.3
)  # LSTM unter -0.3 = Momentum endet
MOMENTUM_GRACE_PERIODS = 2  # 2 Zyklen warten bevor Momentum-Exit

# === NEWS & SENTIMENT ===
NEWS_NEGATIVE_THRESHOLD = -0.5  # News-Score unter -0.5 = stark negativ
NEWS_SELL_WEIGHT = 0.3  # Gewichtung von News im Gesamtscore

# === ALLGEMEIN ===
MIN_HOLD_HOURS = _config("MIN_HOLD_HOURS", 0.5)  # Mindestens 30min halten (Anti-Churn)
PANIC_PROTECTION_HOURS = _config(
    "PANIC_PROTECTION_HOURS", 2.0
)  # Erste 2h: Kein Panikverkauf

# Sell threshold score
SELL_THRESHOLD = 70.0


@dataclass
class ExitAnalysis:
    """Detaillierte Analyse einer Position für Exit-Entscheidung"""

    symbol: str
    pnl_pct: float
    hours_held: float

    # Einzelne Scores (0-100, höher = mehr Verkaufsdruck)
    loss_pressure_score: float = 0.0
    trailing_stop_score: float = 0.0
    momentum_fade_score: float = 0.0
    news_pressure_score: float = 0.0
    time_pressure_score: float = 0.0

    # Finale Entscheidung
    total_score: float = 0.0
    should_sell: bool = False
    reason: str = ""
    confidence: float = 0.0
    # #3180: RISK (hard stop-loss / trailing / panic) vs OPINION (momentum-fade / weighted
    # composite). The consensus-retention gate (#3180) may only ever veto an OPINION exit; a hard
    # risk stop must fire regardless of consensus. Default 'risk' is FAIL-SAFE: an untagged exit is
    # treated as risk → NEVER vetoed (mis-holding a crash is catastrophic; missing an opinion veto is
    # benign). OPINION returns below tag themselves explicitly.
    tier: str = "risk"


@dataclass
class PositionContext:
    """Kontext einer Position für Exit-Analyse"""

    symbol: str
    entry_price: float
    current_price: float
    high_water_mark: float
    hours_held: float
    entry_time: datetime

    # Technische Indikatoren
    lstm_prediction: float = 0.0
    rsi: float = 50.0
    adx: float = 20.0
    macd: float = 0.0

    # News
    news_score: float = 0.0
    recent_headlines: List[str] = field(default_factory=list)

    # Historische Daten
    momentum_history: List[float] = field(
        default_factory=list
    )  # Letzte N LSTM Predictions


def analyze_exit(ctx: PositionContext) -> ExitAnalysis:
    """
    Analysiert eine Position und entscheidet ob verkauft werden soll.

    Kernprinzip: Asymmetrische Behandlung von Verlierern vs Gewinnern
    - Verlierer: Je länger gehalten und je tiefer im Minus, desto mehr Druck zu verkaufen
    - Gewinner: Je höher im Plus, desto mehr Raum geben (dynamischer Trailing Stop)
    """
    pnl_pct = (
        ((ctx.current_price - ctx.entry_price) / ctx.entry_price) * 100
        if ctx.entry_price > 0
        else 0
    )
    drawdown_pct = (
        ((ctx.high_water_mark - ctx.current_price) / ctx.high_water_mark) * 100
        if ctx.high_water_mark > 0
        else 0
    )

    analysis = ExitAnalysis(
        symbol=ctx.symbol, pnl_pct=pnl_pct, hours_held=ctx.hours_held
    )

    # === PANIK-SCHUTZ: Erste 2 Stunden keine Verkäufe (außer Hard Stop) ===
    if ctx.hours_held < PANIC_PROTECTION_HOURS:
        if pnl_pct <= HARD_STOP_LOSS_PCT:
            analysis.should_sell = True
            analysis.reason = f"HARD STOP: {pnl_pct:.1f}% (unter {HARD_STOP_LOSS_PCT}%)"
            analysis.confidence = 1.0
            analysis.total_score = 100
            analysis.tier = "risk"  # #3180: hard stop — never gate-eligible
            return analysis
        else:
            analysis.reason = (
                f"Panic protection: Position unter {PANIC_PROTECTION_HOURS}h alt"
            )
            return analysis

    # === VERLUST-MANAGEMENT ===
    analysis.loss_pressure_score = _calculate_loss_pressure(pnl_pct, ctx.hours_held)

    # === GEWINN-MANAGEMENT (Trailing Stop) ===
    analysis.trailing_stop_score = _calculate_trailing_stop_score(
        pnl_pct, drawdown_pct, ctx.hours_held
    )

    # === MOMENTUM-ANALYSE ===
    analysis.momentum_fade_score = _calculate_momentum_fade(
        ctx.lstm_prediction, ctx.momentum_history, pnl_pct
    )

    # === NEWS-ANALYSE ===
    analysis.news_pressure_score = _calculate_news_pressure(ctx.news_score, pnl_pct)

    # === ZEIT-DRUCK (für Verlierer) ===
    analysis.time_pressure_score = _calculate_time_pressure(pnl_pct, ctx.hours_held)

    # === DIREKTE TRIGGER (Bypass weighted scoring wenn klar überschritten) ===
    # Trailing Stop klar überschritten = direkter Verkauf
    if analysis.trailing_stop_score >= 85:
        analysis.total_score = analysis.trailing_stop_score
        analysis.should_sell = True
        analysis.confidence = 1.0
        analysis.tier = "risk"  # #3180: hard trailing stop — never gate-eligible
        analysis.reason = f"TRAILING STOP: Drawdown {drawdown_pct:.1f}% vom High"
        return analysis

    # Loss Pressure sehr hoch = direkter Verkauf
    if analysis.loss_pressure_score >= 90:
        analysis.total_score = analysis.loss_pressure_score
        analysis.should_sell = True
        analysis.confidence = 1.0
        analysis.tier = "risk"  # #3180: hard loss cut — never gate-eligible
        analysis.reason = f"LOSS CUT: {pnl_pct:.1f}% Verlust (Score: {analysis.loss_pressure_score:.0f})"
        return analysis

    # Momentum Fade stark bei Gewinnern = Gewinne sichern
    if pnl_pct > 5 and analysis.momentum_fade_score >= 80:
        analysis.total_score = analysis.momentum_fade_score
        analysis.should_sell = True
        analysis.confidence = 0.9
        analysis.tier = "opinion"  # #3180: profit-taking on a winner — a judgment call, gate-eligible
        analysis.reason = (
            f"MOMENTUM FADE: Gewinn sichern bei +{pnl_pct:.1f}% (LSTM bearish)"
        )
        return analysis

    # === FINALE SCORE-BERECHNUNG ===
    # Gewichtung hängt davon ab ob Position im Plus oder Minus ist
    if pnl_pct < 0:
        # VERLIERER: Loss-Pressure und Zeit-Druck wichtiger
        weights = {
            "loss": 0.45,
            "trailing": 0.05,
            "momentum": 0.20,
            "news": 0.15,
            "time": 0.15,
        }
    else:
        # GEWINNER: Trailing Stop und Momentum wichtiger
        weights = {
            "loss": 0.00,
            "trailing": 0.40,
            "momentum": 0.35,
            "news": 0.15,
            "time": 0.10,
        }

    analysis.total_score = (
        analysis.loss_pressure_score * weights["loss"]
        + analysis.trailing_stop_score * weights["trailing"]
        + analysis.momentum_fade_score * weights["momentum"]
        + analysis.news_pressure_score * weights["news"]
        + analysis.time_pressure_score * weights["time"]
    )

    # === ENTSCHEIDUNG ===
    # Score über 70 = Verkaufen
    if analysis.total_score >= SELL_THRESHOLD:
        analysis.should_sell = True
        analysis.confidence = min(1.0, analysis.total_score / 100)
        # #3180: the weighted composite is a SOFT judgment exit (not a hard stop — those returned
        # above as tier='risk'), so it is gate-eligible.
        analysis.tier = "opinion"
        analysis.reason = _build_sell_reason(analysis)
    else:
        analysis.should_sell = False
        analysis.reason = f"Hold (Score: {analysis.total_score:.0f}/100)"

    return analysis


def _calculate_loss_pressure(pnl_pct: float, hours_held: float) -> float:
    """
    Berechnet Verkaufsdruck für Verlierer.
    Je tiefer im Minus + je länger gehalten = mehr Druck.
    """
    if pnl_pct >= 0:
        return 0.0

    # Basis-Score nach Verlust-Tiefe (more aggressive)
    base_score = 0.0
    if pnl_pct <= HARD_STOP_LOSS_PCT:
        return 100.0  # Absoluter Stop
    elif pnl_pct <= LOSS_TIER_3_PCT:
        base_score = 90.0
    elif pnl_pct <= LOSS_TIER_2_PCT:
        base_score = 70.0  # -4% should be serious
    elif pnl_pct <= LOSS_TIER_1_PCT:
        base_score = 40.0
    else:
        base_score = abs(pnl_pct) * 15  # Steeper increase

    # Zeit-Multiplikator: Länger halten = mehr Druck
    time_mult = 1.0
    if hours_held >= LOSS_TIME_TIER_3_HOURS:
        time_mult = 1.5
    elif hours_held >= LOSS_TIME_TIER_2_HOURS:
        time_mult = 1.4
    elif hours_held >= LOSS_TIME_TIER_1_HOURS:
        time_mult = 1.2

    return min(100.0, base_score * time_mult)


def _calculate_trailing_stop_score(
    pnl_pct: float, drawdown_pct: float, hours_held: float
) -> float:
    """
    Dynamischer Trailing Stop für Gewinner.
    Höherer Gewinn = weiterer Trailing Stop (mehr Raum zum Laufen).
    """
    if pnl_pct <= 0:
        return 0.0

    # Finde passendes Tier
    trailing_pct = None
    min_hold = None

    for min_profit, trail, hold in reversed(_active_trail_tiers()):  # #2681
        if pnl_pct >= min_profit:
            trailing_pct = trail
            min_hold = hold
            break

    if trailing_pct is None:
        # #3006: gain is below the lowest tier (< +2%). The sub-tier follows the active
        # profile — legacy keeps 1.5% / 1.0h, midterm prescribes no trailing exit here.
        _sub = _active_subtier()
        if _sub is None:
            return 0.0
        trailing_pct, min_hold = _sub

    # Noch nicht lang genug gehalten?
    if hours_held < min_hold:
        return 0.0

    # Trailing Stop ausgelöst?
    if drawdown_pct >= trailing_pct:
        # Score basiert darauf wie weit über dem Trailing Stop (more aggressive)
        overshoot = drawdown_pct - trailing_pct
        return min(100.0, 85 + overshoot * 15)

    # Noch nicht ausgelöst, aber nah dran?
    proximity = (drawdown_pct / trailing_pct) * 100 if trailing_pct > 0 else 0
    if proximity > 70:
        return proximity * 0.6  # Warnsignal

    return 0.0


def _calculate_momentum_fade(
    lstm_pred: float, momentum_history: List[float], pnl_pct: float
) -> float:
    """
    Erkennt wenn Momentum nachlässt (Zeit für Gewinnmitnahme).
    Für Gewinner: Verkaufe wenn LSTM bearish wird.
    Für Verlierer: Erhöhe Druck wenn LSTM weiter bearish.
    """
    score = 0.0

    # Aktuelles LSTM Signal
    if lstm_pred < MOMENTUM_SELL_THRESHOLD:
        if pnl_pct > 0:
            # Gewinner mit fallender Momentum = Zeit zu verkaufen (more aggressive)
            score = 60 + abs(lstm_pred) * 60
        else:
            # Verlierer mit bearish Momentum = zusätzlicher Druck
            score = 40 + abs(lstm_pred) * 40

    # Trend-Analyse: Waren die letzten N Predictions fallend?
    if momentum_history and len(momentum_history) >= 3:
        recent = momentum_history[-3:]
        if all(recent[i] > recent[i + 1] for i in range(len(recent) - 1)):
            # Fallender Trend
            score += 25

        # Durchschnitt der letzten Predictions
        avg = sum(recent) / len(recent)
        if avg < -0.2 and pnl_pct > 5:
            # Anhaltend bearish bei Gewinner = verkaufen
            score += 30

    return min(100.0, score)


def _calculate_news_pressure(news_score: float, pnl_pct: float) -> float:
    """
    News-basierter Verkaufsdruck.
    Negative News = mehr Druck zu verkaufen.
    """
    if news_score >= 0:
        return 0.0

    # Basis-Score aus News
    base = abs(news_score) * 100  # -1.0 = 100 Punkte

    # Bei Gewinnern: News weniger wichtig (Gewinne schützen)
    if pnl_pct > 10:
        base *= 0.5
    # Bei Verlierern: News verstärken Exit-Druck
    elif pnl_pct < -2:
        base *= 1.3

    return min(100.0, base)


def _calculate_time_pressure(pnl_pct: float, hours_held: float) -> float:
    """
    Zeit-basierter Druck für Verlierer.
    Gewinner bekommen keinen Zeitdruck.
    """
    if pnl_pct >= 0:
        return 0.0  # Gewinner haben keinen Zeitdruck

    # Verlierer: Je länger gehalten, desto mehr Druck
    if hours_held < 4:
        return 0.0
    elif hours_held < 24:
        return (hours_held - 4) / 20 * 30  # 0-30 Punkte
    elif hours_held < 72:
        return 30 + (hours_held - 24) / 48 * 30  # 30-60 Punkte
    else:
        return min(100.0, 60 + (hours_held - 72) / 24 * 10)  # 60+ Punkte


def _build_sell_reason(analysis: ExitAnalysis) -> str:
    """Baut lesbare Begründung für Verkauf."""
    reasons = []

    if analysis.loss_pressure_score >= 50:
        reasons.append(f"Loss pressure ({analysis.pnl_pct:.1f}%)")
    if analysis.trailing_stop_score >= 50:
        reasons.append("Trailing stop hit")
    if analysis.momentum_fade_score >= 50:
        reasons.append("Momentum fading")
    if analysis.news_pressure_score >= 50:
        reasons.append("Negative news")
    if analysis.time_pressure_score >= 50:
        reasons.append(f"Time pressure ({analysis.hours_held:.1f}h)")

    if not reasons:
        reasons.append(f"Combined score {analysis.total_score:.0f}")

    return f"SELL: {'; '.join(reasons)}"


def get_dynamic_trailing_stop_pct(pnl_pct: float) -> float:
    """
    Gibt den dynamischen Trailing Stop % für einen gegebenen Gewinn zurück.
    Für Logging und externe Nutzung.
    """
    if pnl_pct <= 0:
        return 0.0

    trailing_pct = None
    for min_profit, trail, _ in reversed(_active_trail_tiers()):  # #2681
        if pnl_pct >= min_profit:
            trailing_pct = trail
            break

    if trailing_pct is None:
        # #3006: sub-+2% band follows the profile (legacy 1.5%, midterm 0 = no trailing).
        _sub = _active_subtier()
        return 0.0 if _sub is None else _sub[0]

    return trailing_pct


# === TEST FUNCTIONS ===
def test_intelligent_exit():
    """
    Testfälle zur Validierung des Intelligent Exit Systems.
    """
    results = []

    # Test 1: Loss Pressure - Position bei -5%, 30h gehalten + bearish LSTM + negative news → Score > 70
    ctx1 = PositionContext(
        symbol="TEST1",
        entry_price=100.0,
        current_price=95.0,  # -5%
        high_water_mark=100.0,
        hours_held=30.0,
        entry_time=datetime.now() - timedelta(hours=30),
        lstm_prediction=-0.4,  # Bearish
        news_score=-0.4,  # Negative news
    )
    analysis1 = analyze_exit(ctx1)
    test1_pass = analysis1.total_score > 70
    results.append(
        ("Test 1 - Loss Pressure (-5%, 30h)", test1_pass, analysis1.total_score)
    )

    # Test 2: Trailing Stop - Position bei +25%, drawdown 8% from high → Score > 70
    # At +25% (125), with 6% trailing stop, sell if drops 8%
    ctx2 = PositionContext(
        symbol="TEST2",
        entry_price=100.0,
        current_price=115.0,  # Started at +25%, now at +15% (8% drawdown from 125)
        high_water_mark=125.0,  # Was at +25%
        hours_held=12.0,
        entry_time=datetime.now() - timedelta(hours=12),
        lstm_prediction=-0.2,  # Slightly bearish
        momentum_history=[0.3, 0.1, -0.1],  # Falling momentum
        news_score=0.0,
    )
    analysis2 = analyze_exit(ctx2)
    test2_pass = analysis2.total_score > 70
    results.append(
        (
            "Test 2 - Trailing Stop (+25%, 8% drawdown)",
            test2_pass,
            analysis2.total_score,
        )
    )

    # Test 3: Panic Protection - Position bei -3%, 1h gehalten → Score < 70
    ctx3 = PositionContext(
        symbol="TEST3",
        entry_price=100.0,
        current_price=97.0,  # -3%
        high_water_mark=100.0,
        hours_held=1.0,  # Only 1 hour
        entry_time=datetime.now() - timedelta(hours=1),
        lstm_prediction=-0.5,
        news_score=-0.5,
    )
    analysis3 = analyze_exit(ctx3)
    test3_pass = analysis3.total_score < 70
    results.append(
        ("Test 3 - Panic Protection (-3%, 1h)", test3_pass, analysis3.total_score)
    )

    # Test 4: Momentum Fade - Position bei +10%, LSTM = -0.6 with falling history → Score > 70
    ctx4 = PositionContext(
        symbol="TEST4",
        entry_price=100.0,
        current_price=110.0,  # +10%
        high_water_mark=112.0,
        hours_held=6.0,
        entry_time=datetime.now() - timedelta(hours=6),
        lstm_prediction=-0.6,  # Strongly bearish
        momentum_history=[0.5, 0.3, 0.1, -0.2, -0.4],  # Clearly falling trend
        news_score=-0.2,  # Slightly negative news
    )
    analysis4 = analyze_exit(ctx4)
    test4_pass = analysis4.total_score > 70
    results.append(
        ("Test 4 - Momentum Fade (+10%, LSTM=-0.6)", test4_pass, analysis4.total_score)
    )

    # Test 5: Winner Holding - Position bei +20%, Drawdown 3% → Score < 50
    ctx5 = PositionContext(
        symbol="TEST5",
        entry_price=100.0,
        current_price=117.0,  # +17% (was +20%, so 2.5% drawdown from 120)
        high_water_mark=120.0,
        hours_held=10.0,
        entry_time=datetime.now() - timedelta(hours=10),
        lstm_prediction=0.3,  # Still bullish
        momentum_history=[0.2, 0.3, 0.25],
        news_score=0.1,
    )
    analysis5 = analyze_exit(ctx5)
    test5_pass = analysis5.total_score < 50
    results.append(
        (
            "Test 5 - Winner Holding (+20%, 3% drawdown)",
            test5_pass,
            analysis5.total_score,
        )
    )

    # Print results
    print("\n" + "=" * 60)
    print("INTELLIGENT EXIT SYSTEM - TEST RESULTS")
    print("=" * 60)

    all_pass = True
    for name, passed, score in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status} | {name} | Score: {score:.1f}")
        if not passed:
            all_pass = False

    print("=" * 60)
    if all_pass:
        print("[SUCCESS] ALL TESTS PASSED!")
    else:
        print("[ERROR] SOME TESTS FAILED!")
    print("=" * 60 + "\n")

    return all_pass


if __name__ == "__main__":
    test_intelligent_exit()
