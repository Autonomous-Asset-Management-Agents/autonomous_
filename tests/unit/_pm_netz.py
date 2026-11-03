"""#4282 (H-5a) — Netz für Zulassung, Verdrängung und Bericht des PortfolioManager.

Plan: ``docs/4282-*/implementation_plan.md`` §2. Gemessen werden die Rückgabe der
Zulassung ``(erlaubt, Grund, zu_schliessen)``, die neue Debattenzeile, der Bestand nach
dem echten ``refresh_positions`` und die Rückgaben des Berichts. Echt laufen der ganze
``PortfolioManager`` (keine Methode am Exemplar ersetzt) und seine Helfer. Ersetzt ist
nur der Broker-Client. Gesteuert wird über das Config-Objekt und die Sim-Uhr, **nicht**
über Namen am Modul ``core.portfolio_manager`` (Entscheidung #4187 §3) — so übersteht das
Netz die Umzüge H-5c bis H-5k: die Mixins lesen dieselben Schalter zur Aufrufzeit.

Aufruf zum Neuschreiben der Referenz (nur mit Begründung im PR):

    python tests/unit/_pm_netz.py --schreibe
"""

from __future__ import annotations

import json
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

REFERENZ = _AI_BOT / "tests" / "fixtures" / "pm_netz_referenz_4282.json"

#: Dienstag, 11:00 ET — mitten im Handelstag, fünf Stunden Abstand zum ET-Datumswechsel.
T0 = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
EIGENKAPITAL = 5000.0  # ``get_account().equity``; überschreibt ``total_capital``
NUTZER = "netz"

#: Fester Bestand ``(Symbol, Stück, Einstand, Kurs)``. Scores nach
#: ``_calculate_position_scores`` ohne Alter: AAPL +10 % = 76, MSFT +2 % = 62,
#: XOM −8 % = 19,5 — XOM ist die schwächste Position. AAPL hält 22 % des Kapitals und
#: liegt damit im Totband seines Ziels (1/5 = 20 %, Band ab 15 %).
BESTAND = (
    ("AAPL", 10, 100.0, 110.0),
    ("MSFT", 5, 200.0, 204.0),
    ("XOM", 20, 50.0, 46.0),
)

#: Plan §2.2: diese Schalter setzt jedes Szenario ausdrücklich.
PFLICHT_SCHALTER = frozenset(
    {
        "BOOK_CAP_ENFORCEMENT_ENABLED",
        "DISPLACEMENT_ENABLED",
        "DISPLACEMENT_MAX_PER_SESSION",
        "DISPLACEMENT_RESPECTS_MIN_HOLD",
        "SMART_EXIT_MIN_HOLD_DAYS",
        "STOP_EXIT_SLOT_HOLD_DAYS",
        "CONSENSUS_RETENTION_THRESHOLD",
        "SIM_MODE",
    }
)

#: Grundlage aller Szenarien: die Pflicht-Schalter (Werkseinstellung, außer
#: ``STOP_EXIT_SLOT_HOLD_DAYS = 0``: kein Lockout-Speicher) und jeder weitere Schalter,
#: den die gefahrenen Pfade lesen — damit kein Wert aus der Umgebung des Runners
#: durchschlägt. ``__init__`` liest seine Werte per ``from config import …``; das läuft
#: über ``config.__getattr__`` auf dasselbe Objekt.
GRUNDLAGE = {
    "SIM_MODE": True,
    "SIM_DATE": "2026-10-06",
    "BOOK_CAP_ENFORCEMENT_ENABLED": True,
    "DISPLACEMENT_ENABLED": True,
    "DISPLACEMENT_MAX_PER_SESSION": 2,
    "DISPLACEMENT_RESPECTS_MIN_HOLD": True,
    "SMART_EXIT_MIN_HOLD_DAYS": 20.0,
    "STOP_EXIT_SLOT_HOLD_DAYS": 0,
    "CONSENSUS_RETENTION_THRESHOLD": 0.0,
    # Pfade, die die Zulassung zusätzlich liest
    "ROTATION_MIN_HOLD_CURRENT_LOT": False,
    "HOLDING_PERIOD_SCORE_FRESH": 30.0,
    "HOLDING_PERIOD_SCORE_SPAN_DAYS": 0.0,
    "REGIME_THROTTLE_ENABLED": False,  # sonst liest das Totband einen Cache
    "CLEAN_WEIGHT_SIZING": "b",
    "FULL_UNIVERSE_TRADING_ENABLED": False,
    "MAX_POSITIONS": 5,
    "MAX_POSITION_PERCENT_SIZING": 0.30,
    # ``PortfolioManager.__init__``
    "MAX_TRADES_PER_SYMBOL_PER_DAY": 5,
    "MIN_ORDER_INTERVAL_SEC": 900,
    "REBALANCE_COOLDOWN_HOURS": 2.0,
    "REBALANCE_DRIFT_THRESHOLD_PCT": 3.0,
    "POSITION_TOPUP_DEAD_BAND_REL": 0.25,
    "MAX_POSITION_PERCENT": 0.25,
    "MIN_POSITION_PERCENT": 0.05,
    "MIN_HOLD_HOURS": 1.0,
    "CONSECUTIVE_SELL_BYPASS_THRESHOLD": 8,
    "CONVICTION_EWMA_ENABLED": True,
    "CONVICTION_EWMA_ALPHA": 0.3,
    "DECONCENTRATION_TRIM_RESPECTS_CONVICTION": True,
}

