"""Paritaetspruefung der schnellen Stimmen-Simulation gegen ``run_sim`` (#4862, RLW-10b).

Plan: ``docs/4862-rlw-10b-paritaetspruefung-der-schnellen-stimmen/implementation_plan.md``.

Ein reiner Vergleich zweier Ergebnis-JSONs: links ein Strategie-Eintrag aus
``python -m core.sim.stimmen_sim``, rechts das Ergebnis von
``python -m research.run_signal_replay`` (``SimResult``). Kein Engine-Boot, kein Import aus
``research/`` (Epic-NFR Isolation) — die Referenz laeuft als eigener Prozess.

Abweichungen werden ihrer Ursache **zugeordnet**, nie abgezogen: Die Toleranz gilt immer
auf die Rohzahlen. Was nicht zugeordnet werden kann (Fuellung ohne Datum, Tag ohne
Gegenstueck, abgebrochener Referenzlauf), ist nicht geprueft und damit kein Bestehen.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# (tag, symbol, side, qty, fill_price) — ein gefuellter Trade, normalisiert.
Trade = Tuple[str, str, str, float, Optional[float]]
# (tag, symbol, side) — die Entscheidungsebene: ob an einem Tag gehandelt wird.
Schluessel = Tuple[str, str, str]


@dataclass(frozen=True)
class Toleranz:
    trades_abweichend: int
    tagesrendite_bp: float
    endbilanz_bp_je_tag: float


# ADR-4862-T: Paritaets-Toleranz der schnellen Sim gegen das Replay. Fundstelle: Epic #4625
#   "Leading indicators" — "BUY/HOLD zu 100 %, Tagesrendite auf 1 bp". Trades: 0 abweichende
#   (tag, symbol, side), auf alle Seiten ausgedehnt (das Sub-Issue sagt "Trades"). Endbilanz:
#   1 bp je Handelstag = die Obergrenze, die aus der Tagestoleranz folgt, wenn sich die
#   Abweichungen addieren; eine strengere Zahl waere ohne Messung eine Vermutung.
#   Gelockert wird nur durch Owner-Entscheidung, nie still.
TOLERANZ = Toleranz(trades_abweichend=0, tagesrendite_bp=1.0, endbilanz_bp_je_tag=1.0)

# Gleitkomma-Rest beim Vergleich gegen die Toleranz; weit unter jeder fachlichen Groesse.
_EPS = 1e-9


@dataclass(frozen=True)
class Paritaetsbericht:
    trades_nur_schnell: Tuple[Schluessel, ...]
    trades_nur_voll: Tuple[Schluessel, ...]
    trade_quote: float
    mengen_abweichung: Tuple[Tuple[str, str, str, float], ...]
    max_tagesrendite_bp: float
    endbilanz_bp: float
    ursachen: Tuple[Tuple[str, str, str, str], ...]
    tage_ohne_gegenstueck: Tuple[str, ...]
    nicht_zuordenbar: int
    voll_abgebrochen: bool
    n_tage: int
    toleranz: Toleranz
    innerhalb_toleranz: bool


def _trade(tag, o: Mapping) -> Trade:
    fp = o.get("fill_price")
    return (
        str(tag)[:10],
        str(o.get("symbol")).upper(),
        str(o.get("side")).upper(),
        float(o.get("qty") or 0.0),
        None if fp is None else float(fp),
    )


def trades_schnell(ergebnis: Mapping) -> List[Trade]:
    """Gefuellte Trades eines Strategie-Eintrags aus ``stimmen_sim.main``, sortiert."""
    orders = ergebnis["ergebnis"]["orders"]
    return sorted(_trade(o["tag"], o) for o in orders if o.get("status") == "filled")


def trades_voll(sim_result: Mapping) -> Tuple[List[Trade], List[Mapping]]:
    """Gefuellte Trades der vollen Sim (``tag`` aus ``filled_at``), sortiert.

    Eine gefuellte Zeile ohne ``filled_at`` ist keinem Tag zuzuordnen. Sie wird nicht still
    verworfen, sondern als zweiter Rueckgabewert geliefert (WARNING, CLAUDE.md §5.6).
    """
    trades, nicht_zuordenbar = [], []
    for o in sim_result.get("orders") or []:
        if o.get("status") != "filled":
            continue
        if not o.get("filled_at"):
            logger.warning(
                "StimmenParitaet: gefuellte Zeile %s %s ohne filled_at — keinem Tag "
                "zuzuordnen, zaehlt als nicht geprueft",
                o.get("side"),
                o.get("symbol"),
            )
            nicht_zuordenbar.append(o)
            continue
        trades.append(_trade(o["filled_at"], o))
    return sorted(trades), nicht_zuordenbar


def _tagesrenditen(start: float, kurve: Sequence[Mapping]) -> Dict[str, float]:
    """Rendite je Tag gegen den eigenen Vortag; der erste Tag gegen das Startkapital."""
    out, vorher = {}, float(start)
    for p in kurve:
        e = float(p["equity"])
        out[str(p["date"])[:10]] = e / vorher - 1.0 if vorher else 0.0
        vorher = e
    return out


def _ursache(schluessel: Schluessel, orders: Sequence[Mapping], ents) -> str:
    """Warum die schnelle Sim an dieser Stelle anders handelte — Auftrag vor Entscheidung."""
    tag, sym, side = schluessel
    for o in orders:
        if (str(o.get("tag")), str(o.get("symbol")).upper(), o.get("side")) == (
            tag,
            sym,
            side,
        ) and o.get("blocked_reason"):
            return str(o["blocked_reason"])
    for e in ents:
        if (str(e.get("tag")), str(e.get("symbol")).upper()) == (tag, sym):
            return str(e.get("grund"))
    return "keine_entscheidung"


def vergleiche(
    schnell: Mapping, voll: Mapping, n_tage: int, toleranz: Toleranz = TOLERANZ
) -> Paritaetsbericht:
    """Ein Strategie-Eintrag der schnellen Sim gegen das Ergebnis von ``run_sim``."""
    erg = schnell["ergebnis"]
    t_s = {t[:3]: t for t in trades_schnell(schnell)}
    t_v_liste, nicht_zuordenbar = trades_voll(voll)
    t_v = {t[:3]: t for t in t_v_liste}
    nur_s = tuple(sorted(set(t_s) - set(t_v)))
    nur_v = tuple(sorted(set(t_v) - set(t_s)))
    gemeinsam = sorted(set(t_s) & set(t_v))
    vereinigung = set(t_s) | set(t_v)
    quote = len(gemeinsam) / len(vereinigung) if vereinigung else 1.0

    mengen = []
    for k in gemeinsam:
        q_s, q_v = t_s[k][3], t_v[k][3]
        rel = abs(q_s - q_v) / abs(q_v) if q_v else abs(q_s - q_v)
        mengen.append((*k, round(rel, 9)))

    r_s = _tagesrenditen(erg["starting_equity"], erg.get("daily_equity") or [])
    r_v = _tagesrenditen(voll["starting_equity"], voll.get("daily_equity") or [])
    ohne = tuple(sorted(set(r_s) ^ set(r_v)))
    for tag in ohne:
        logger.warning(
            "StimmenParitaet: Tag %s nur in %s Sim — ohne Gegenstueck, nicht geprueft",
            tag,
            "schneller" if tag in r_s else "voller",
        )
    abw = [abs(r_s[t] - r_v[t]) * 10_000 for t in sorted(set(r_s) & set(r_v))]
    max_bp = round(max(abw), 6) if abw else 0.0

    e_v = float(voll["ending_equity"])
    end_bp = round(abs(float(erg["ending_equity"]) - e_v) / e_v * 10_000, 6)

    orders, ents = erg.get("orders") or [], schnell.get("entscheidungen") or []
    ursachen = tuple(
        (*k, _ursache(k, orders, ents)) for k in sorted(set(nur_s) | set(nur_v))
    )
    abgebrochen = bool(voll.get("timed_out") or voll.get("halted"))
    if abgebrochen:
        logger.warning("StimmenParitaet: Referenzlauf abgebrochen — nicht vergleichbar")

    innerhalb = (
        len(nur_s) + len(nur_v) <= toleranz.trades_abweichend
        and max_bp <= toleranz.tagesrendite_bp + _EPS
        and end_bp <= toleranz.endbilanz_bp_je_tag * n_tage + _EPS
        and not ohne
        and not nicht_zuordenbar
        and not abgebrochen
    )
    return Paritaetsbericht(
        trades_nur_schnell=nur_s,
        trades_nur_voll=nur_v,
        trade_quote=round(quote, 9),
        mengen_abweichung=tuple(mengen),
        max_tagesrendite_bp=max_bp,
        endbilanz_bp=end_bp,
        ursachen=ursachen,
        tage_ohne_gegenstueck=ohne,
        nicht_zuordenbar=len(nicht_zuordenbar),
        voll_abgebrochen=abgebrochen,
        n_tage=int(n_tage),
        toleranz=toleranz,
        innerhalb_toleranz=innerhalb,
    )


# Strategie-Feld -> Config-Name der vollen Sim (Kommentar in StimmenSimStrategie).
_CONFIG_FELDER = {
    "max_positions": "FULL_UNIVERSE_MAX_POSITIONS",
    "pacing_aktiv": "INTRADAY_TIMING_ENABLED",
    "buy_cooldown_minuten": "GLOBAL_BUY_COOLDOWN_MINUTES",
    "max_buys_pro_stunde": "MAX_BUYS_PER_HOUR",
    "strict_ml_requires_rl": "GATEKEEPER_STRICT_ML_REQUIRES_RL",
    "material_min_pct": "MATERIAL_ENTRY_MIN_PCT_OF_TARGET",
}
# settings.py liest diese Namen mit int(); ein Bruch waere dort ein ValueError beim Boot.
_GANZZAHLIG = {"max_positions", "buy_cooldown_minuten", "max_buys_pro_stunde"}
# Felder ohne Config-Gegenstueck in der vollen Sim: gewichte/Schwellen (Modulkonstanten der
# ConsensusEngine), kosten_bp (SimFillEngine bucht keine), startkapital (VirtualLiveBroker
# fest 100 000), max_drawdown_limit (risk_manager.py, Portfolio-Stop fest 7 %).
_NUR_AUSLIEFERUNG = (
    "gewichte",
    "buy_threshold",
    "sell_threshold",
    "kosten_bp",
    "startkapital",
    "max_drawdown_limit",
)


def referenz_setzungen(strategie) -> List[str]:
    """``--set NAME=WERT``-Argumente, mit denen die volle Sim dieselbe Strategie faehrt.

    Ein Feld, das die volle Sim nicht per Config kennt, bricht ab, sobald es vom
    Auslieferungswert abweicht — ein still eingesetzter Standardwert waere ein anderer Lauf.
    """
    from core.sim.stimmen_sim import StimmenSimStrategie

    auslieferung = StimmenSimStrategie(name=strategie.name)
    for feld in _NUR_AUSLIEFERUNG:
        if getattr(strategie, feld) != getattr(auslieferung, feld):
            raise ValueError(
                f"nicht abbildbar: {feld}={getattr(strategie, feld)!r} hat in der vollen "
                f"Sim kein Config-Gegenstueck (Auslieferung {getattr(auslieferung, feld)!r})"
            )
    setzungen = []
    for feld, name in _CONFIG_FELDER.items():
        wert = getattr(strategie, feld)
        if feld in _GANZZAHLIG:
            if float(wert) != int(wert):
                raise ValueError(
                    f"nicht abbildbar: {feld}={wert!r}, {name} ist ganzzahlig"
                )
            wert = int(wert)
        elif isinstance(wert, bool):
            wert = "True" if wert else "False"
        else:
            wert = float(wert)
        setzungen.append(f"{name}={wert}")
    return sorted(setzungen)


def bericht_json(bericht: Paritaetsbericht) -> str:
    """Deterministisch: sortierte Schluessel, keine Uhrzeit."""
    return json.dumps(asdict(bericht), indent=2, sort_keys=True)


def _lies(pfad: str) -> Dict:
    with open(pfad, encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Exit 0 == innerhalb der Toleranz, 1 == ausserhalb, 2 == nichts verglichen."""
    ap = argparse.ArgumentParser(prog="python -m core.sim.stimmen_paritaet")
    sub = ap.add_subparsers(dest="befehl", required=True)
    v = sub.add_parser("vergleiche")
    for name in ("--schnell", "--voll", "--strategie", "--out"):
        v.add_argument(name, required=True)
    r = sub.add_parser("referenz")
    r.add_argument("--strategie", required=True)
    a = ap.parse_args(argv)

    if a.befehl == "referenz":
        from core.sim.stimmen_sim import StimmenSimStrategie

        # Ein unbekanntes Feld bricht ab (TypeError), wie in stimmen_sim.main.
        strategie = StimmenSimStrategie(**_lies(a.strategie))
        print(" ".join(f"--set {kv}" for kv in referenz_setzungen(strategie)))
        return 0

    try:
        schnell_datei, voll = _lies(a.schnell), _lies(a.voll)
    except (OSError, ValueError) as exc:
        # Exit 1 heisst "ausserhalb"; ein Lesefehler ist kein Vergleich (argparse: 2).
        ap.error(f"nicht lesbar: {exc}")
    eintraege = [
        s for s in schnell_datei["strategien"] if s["strategie"] == a.strategie
    ]
    if len(eintraege) != 1:
        ap.error(f"Strategie {a.strategie!r}: {len(eintraege)} Eintraege in --schnell")
    schnell = eintraege[0]
    n_tage = max(1, len(schnell["ergebnis"].get("daily_equity") or []))
    bericht = vergleiche(schnell, voll, n_tage=n_tage)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        f.write(bericht_json(bericht))
    return 0 if bericht.innerhalb_toleranz else 1


if __name__ == "__main__":
    sys.exit(main())
