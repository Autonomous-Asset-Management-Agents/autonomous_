"""Schnelle Portfolio-Simulation mit vorberechneten Stimmen (#3712, RLW-10).

Plan: ``docs/3712-rlw-10-schnelle-portfolio-simulation-mit-vorbere/implementation_plan.md``.

Statt die Engine Tag fuer Tag zu booten, liest dieses Modul eine Stimmen-Tabelle
``(symbol, tag) -> {agent: (score, weight, vetoed)}`` und verrechnet sie je Strategie mit
deren Gewichten, Schwellen und Gates zu einer Tageskurve. Die Scores haengen nicht von den
Gewichten ab; nur der Konsens wird neu gerechnet — mit dem echten
``ConsensusEngine.aggregate`` (``metrics.replicate_consensus``).

Isolation (Epic-NFR): nur als Subprozess aufrufen (``python -m core.sim.stimmen_sim``); der
Engine-Prozess importiert dieses Modul nicht, und es importiert nichts aus ``research/``.
Determinismus: sortierte Iteration, keine Zufallszahlen, keine Uhrzeit im Ergebnis.
"""

from __future__ import annotations

import argparse
import bisect
import json
import logging
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from core.analysis.attribution.metrics import _has_directional_vote, replicate_consensus
from core.round_table.base_agent import VoteResult
from core.round_table.consensus import SIGNAL_BUY_THRESHOLD, SIGNAL_SELL_THRESHOLD
from core.sim.result import SimResult

logger = logging.getLogger(__name__)

# (score, weight, vetoed) — eine vorberechnete Stimme.
Stimme = Tuple[float, float, bool]

# Die beiden ML-Kernstimmen des Strict-ML-Gates. runner.py::_compliance_pruefen prueft sie
# per isinstance; der Store kennt nur Namen, keine Agenten-Objekte.
_LSTM, _RL = "LSTMSignalAgent", "RLConfidenceAgent"
# absendung_vorlauf.py: ohne ATR rechnet der echte BUY-Pfad mit 5 % des Kurses.
_ATR_ERSATZ_ANTEIL = 0.05
_ATR_FENSTER = 14


@dataclass(frozen=True)
class StimmenSimStrategie:
    """Eine Strategie als reines Datenobjekt — keine globale Config wird gelesen."""

    name: str
    gewichte: Mapping[str, float] = field(default_factory=dict)
    buy_threshold: float = SIGNAL_BUY_THRESHOLD
    sell_threshold: float = SIGNAL_SELL_THRESHOLD
    # Die Standardwerte folgen der Auslieferung (config: FULL_UNIVERSE_MAX_POSITIONS,
    # INTRADAY_TIMING_ENABLED, GLOBAL_BUY_COOLDOWN_MINUTES, MAX_BUYS_PER_HOUR,
    # GATEKEEPER_STRICT_ML_REQUIRES_RL, MATERIAL_ENTRY_MIN_PCT_OF_TARGET). Gelesen wird
    # die Config hier nie — die Strategie traegt ihre Werte selbst.
    max_positions: int = 20
    pacing_aktiv: bool = True
    buy_cooldown_minuten: float = 30.0
    max_buys_pro_stunde: int = 2
    strict_ml_requires_rl: bool = False
    # ADR-R07: 7 % Portfolio-Stop (risk_manager.py), hier gegen den laufenden Equity-Gipfel.
    max_drawdown_limit: float = 0.07
    material_min_pct: float = 0.0
    startkapital: float = 100_000.0
    # #3710 offen: 0 bp == die volle Sim (SimFillEngine bucht keine Kosten).
    kosten_bp: float = 0.0


@dataclass(frozen=True)
class Entscheidung:
    aktion: str  # "BUY" | "SELL" | "HOLD"
    score: float
    grund: str


def tausche_gewichte(
    stimmen: Mapping[str, Stimme], gewichte: Mapping[str, float]
) -> List[VoteResult]:
    """Stimmen mit den Strategie-Gewichten; die Scores bleiben.

    Enthaltungs-Schutz: Eine Stimme mit Gewicht 0.0 war eine Enthaltung und bleibt eine —
    ein positives Strategiegewicht macht daraus nie eine aktive Stimme. Ein Agent, den die
    Strategie nicht nennt, behaelt sein aufgezeichnetes Gewicht.

    Schaltet die Strategie einen Agenten ab (Gewicht 0.0), vetoet er auch nicht (#5071), wie
    live. Massgeblich ist das Strategie-Gewicht: Aufzeichnungen stempeln den DrawdownGuard
    mit 0.0, sein Veto galt trotzdem (``aufzeichnung._serialize_votes``, #3084).
    """
    votes = []
    for agent in sorted(stimmen):
        score, weight, vetoed = stimmen[agent]
        if weight != 0.0:
            weight = float(gewichte.get(agent, weight))
        if gewichte.get(agent) == 0.0:
            vetoed = False
        votes.append(VoteResult(agent, "", float(score), weight, "", bool(vetoed)))
    return votes