#: Die Funktionen, deren Aufruf das Netz mitschneidet (``sys.setprofile``, kein Patch;
#: nach Funktionsname, damit ein Umzug in ein Mixin sie nicht verliert).
BEOBACHTET = frozenset(
    {
        "refresh_positions",
        "should_open_new_position",
        "debate_position_swap",
        "consensus_retention_veto",
        "_displacement_session_budget_ok",
        "_note_displacement",
        "_can_trade_symbol_when_room",
        "_can_trade_symbol",
        "_within_order_cooldown",
        "_slot_hold_reason",
        "_topup_dead_band_reason",
        "_clean_weight_target_pct",
        "get_weakest_position",
        "get_strongest_position",
        "get_portfolio_summary",
        "record_sell_signal",
        "reset_sell_signals",
        "clear_sell_signals_after_sale",
        "can_sell_position",
        "update_total_capital",
    }
)


@dataclass(frozen=True)
class Szenario:
    """Ein Szenario: Aufbau, Vorgeschichte und eine Folge von Aufrufen.

    Vorgeschichte: ``(symbol, seite, minuten_relativ_zu_T0)`` über ``record_trade``.
    Schritte: ``("zulassung", symbol, score)``, ``("summary",)``, ``("leeren",)``,
    ``("refresh",)``, ``("staerkste",)``, ``("schwaechste",)``,
    ``("signal"|"reset"|"loesche"|"kann_verkaufen", symbol)``, ``("kapital", wert)``.
    """

    name: str
    schritte: tuple
    max_positionen: int = 3
    vorgeschichte: tuple = ()
    schalter: tuple = ()
    zweige: tuple = ()
    nicht: tuple = ()
    erlaubt: bool | None = None
    grund_beginnt: str | None = None
    positionsabfrage_scheitert: bool = False


_FUENF_KAEUFE = tuple(("NVDA", "buy", m) for m in (-300, -240, -180, -120, -60))
_DEBATTE = ("_displacement_session_budget_ok", "get_weakest_position")

