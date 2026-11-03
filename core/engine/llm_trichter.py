"""QA-Trichter der LLM-Vorauswahl: Endstatus und Grund je Vorschlag (#4656, Epic #4638).

Fuehrt drei Quellen zusammen, die es schon gibt, und liest sie nur:

- den Lauf aus dem ``LaufSpeicher`` (``core.engine.llm_ranking_lauf``): geliefert und vorab
  verworfen,
- die Round-Table-Entscheidung je Symbol (``core.round_table.recent_decisions``),
- das Iron-Dome-Ergebnis je Symbol, d. h. den Ausfuehrungscode, den der Order-Pfad ueber
  ``order_executor._rec_outcome`` in ``core.round_table.execution_outcomes`` ablegt.

Jeder Vorschlag des Laufs bekommt genau einen Endstatus aus ``ENDSTATUS`` und einen
maschinellen Grund. Lesbare Texte entstehen in der Konsole (Sub-Issue 6). Nur Anzeige: nichts
hier wirkt auf den Order-Pfad zurueck.

#4662: Ein Lauf unter SIM_MODE (``LlmRankingLauf.sim``) mit LLM-Quelle ist nicht belastbar —
das LLM kennt die Zukunft des simulierten Zeitraums (Grund ``llm_lookahead``).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from core.engine.llm_ranking_lauf import DAUERHAFT, KEINER, LlmRankingLauf

VERWORFEN_VORAB = "verworfen_vorab"
NOCH_NICHT_BEWERTET = "noch_nicht_bewertet"
ABGELEHNT_ROUND_TABLE = "abgelehnt_round_table"
ABGELEHNT_IRON_DOME = "abgelehnt_iron_dome"
ZUGELASSEN = "zugelassen"

# Rueckwaertskompatible Aliase
NICHT_BEWERTET = NOCH_NICHT_BEWERTET
KEIN_HANDEL = ABGELEHNT_ROUND_TABLE
BLOCKIERT = ABGELEHNT_IRON_DOME
AUSGEFUEHRT = ZUGELASSEN
AUSSTEHEND = ZUGELASSEN
HITL_GEHALTEN = ABGELEHNT_IRON_DOME
UNBEKANNT = ABGELEHNT_IRON_DOME

ENDSTATUS = frozenset(
    {
        VERWORFEN_VORAB,
        NOCH_NICHT_BEWERTET,
        ABGELEHNT_ROUND_TABLE,
        ABGELEHNT_IRON_DOME,
        ZUGELASSEN,
    }
)

GRUND_KEINE_ENTSCHEIDUNG = "keine_round_table_entscheidung"
GRUND_LLM_LOOKAHEAD = "llm_lookahead"

# #4861: die eigene Liste (Quelle ``liste``). Unter SIM_MODE bleibt sie belastbar — sie kennt
# keine Kurse —, traegt aber den Hinweis, dass der Nutzer sie mit dem Wissen von heute
# zusammengestellt hat.
QUELLE_LISTE = "liste"
HINWEIS_LISTE_HEUTIGES_WISSEN = "liste_heutiges_wissen"

# Die LLM-Ranking-Quellen wie im Rang-Trichter (``zyklus_kontext._LLM_QUELLEN``). Hier
# dupliziert, damit dieses Anzeige-Modul den Engine-Kern nicht importiert; ein Test haelt beide
# gleich (``test_llm_sim_grenze.py::test_llm_quellen_wie_im_rang_trichter``).
LLM_QUELLEN = ("llm", "llm_und_lstm")

# Ausfuehrungscodes aus core/round_table/execution_outcomes.py → Endstatus.
_AUSGEFUEHRT_CODES = frozenset({"executed", "resized", "closed"})


def _epoch(ts: Any) -> Optional[float]:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t).timestamp()


def _entscheidungen_des_laufs(
    lauf: LlmRankingLauf, decisions: Iterable[Mapping[str, Any]]
) -> Dict[str, Mapping[str, Any]]:
    """Die Entscheidung je Symbol, die NACH dem Lauf fiel. Eine aeltere gehoert zu einem
    frueheren Ausschnitt; ohne lesbaren Zeitstempel ist die Zuordnung nicht belegbar."""
    ab = lauf.zeitpunkt_utc.timestamp()
    je_symbol: Dict[str, Mapping[str, Any]] = {}
    for d in decisions:
        sym = str(d.get("symbol") or "").strip().upper()
        t = _epoch(d.get("timestamp"))
        if sym and t is not None and t >= ab and sym not in je_symbol:
            je_symbol[sym] = d
    return je_symbol


def _ist_iron_dome_ablehnung(code: str) -> bool:
    c = str(code or "").strip().lower()
    return (
        c.startswith("blocked:")
        or c in ("error", "hitl_held", "max_position_pct")
        or "blocked" in c
        or "reject" in c
    )


def _endstatus_handel(code: str) -> str:
    if _ist_iron_dome_ablehnung(code):
        return ABGELEHNT_IRON_DOME
    return ZUGELASSEN


def _ist_llm_quelle(quelle: Any) -> bool:
    return str(quelle or "").strip().lower() in LLM_QUELLEN


def _belastbarkeit(
    lauf: Optional[LlmRankingLauf], quelle: Any
) -> Tuple[bool, Optional[str]]:
    if lauf is not None and lauf.sim and _ist_llm_quelle(quelle):
        return False, GRUND_LLM_LOOKAHEAD
    return True, None


def ist_ergebnis_belastbar(strategie: Any) -> Tuple[bool, Optional[str]]:
    """Fuer den Sim-Vergleich (#4625): (belastbar, grund) eines Sim-Ergebnisses der Strategie.

    Ein Sim-Ergebnis mit LLM-Quelle (``llm``, ``llm_und_lstm``) ist nie belastbar — das LLM
    kennt den vergangenen Zeitraum (``llm_lookahead``). ``strategie`` ist ein Objekt oder ein
    Mapping mit ``ranking_quelle``; ohne das Feld gilt der Bestand, die LSTM-Quelle.
    """
    if isinstance(strategie, Mapping):
        quelle = strategie.get("ranking_quelle")
    else:
        quelle = getattr(strategie, "ranking_quelle", None)
    if _ist_llm_quelle(quelle):
        return False, GRUND_LLM_LOOKAHEAD
    return True, None


def liste_als_lauf(
    zeitpunkt: datetime,
    rang: List[str],
    verworfen: Mapping[str, str],
    sim: bool,
    text: str = "",
) -> LlmRankingLauf:
    """#4861: der Pruefstand der eigenen Liste als Pseudo-Lauf fuer ``baue_trichter``.

    Nie gespeichert und nie im Lauf-Protokoll (Option B des Plans verworfen). ``prompt_hash``
    nennt die Version des Listentexts, ``anbieter = "liste"``, kein Modell.
    """
    return LlmRankingLauf(
        zeitpunkt_utc=zeitpunkt,
        prompt_hash=(
            "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        ),
        modell=None,
        anbieter=QUELLE_LISTE,
        ok=True,
        geliefert=list(rang),
        verworfen=dict(verworfen),
        lauf_id=QUELLE_LISTE,
        sim=bool(sim),
    )


def _hinweis(lauf: Optional[LlmRankingLauf], quelle: Any) -> Optional[str]:
    if lauf is not None and lauf.sim and str(quelle or "").strip() == QUELLE_LISTE:
        return HINWEIS_LISTE_HEUTIGES_WISSEN
    return None


def _leere_zaehlstufen() -> Dict[str, int]:
    return dict.fromkeys(
        (
            "vorgeschlagen",
            "verworfen_vorab",
            "an_round_table",
            "bewertet",
            "handelssignal",
            "ausgefuehrt",
        ),
        0,
    )


def baue_trichter(
    lauf: Optional[LlmRankingLauf],
    decisions: Iterable[Mapping[str, Any]],
    iron_dome_ergebnisse: Mapping[str, str],
    liste_status: str = KEINER,
    liste_alter_s: Optional[float] = None,
    quelle: str = "llm",
) -> Dict[str, Any]:
    """Zaehlstufen und Symbolliste (Status, Grund) eines Laufs. ``decisions`` sind neueste
    zuerst (wie ``get_recent_round_table_decisions``); ``iron_dome_ergebnisse`` bildet das
    Symbol auf den Ausfuehrungscode ab. Ohne Lauf: leerer Trichter. ``quelle`` ist die
    eingestellte Ranking-Quelle; Sim-Lauf und LLM-Quelle ergeben ``belastbar = False``.
    #4861: beide Rueckgabepfade tragen ``quelle`` und ``hinweis`` (Sim-Lauf der eigenen Liste:
    ``liste_heutiges_wissen``, belastbar bleibt).
    #4960: beide Pfade tragen ``websuche``, ``anbieter`` (effektiver Anbieter des Laufs) und
    ``n_quellen``, nie die URLs selbst. Ohne Lauf und fuer die eigene Liste gibt es keine
    Aussage: ``websuche`` und ``anbieter`` sind ``None``.
    """
    stufen = _leere_zaehlstufen()
    belastbar, belastbar_grund = _belastbarkeit(lauf, quelle)
    if lauf is None:
        return {
            "quelle": quelle,
            "hinweis": None,
            "lauf_id": None,
            "lauf_zeitpunkt_utc": None,
            "websuche": None,
            "anbieter": None,
            "n_quellen": 0,
            "liste_status": KEINER,
            "liste_alter_s": None,
            "sim": False,
            "belastbar": belastbar,
            "belastbar_grund": belastbar_grund,
            "n_vorgeschlagen": 0,
            "n_universum": 0,
            "n_round_table": 0,
            "n_iron_dome": 0,
            "zaehlstufen": stufen,
            "symbole": [],
        }

    entscheidungen = _entscheidungen_des_laufs(lauf, decisions)
    symbole: List[Dict[str, str]] = []
    n_round_table = 0
    n_iron_dome = 0

    for sym in lauf.geliefert:
        sym_norm = str(sym).strip().upper()
        d = entscheidungen.get(sym_norm)
        if d is None:
            status, grund = NOCH_NICHT_BEWERTET, GRUND_KEINE_ENTSCHEIDUNG
        else:
            stufen["bewertet"] += 1
            gatekeeper_approved = d.get("gatekeeper_approved")
            gatekeeper_reason = d.get("gatekeeper_reason")
            aktion = str(d.get("signal_action") or d.get("action") or "").upper()
            if gatekeeper_approved is False or aktion not in ("BUY", "SELL"):
                status = ABGELEHNT_ROUND_TABLE
                if gatekeeper_approved is False and gatekeeper_reason:
                    grund = str(gatekeeper_reason)
                elif gatekeeper_reason and aktion not in ("BUY", "SELL"):
                    grund = str(gatekeeper_reason)
                else:
                    grund = f"signal_{(aktion or 'hold').lower()}"
            else:
                n_round_table += 1
                stufen["handelssignal"] += 1
                code = str(
                    iron_dome_ergebnisse.get(sym_norm)
                    or iron_dome_ergebnisse.get(sym)
                    or d.get("execution_outcome")
                    or "pending"
                )
                status = _endstatus_handel(code)
                grund = code
                if status == ZUGELASSEN:
                    n_iron_dome += 1
                    if code in _AUSGEFUEHRT_CODES:
                        stufen["ausgefuehrt"] += 1
        symbole.append({"symbol": sym, "status": status, "grund": grund})
    for sym, grund in lauf.verworfen.items():
        symbole.append({"symbol": sym, "status": VERWORFEN_VORAB, "grund": grund})

    n_universum = len(lauf.geliefert)
    n_vorgeschlagen = n_universum + len(lauf.verworfen)
    stufen["an_round_table"] = n_universum
    stufen["verworfen_vorab"] = len(lauf.verworfen)
    stufen["vorgeschlagen"] = n_vorgeschlagen

    return {
        "quelle": quelle,
        "hinweis": _hinweis(lauf, quelle),
        "lauf_id": lauf.lauf_id,
        "lauf_zeitpunkt_utc": lauf.zeitpunkt_utc.isoformat(),
        "websuche": None if quelle == QUELLE_LISTE else bool(lauf.websuche),
        "anbieter": lauf.kanonischer_anbieter,
        "n_quellen": len(lauf.quellen),
        "liste_status": liste_status,
        "liste_alter_s": liste_alter_s,
        "sim": bool(lauf.sim),
        "belastbar": belastbar,
        "belastbar_grund": belastbar_grund,
        "n_vorgeschlagen": n_vorgeschlagen,
        "n_universum": n_universum,
        "n_round_table": n_round_table,
        "n_iron_dome": n_iron_dome,
        "zaehlstufen": stufen,
        "symbole": symbole,
    }


__all__ = [
    "DAUERHAFT",
    "ENDSTATUS",
    "GRUND_LLM_LOOKAHEAD",
    "HINWEIS_LISTE_HEUTIGES_WISSEN",
    "baue_trichter",
    "ist_ergebnis_belastbar",
    "liste_als_lauf",
]