def strict_ml_blockiert(
    lstm_valid: bool, rl_valid: bool, lstm_disabled: bool, requires_rl: bool
) -> bool:
    """``gate_stufen._strict_ml_gate_blocks`` als pure Funktion: das Flag ist Argument.

    Der Drift-Waechter in ``test_3712_stimmen_sim_gates.py`` vergleicht beide ueber alle
    Kombinationen.
    """
    if not lstm_valid:
        return not lstm_disabled or (requires_rl and not rl_valid)
    return not rl_valid and requires_rl


def entscheide(
    votes: List[VoteResult], strategie: StimmenSimStrategie, lstm_aus: bool = False
) -> Entscheidung:
    """Konsens, dann die Gates der Phase 3 — mit den Schwellen der Strategie.

    Spiegelt ``metrics.consensus_positions``, nimmt die Schwellen aber als Argument statt
    aus den Modulkonstanten (die bleiben unberuehrt). Reihenfolge wie
    ``runner.py::_compliance_pruefen``: Strict-ML, dann Agenten-Veto. ``lstm_aus``: der
    ganze Store traegt keine LSTM-Stimme (``StimmenStore.ohne_lstm``, #5044).
    """
    score = replicate_consensus(votes)
    if not _has_directional_vote(votes):
        return Entscheidung("HOLD", score, "keine_richtungsstimme")
    lstm_valid = any(v.agent_name == _LSTM and v.weight > 0.0 for v in votes)
    rl_valid = any(v.agent_name == _RL and v.weight > 0.0 for v in votes)
    # lstm_disabled: der Store traegt keinen abstain_reason. An einem einzelnen Tag ist eine
    # abgeschaltete LSTM-Stimme von einer fehlenden nicht zu unterscheiden, also fail-closed;
    # nur ein Store ganz ohne LSTM-Zeile gilt als abgeschaltet.
    requires_rl = strategie.strict_ml_requires_rl
    if strict_ml_blockiert(lstm_valid, rl_valid, lstm_aus, requires_rl):
        # Wie runner.py: das Gate blockt jede Richtung, auch einen SELL.
        return Entscheidung("HOLD", score, "strict_ml")
    if any(v.vetoed for v in votes) and score >= strategie.sell_threshold:
        # gate_stufen._apply_agent_veto: ein Veto blockt alles ausser einem SELL.
        return Entscheidung("HOLD", score, "agent_veto")
    if score > strategie.buy_threshold:
        return Entscheidung("BUY", score, "konsens")
    if score < strategie.sell_threshold:
        return Entscheidung("SELL", score, "konsens")
    return Entscheidung("HOLD", score, "konsens")


class StimmenStore:
    """``(symbol, tag) -> {agent: (score, weight, vetoed)}`` — die Eingabe aus #4634."""

    def __init__(self, table: Mapping[Tuple[str, str], Mapping[str, Stimme]]):
        self._stimmen = dict(table)
        self._symbole = sorted({sym for sym, _tag in self._stimmen})

    def symbole(self) -> List[str]:
        return list(self._symbole)

    def stimmen(self, symbol: str, tag: str) -> Optional[Mapping[str, Stimme]]:
        """Die Stimmen genau dieses Tages — eine aeltere wird nicht still fortgeschrieben."""
        return self._stimmen.get((symbol, tag))

    def ohne_lstm(self) -> bool:
        """Wahr, wenn der ganze Store keine LSTM-Zeile traegt (#5044).

        Der Korpus der eigenen Pipeline hatte die Stimme nie (``OfflineStimmenRechner``).
        Das ist etwas anderes als ein Ausfall an einzelnen Tagen: Eine einzige Zeile laesst
        das Gate fail-closed.
        """
        return not any(_LSTM in stimmen for stimmen in self._stimmen.values())

    @classmethod
    def from_verzeichnis(cls, pfad: str) -> "StimmenStore":
        """Liest ``decisions.parquet`` (did, symbol, ts) und ``votes.parquet``
        (did, agent, score, weight, vetoed). Mehrere Zeitpunkte eines Tages: der letzte.
        """
        import pandas as pd

        dec = pd.read_parquet(
            os.path.join(pfad, "decisions.parquet"), columns=["did", "symbol", "ts"]
        )
        vot = pd.read_parquet(
            os.path.join(pfad, "votes.parquet"),
            columns=["did", "agent", "score", "weight", "vetoed"],
        )
        df = vot.merge(dec, on="did", how="inner")
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df["tag"] = df["ts"].dt.strftime("%Y-%m-%d")
        df = df[df["ts"] == df.groupby(["symbol", "tag"])["ts"].transform("max")]
        df = df.sort_values(["symbol", "tag", "agent"], kind="mergesort")
        table: Dict[Tuple[str, str], Dict[str, Stimme]] = {}
        for r in df.itertuples(index=False):
            stimme = (float(r.score), float(r.weight), bool(r.vetoed))
            table.setdefault((str(r.symbol), r.tag), {})[str(r.agent)] = stimme
        return cls(table)