SZENARIEN: tuple[Szenario, ...] = (
    # (1) Fall 1: 3 von 5 Slots belegt, neuer Name mit Score 60.
    Szenario(
        "slot_frei_kauf",
        (("zulassung", "NVDA", 60.0),),
        max_positionen=5,
        zweige=("_can_trade_symbol_when_room", "_topup_dead_band_reason"),
        nicht=("debate_position_swap",),
        erlaubt=True,
        grund_beginnt="Room for new position",
    ),
    # (2) Fünf Käufe am selben ET-Tag, der letzte vor 60 min (> 15 min Order-Cooldown).
    Szenario(
        "slot_frei_tageslimit",
        (("zulassung", "NVDA", 60.0),),
        max_positionen=5,
        vorgeschichte=_FUENF_KAEUFE,
        zweige=("_within_order_cooldown", "_can_trade_symbol_when_room"),
        nicht=("debate_position_swap", "_topup_dead_band_reason"),
        erlaubt=False,
        grund_beginnt="NVDA at daily trade limit",
    ),
    # (3) Score 20 < 25.
    Szenario(
        "slot_frei_score_zu_niedrig",
        (("zulassung", "NVDA", 20.0),),
        max_positionen=5,
        zweige=("_can_trade_symbol_when_room",),
        nicht=("debate_position_swap", "_topup_dead_band_reason"),
        erlaubt=False,
        grund_beginnt="Opportunity score too low",
    ),
    # (4) AAPL gehalten mit 22 % >= 20 % x (1 - 0,25): Nachkauf im Totband.
    Szenario(
        "nachkauf_im_totband",
        (("zulassung", "AAPL", 60.0),),
        max_positionen=5,
        zweige=("_topup_dead_band_reason", "_clean_weight_target_pct"),
        nicht=("debate_position_swap",),
        erlaubt=False,
        grund_beginnt="AAPL within top-up dead-band",
    ),
    # (5) Volles Buch; NVDA vor 60 min gehandelt: 60 min > 15 min Order-Cooldown, aber
    # < 2 h REBALANCE_COOLDOWN_HOURS.
    Szenario(
        "voll_cooldown",
        (("zulassung", "NVDA", 80.0),),
        vorgeschichte=(("NVDA", "buy", -60),),
        zweige=("_topup_dead_band_reason", "_can_trade_symbol"),
        nicht=("debate_position_swap", "_displacement_session_budget_ok"),
        erlaubt=False,
        grund_beginnt="NVDA in cooldown - traded too recently",
    ),
    # (6) 40 − 19,5 = 20,5 > 15: starker Tausch gegen XOM. Bewusst unter 50 und über 10:
    # Eine verschobene Schwelle ``score_diff > 15`` landet im Zweig „Upgrade“ und ändert
    # den Grund (Gegenprobe im Walkthrough).
    Szenario(
        "voll_debatte_gewonnen",
        (("zulassung", "NVDA", 40.0),),
        zweige=_DEBATTE
        + ("debate_position_swap", "consensus_retention_veto", "_note_displacement"),
        erlaubt=True,
        grund_beginnt="Strong upgrade",
    ),
    # (7) 25 − 19,5 = 5,5 < 8: kein Zweig der Entscheidungslogik greift.
    Szenario(
        "voll_debatte_verloren",
        (("zulassung", "NVDA", 25.0),),
        zweige=_DEBATTE + ("debate_position_swap", "consensus_retention_veto"),
        nicht=("_note_displacement",),
        erlaubt=False,
        grund_beginnt="Keeping XOM: Score difference only",
    ),
    # (8) XOM vor zwei Tagen gekauft (Alter belegt), Haltefrist 20 Tage.
    Szenario(
        "voll_mindesthaltedauer",
        (("zulassung", "NVDA", 80.0),),
        vorgeschichte=(("XOM", "buy", -2 * 24 * 60),),
        zweige=_DEBATTE + ("debate_position_swap",),
        nicht=("consensus_retention_veto", "_note_displacement"),
        erlaubt=False,
        grund_beginnt="Keeping XOM: minimum holding period not met",
    ),
    # (9) Deckel 1: der erste Tausch verbraucht ihn, der zweite Aufruf am selben Tag
    # endet vor der Debatte.
    Szenario(
        "voll_sitzungsdeckel",
        (("zulassung", "NVDA", 80.0), ("zulassung", "AMD", 80.0)),
        schalter=(("DISPLACEMENT_MAX_PER_SESSION", 1),),
        zweige=_DEBATTE + ("debate_position_swap", "_note_displacement"),
        erlaubt=False,
        grund_beginnt="displacement session cap reached",
    ),
    # (10) Hauptschalter der Verdrängung aus.
    Szenario(
        "verdraengung_aus",
        (("zulassung", "NVDA", 80.0),),
        schalter=(("DISPLACEMENT_ENABLED", False),),
        zweige=("_can_trade_symbol",),
        nicht=("debate_position_swap", "_displacement_session_budget_ok"),
        erlaubt=False,
        grund_beginnt="displacement disabled",
    ),
    # (11) ``get_all_positions`` wirft; Buchdeckel scharf.
    Szenario(
        "refresh_gescheitert_buchdeckel",
        (("zulassung", "NVDA", 80.0),),
        positionsabfrage_scheitert=True,
        zweige=("_within_order_cooldown",),
        nicht=("_slot_hold_reason", "debate_position_swap"),
        erlaubt=False,
        grund_beginnt="slot_unverified",
    ),
    # (12) Bericht mit Bestand, dann über ein leeres Buch.
    Szenario(
        "bericht_summary",
        (("summary",), ("leeren",), ("summary",)),
        zweige=(
            "get_portfolio_summary",
            "get_weakest_position",
            "get_strongest_position",
        ),
    ),
    # (13) Stärkste und schwächste Position, dann über ein leeres Buch.
    Szenario(
        "bericht_staerkste_schwaechste",
        (
            ("refresh",),
            ("staerkste",),
            ("schwaechste",),
            ("leeren",),
            ("refresh",),
            ("staerkste",),
            ("schwaechste",),
        ),
        zweige=("get_weakest_position", "get_strongest_position"),
    ),
    # (14) AAPL vor 30 min gekauft: Mindesthaltedauer 1 h offen, Bypass erst ab 8 Signalen.
    Szenario(
        "verkaufssignale",
        (
            ("refresh",),
            ("signal", "AAPL"),
            ("signal", "AAPL"),
            ("kann_verkaufen", "AAPL"),
            ("reset", "AAPL"),
            ("kann_verkaufen", "AAPL"),
            ("signal", "AAPL"),
            ("loesche", "AAPL"),
            ("kann_verkaufen", "AAPL"),
            ("signal", "AAPL"),
        ),
        vorgeschichte=(("AAPL", "buy", -30),),
        zweige=(
            "record_sell_signal",
            "reset_sell_signals",
            "clear_sell_signals_after_sale",
            "can_sell_position",
        ),
    ),
    # (15) Gültig, 0 und None; danach überschreibt der refresh mit dem Eigenkapital.
    Szenario(
        "gesamtkapital",
        (("kapital", 12345.0), ("kapital", 0), ("kapital", None), ("refresh",)),
        zweige=("update_total_capital",),
    ),
)