def lade_kurse(pfad: str) -> Dict[str, Dict[str, float]]:
    """Tages-Schlusskurse ``tag -> {symbol: close}`` aus einem Parquet (tag, symbol, close)."""
    import pandas as pd

    df = pd.read_parquet(pfad, columns=["tag", "symbol", "close"])
    kurse: Dict[str, Dict[str, float]] = {}
    for tag, sym, close in zip(df["tag"], df["symbol"], df["close"]):
        kurse.setdefault(str(tag)[:10], {})[str(sym)] = float(close)
    return kurse


@dataclass
class Portfolio:
    """Cash und Positionen — gebucht wie ``SimFillEngine.process_orders``."""

    cash: float
    kosten_bp: float = 0.0
    positionen: Dict[str, float] = field(default_factory=dict)

    def kaufe(self, symbol: str, qty: float, preis: float) -> bool:
        kosten = qty * preis * (1.0 + self.kosten_bp / 10_000.0)
        if self.cash < kosten:
            logger.warning(
                "StimmenSim: BUY %s, Cash %.2f < %.2f", symbol, self.cash, kosten
            )
            return False
        self.cash -= kosten
        self.positionen[symbol] = self.positionen.get(symbol, 0.0) + qty
        return True

    def verkaufe(self, symbol: str, preis: float) -> float:
        qty = self.positionen.pop(symbol, 0.0)
        self.cash += qty * preis * (1.0 - self.kosten_bp / 10_000.0)
        return qty

    def wert(self, kurse: Mapping[str, float]) -> float:
        return self.cash + sum(q * kurse[s] for s, q in self.positionen.items())


@contextmanager
def _slot_deckel(n: int) -> Iterator[None]:
    """Die Slot-Zahl der Strategie als ``effective_max_positions`` des Sizers.

    ``risk_bemessung`` liest den Deckel ausdruecklich als ``_rm.effective_max_positions``
    (Zugriffsregel im Modul-Docstring dort). Genau diese Naht wird fuer die Dauer EINES
    Aufrufs belegt und danach wiederhergestellt. Kein Thread-Schutz: das Modul laeuft als
    eigener Subprozess, nie im Engine-Prozess.
    """
    import core.risk_manager as rm

    original = rm.effective_max_positions
    rm.effective_max_positions = lambda: max(1, int(n))
    try:
        yield
    finally:
        rm.effective_max_positions = original


def bemesse_position(
    strategie: StimmenSimStrategie,
    *,
    equity: float,
    cash: float,
    preis: float,
    atr: float,
    conviction: float = 0.5,
    positionswerte: Sequence[float] = (),
) -> float:
    """``RiskManager.calculate_position_size`` mit der Slot-Zahl der Strategie.

    Die Argumente folgen dem BUY-Pfad in ``core/engine/absendung_vorlauf.py``. Der Halt ist
    ein ``LokalerHalt`` (#3485): ein Simulationsverlust haelt nie die Engine an.
    """
    import core.risk_manager as rm
    from core.kill_switch import LokalerHalt

    # Was _step_total_exposure_cap vom Broker liest: die Marktwerte der Positionen.
    werte = [{"market_value": w} for w in positionswerte]
    client = SimpleNamespace(get_all_positions=lambda: werte) if werte else None
    sizer = rm.RiskManager(
        client=client, total_capital=equity, kill_switch=LokalerHalt(), clock=None
    )
    with _slot_deckel(strategie.max_positions):
        return sizer.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=atr,
            confidence="high",
            size_scaler=1.0,
            market_data={"vix": 20.0},
            num_stocks_in_strategy=rm.effective_free_slots(len(positionswerte)),
            current_price=preis,
            account_cash=cash,
            allow_fractional=True,
            conviction_score=conviction,
        )


def _atr(verlauf: List[float], symbol: str, gewarnt: set) -> float:
    """Close-zu-Close-Proxy der 14-Tage-ATR; zu kurze Historie: 5 % des Kurses (WARNING)."""
    if len(verlauf) > _ATR_FENSTER:
        letzte = verlauf[-(_ATR_FENSTER + 1) :]
        return sum(abs(b - a) for a, b in zip(letzte, letzte[1:])) / _ATR_FENSTER
    if symbol not in gewarnt:
        gewarnt.add(symbol)
        logger.warning(
            "StimmenSim: %s mit zu kurzer Kurs-Historie — ATR = 5 %% des Kurses", symbol
        )
    return verlauf[-1] * _ATR_ERSATZ_ANTEIL


def _kauf_sperre(
    strategie: StimmenSimStrategie,
    p: Portfolio,
    gebremst: bool,
    jetzt: float,
    kaeufe: List[float],
) -> Optional[str]:
    """Gates vor der Bemessung: BuyPacingGuard (Gatekeeper 6b/6c), Slots, Circuit Breaker."""
    if strategie.pacing_aktiv and kaeufe:
        if jetzt - kaeufe[-1] < strategie.buy_cooldown_minuten:
            return "buy_pacing_cooldown"
        if sum(1 for t in kaeufe if jetzt - t < 60.0) >= strategie.max_buys_pro_stunde:
            return "buy_pacing_stunde"
    if len(p.positionen) >= strategie.max_positions:
        return "slot_deckel"
    if gebremst:
        return "circuit_breaker"
    return None


def _groessen_sperre(
    strategie: StimmenSimStrategie, wert: float, equity: float
) -> Optional[str]:
    from core.risk_manager import is_immaterial_entry

    if wert <= 0:
        return "groesse_null"
    if is_immaterial_entry(
        wert, equity, strategie.material_min_pct, max_positions=strategie.max_positions
    ):
        return "immaterial_entry"
    return None


def _order(tag, symbol, side, qty, preis, grund=None) -> Dict:
    """Ein Auftrag in der Form von ``OrderRecord``. ``grund`` None == gefuellt."""
    status = "filled" if grund is None else "rejected" if grund == "cash" else "blocked"
    return {
        "tag": tag,
        "symbol": symbol,
        "side": side,
        "qty": round(float(qty), 6),
        "status": status,
        "fill_price": preis if grund is None else None,
        "blocked_reason": grund,
    }