# ---------------------------------------------------------------------------
# Außenwelt
# ---------------------------------------------------------------------------


class _Client:
    """Broker-Client mit festem Bestand, Feldnamen wie Alpaca (Text-Zahlen)."""

    def __init__(self, scheitert: bool):
        self.bestand = BESTAND
        self._scheitert = scheitert

    def get_account(self):
        return SimpleNamespace(equity=str(EIGENKAPITAL), account_number="PA-NETZ")

    def get_all_positions(self):
        if self._scheitert:
            raise ConnectionError("Netz H-5a: Positionsabfrage gescheitert")
        aus = []
        for symbol, stueck, einstand, kurs in self.bestand:
            wert = stueck * kurs
            gewinn = stueck * (kurs - einstand)
            aus.append(
                SimpleNamespace(
                    symbol=symbol,
                    qty=str(stueck),
                    avg_entry_price=str(einstand),
                    current_price=str(kurs),
                    market_value=str(wert),
                    unrealized_pl=str(gewinn),
                    unrealized_plpc=str(gewinn / (stueck * einstand)),
                )
            )
        return aus


# ---------------------------------------------------------------------------
# Normalisierung
# ---------------------------------------------------------------------------


def _wert(v):
    """JSON-fester, adressfreier Wert."""
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, (str, int, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {str(k): _wert(x) for k, x in sorted(v.items(), key=lambda kv: str(kv))}
    if isinstance(v, (list, tuple)):
        return [_wert(x) for x in v]
    return type(v).__name__


_POSITION_FELDER = (
    "qty",
    "avg_entry",
    "current_price",
    "market_value",
    "unrealized_pnl",
    "unrealized_pnl_pct",
    "momentum_score",
    "holding_period_score",
    "risk_adjusted_score",
    "conviction_score",
    "total_score",
    "days_held",
    "age_known",
)


def _position(p) -> dict | None:
    """``PositionScore`` ohne ``last_updated`` (Wanduhr)."""
    if p is None:
        return None
    aus = {f: _wert(getattr(p, f)) for f in _POSITION_FELDER}
    aus["symbol"] = p.symbol
    return aus


def _debattenzeile(zeile: dict) -> dict:
    """Debattenzeile ohne ``timestamp`` (Wanduhr): vermerkt wird nur, ob er ISO war."""
    aus = dict(zeile)
    try:
        datetime.fromisoformat(aus.pop("timestamp"))
        aus["timestamp"] = "iso"
    except (KeyError, TypeError, ValueError) as exc:
        aus["timestamp"] = f"kaputt: {exc}"
    return _wert(aus)


# ---------------------------------------------------------------------------
# Fahren
# ---------------------------------------------------------------------------


def schalter(sz: Szenario) -> dict:
    """Die Konfiguration, die das Szenario fährt: Grundlage plus seine Abweichungen."""
    return {**GRUNDLAGE, **dict(sz.schalter)}


@contextmanager
def umgebung(sz: Szenario):
    """Config-Objekt gesetzt, Sim-Uhr frisch und auf ``T0``; danach zurückgesetzt."""
    from config import get_config
    from core.sim.clock import get_sim_clock, reset_sim_clock

    with ExitStack() as st:
        for name, wert in schalter(sz).items():
            st.enter_context(patch.object(get_config(), name, wert, create=True))
        reset_sim_clock()
        st.callback(reset_sim_clock)
        uhr = get_sim_clock()
        uhr.current_time = T0
        yield uhr


def baue(sz: Szenario):
    """Echter ``PortfolioManager`` mit Vorgeschichte über ``record_trade`` — innerhalb
    von :func:`umgebung` aufzurufen. Die Uhr steht danach wieder auf ``T0``."""
    from core.portfolio_manager import PortfolioManager
    from core.sim.clock import get_sim_clock

    client = _Client(sz.positionsabfrage_scheitert)
    pm = PortfolioManager(
        client, 10_000.0, max_positions=sz.max_positionen, user_id=NUTZER
    )
    uhr = get_sim_clock()
    for symbol, seite, minuten in sz.vorgeschichte:
        uhr.current_time = T0 + timedelta(minutes=minuten)
        pm.record_trade(symbol, seite)
    uhr.current_time = T0
    return pm, client


def _schritt(pm, schritt):
    """Ein Aufruf: ``(Aufruf-Text, Ergebnis)``."""
    from core.portfolio_manager import OpportunityScore

    art, *rest = schritt
    if art == "zulassung":
        symbol, score = rest
        chance = OpportunityScore(
            symbol=symbol, current_price=100.0, model_confidence=0.5, total_score=score
        )
        return f"should_open_new_position({symbol}, {score})", list(
            pm.should_open_new_position(chance)
        )
    if art == "summary":
        return "get_portfolio_summary()", pm.get_portfolio_summary()
    if art == "leeren":
        pm.client.bestand = ()
        return "Broker-Bestand geleert", None
    if art == "refresh":
        return "refresh_positions()", sorted(pm.refresh_positions())
    if art == "staerkste":
        return "get_strongest_position()", _position(pm.get_strongest_position())
    if art == "schwaechste":
        return "get_weakest_position()", _position(pm.get_weakest_position())
    if art == "signal":
        return f"record_sell_signal({rest[0]})", pm.record_sell_signal(rest[0])
    if art == "reset":
        return f"reset_sell_signals({rest[0]})", pm.reset_sell_signals(rest[0])
    if art == "loesche":
        return (
            f"clear_sell_signals_after_sale({rest[0]})",
            pm.clear_sell_signals_after_sale(rest[0]),
        )
    if art == "kann_verkaufen":
        return f"can_sell_position({rest[0]})", list(pm.can_sell_position(rest[0]))
    if art == "kapital":
        return f"update_total_capital({rest[0]})", pm.update_total_capital(rest[0])
    raise ValueError(f"unbekannter Schritt {schritt!r}")


def fahre_schritte(sz: Szenario, pm) -> list:
    """Die Schritte eines Szenarios gegen einen gebauten Manager."""
    aus = []
    for schritt in sz.schritte:
        ab_debatte = len(pm.get_debate_history(limit=1000))
        aufruf, ergebnis = _schritt(pm, schritt)
        aus.append(
            {
                "aufruf": aufruf,
                "ergebnis": _wert(ergebnis),
                "debatte": [
                    _debattenzeile(z)
                    for z in pm.get_debate_history(limit=1000)[ab_debatte:]
                ],
                "bestand": [
                    _position(p) for _, p in sorted(pm._position_scores.items())
                ],
                "total_capital": _wert(pm.total_capital),
            }
        )
    return aus


@contextmanager
def _aufrufe_mitschneiden(gelaufen: dict):
    def _profil(frame, ereignis, _arg):
        if ereignis == "call" and frame.f_code.co_name in BEOBACHTET:
            name = frame.f_code.co_name
            gelaufen[name] = gelaufen.get(name, 0) + 1

    vorher = sys.getprofile()
    sys.setprofile(_profil)
    try:
        yield
    finally:
        sys.setprofile(vorher)


def fahre(sz: Szenario) -> dict:
    """Ein Szenario fahren. Liefert das Messergebnis als dict."""
    gelaufen: dict = {}
    with umgebung(sz):
        pm, _client = baue(sz)
        with _aufrufe_mitschneiden(gelaufen):
            schritte = fahre_schritte(sz, pm)
    return {
        "schritte": schritte,
        "gelaufen": {k: gelaufen[k] for k in sorted(gelaufen)},
    }


def messwerte(ergebnis: dict) -> int:
    """Zahl der Messwerte eines Szenarios (Leerlauf-Wächter)."""
    return sum(
        (s["ergebnis"] is not None) + len(s["debatte"]) for s in ergebnis["schritte"]
    )


# ---------------------------------------------------------------------------
# Befunde gegen die Referenz
# ---------------------------------------------------------------------------

NEU = "NEU"
FEHLT = "FEHLT"
ABWEICHUNG = "ABWEICHUNG"


def messe_alle(szenarien=SZENARIEN) -> dict:
    return {sz.name: fahre(sz) for sz in szenarien}


def befunde(referenz: dict, ist: dict) -> list[tuple[str, str, str]]:
    """(Art, Szenario, Text) je Abweichung. Leere Liste = Verhalten unverändert."""
    aus = []
    for name in sorted(set(referenz) - set(ist)):
        aus.append((FEHLT, name, "Referenz ohne gemessenes Szenario."))
    for name, jetzt in ist.items():
        alt = referenz.get(name)
        if alt is None:
            aus.append((NEU, name, "Szenario ohne Referenz — Referenz neu schreiben."))
            continue
        if alt.get("gelaufen") != jetzt.get("gelaufen"):
            aus.append(
                (
                    ABWEICHUNG,
                    name,
                    f"gelaufen: {alt.get('gelaufen')} -> {jetzt.get('gelaufen')}",
                )
            )
        alte, neue = alt.get("schritte", []), jetzt.get("schritte", [])
        if len(alte) != len(neue):
            aus.append((ABWEICHUNG, name, f"Schritte {len(alte)} -> {len(neue)}"))
        for i, (a, n) in enumerate(zip(alte, neue)):
            for feld in sorted(set(a) | set(n)):
                if a.get(feld) != n.get(feld):
                    aus.append(
                        (
                            ABWEICHUNG,
                            name,
                            f"Schritt {i} {feld}: "
                            f"{json.dumps(a.get(feld), sort_keys=True, ensure_ascii=False)}"
                            f" -> "
                            f"{json.dumps(n.get(feld), sort_keys=True, ensure_ascii=False)}",
                        )
                    )
    return aus


def veraendere_die_zulassung(ist: dict) -> bool:
    """Gegenprobe: der Zuschlag in ``voll_debatte_gewonnen`` wäre eine Ablehnung."""
    ergebnis = ist["voll_debatte_gewonnen"]["schritte"][-1]["ergebnis"]
    if ergebnis[0] is not True:
        return False
    ergebnis[0] = False
    return True


def veraendere_die_verdraengung(ist: dict) -> bool:
    """Gegenprobe: verdrängt würde eine andere Position als die gemessene."""
    ergebnis = ist["voll_debatte_gewonnen"]["schritte"][-1]["ergebnis"]
    if not ergebnis[2]:
        return False
    ergebnis[2] = "MSFT" if ergebnis[2] != "MSFT" else "AAPL"
    return True


def veraendere_den_bericht(ist: dict) -> bool:
    """Gegenprobe: die Summary zählte eine Position mehr."""
    summary = ist["bericht_summary"]["schritte"][0]["ergebnis"]
    if not summary or not summary.get("num_positions"):
        return False
    summary["num_positions"] += 1
    return True


def lade_referenz() -> dict:
    return json.loads(REFERENZ.read_text(encoding="utf-8"))


def schreibe_referenz(ist: dict) -> None:
    REFERENZ.write_text(
        json.dumps(ist, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    gemessen = messe_alle()
    if "--schreibe" in sys.argv:
        schreibe_referenz(gemessen)
        print(f"geschrieben: {REFERENZ}")
    else:
        print(json.dumps(gemessen, indent=2, sort_keys=True, ensure_ascii=False))