def simuliere(
    store: StimmenStore,
    kurse: Mapping[str, Mapping[str, float]],
    strategie: StimmenSimStrategie,
    von: str,
    bis: str,
) -> SimResult:
    """Eine Strategie ueber ``[von, bis]``: ein Zyklus je Handelstag, Fuellung zum Schluss.

    Ergebnis in der Form von ``SimResult``. Die Entscheidungen je Tag und Symbol haengen
    zusaetzlich als ``result.entscheidungen`` daran, ebenso ``result.ohne_lstm`` (kein
    Dataclass-Feld, damit ``SimResult.from_json`` unveraendert bleibt).
    """
    ohne_lstm = store.ohne_lstm()
    if ohne_lstm:
        logger.warning(
            "StimmenSim: Store ohne LSTM-Stimme → gerechnet wie LSTM abgeschaltet (#5044)"
        )
    p = Portfolio(cash=strategie.startkapital, kosten_bp=strategie.kosten_bp)
    alle = sorted(kurse)
    tage = alle[bisect.bisect_left(alle, von) : bisect.bisect_right(alle, bis)]
    letzter_kurs: Dict[str, float] = {}
    verlauf: Dict[str, List[float]] = {}
    kaeufe: List[float] = []
    atr_gewarnt: set = set()
    orders: List[Dict] = []
    entscheidungen: List[Dict] = []
    kurve: List[Dict] = []
    gipfel = strategie.startkapital

    for tag in tage:
        preise = kurse[tag]
        for sym in sorted(preise):
            letzter_kurs[sym] = preise[sym]
            verlauf.setdefault(sym, []).append(preise[sym])
        for sym in sorted(set(p.positionen) - set(preise)):
            logger.warning("StimmenSim: %s am %s zum letzten Kurs bewertet", sym, tag)
        equity = p.wert(letzter_kurs)
        gipfel = max(gipfel, equity)
        # Circuit Breaker gegen den Gipfel der simulierten Equity, vor dem Handel des Tages.
        gebremst = (
            gipfel > 0 and (gipfel - equity) / gipfel > strategie.max_drawdown_limit
        )
        # Ein Zyklus je Tag; die Pacing-Uhr laeuft in Minuten.
        jetzt = date.fromisoformat(tag).toordinal() * 1440.0

        for sym in sorted(set(store.symbole()) | set(p.positionen)):
            stimmen = store.stimmen(sym, tag)
            if sym not in preise or stimmen is None:
                fehlt = "Kurs" if sym not in preise else "Stimme"
                logger.warning(
                    "StimmenSim: %s am %s ohne %s — kein Handel", sym, tag, fehlt
                )
                continue
            e = entscheide(
                tausche_gewichte(stimmen, strategie.gewichte), strategie, ohne_lstm
            )
            entscheidungen.append(
                {"tag": tag, "symbol": sym, "aktion": e.aktion, "grund": e.grund}
            )
            preis = preise[sym]
            if e.aktion == "SELL" and sym in p.positionen:
                qty = p.verkaufe(sym, preis)
                orders.append(_order(tag, sym, "SELL", qty, preis))
            elif e.aktion == "BUY" and sym not in p.positionen:
                qty = 0.0
                grund = _kauf_sperre(strategie, p, gebremst, jetzt, kaeufe)
                if grund is None:
                    werte = [
                        q * letzter_kurs[s] for s, q in sorted(p.positionen.items())
                    ]
                    atr = _atr(verlauf[sym], sym, atr_gewarnt)
                    qty = bemesse_position(
                        strategie,
                        equity=equity,
                        cash=p.cash,
                        preis=preis,
                        atr=atr,
                        conviction=e.score,
                        positionswerte=werte,
                    )
                    grund = _groessen_sperre(strategie, qty * preis, equity)
                if grund is None and not p.kaufe(sym, qty, preis):
                    grund = "cash"
                if grund is None:
                    kaeufe.append(jetzt)
                orders.append(_order(tag, sym, "BUY", qty, preis, grund))

        kurve.append(
            {
                "date": tag,
                "equity": round(p.wert(letzter_kurs), 6),
                "cash": round(p.cash, 6),
                "n_positions": len(p.positionen),
            }
        )

    result = SimResult(
        sim_date=von,
        window=f"{von}..{bis}",
        cadence="1d-stimmen",
        symbols=store.symbole(),
        orders=orders,
        end_positions={s: round(q, 6) for s, q in sorted(p.positionen.items())},
        starting_equity=strategie.startkapital,
        ending_equity=kurve[-1]["equity"] if kurve else strategie.startkapital,
        n_cycles=len(tage),
        daily_equity=kurve,
    )
    result.entscheidungen = entscheidungen  # type: ignore[attr-defined]
    result.ohne_lstm = ohne_lstm  # type: ignore[attr-defined]
    return result


def ergebnis_dict(strategie: StimmenSimStrategie, result: SimResult) -> Dict:
    """Ausgabe je Strategie: ``SimResult.to_dict`` plus Tagesrenditen und Entscheidungen."""
    return {
        "strategie": strategie.name,
        "ergebnis": result.to_dict(),
        "daily_returns_pct": result.daily_returns_pct,
        "entscheidungen": getattr(result, "entscheidungen", []),
        "ohne_lstm": getattr(result, "ohne_lstm", False),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.sim.stimmen_sim")
    # --store: Verzeichnis mit decisions/votes.parquet; --kurse: Parquet (tag, symbol,
    # close), Standard <store>/kurse.parquet; --strategien: eine JSON-Datei je Strategie.
    for name in ("--store", "--von", "--bis", "--out"):
        ap.add_argument(name, required=True)
    ap.add_argument("--kurse")
    ap.add_argument("--strategien", nargs="+", required=True)
    a = ap.parse_args(argv)

    store = StimmenStore.from_verzeichnis(a.store)
    kurse = lade_kurse(a.kurse or os.path.join(a.store, "kurse.parquet"))
    ergebnisse = []
    for pfad in a.strategien:
        with open(pfad, encoding="utf-8") as f:
            # Ein unbekanntes Feld bricht hier ab (TypeError), statt still zu verfallen.
            strategie = StimmenSimStrategie(**json.load(f))
        result = simuliere(store, kurse, strategie, a.von, a.bis)
        ergebnisse.append(ergebnis_dict(strategie, result))
    text = json.dumps(
        {"von": a.von, "bis": a.bis, "strategien": ergebnisse}, indent=2, sort_keys=True
    )
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
