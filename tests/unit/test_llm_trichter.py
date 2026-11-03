"""#4656 (Epic #4638) — QA-Trichter: Endstatus und Grund je LLM-Vorschlag.

Plan: ``docs/4656-qa-trichter-daten-endstatus-und-grund-je-llm-vor/implementation_plan.md``.

Der Trichter fuehrt den Lauf (geliefert, vorab verworfen), die Round-Table-Entscheidung je
Symbol und das Iron-Dome-Ergebnis je Symbol zusammen. Jeder Vorschlag des Laufs bekommt genau
einen Endstatus mit einem maschinellen Grund; lesbare Texte entstehen erst in der Konsole.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.engine.llm_ranking_lauf import LlmRankingLauf

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

_LAUF_ZEIT = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)
_NACH_LAUF = "2026-10-22T14:05:00+00:00"
_VOR_LAUF = "2026-10-22T13:00:00+00:00"


def _lauf(geliefert, verworfen=None):
    return LlmRankingLauf(
        zeitpunkt_utc=_LAUF_ZEIT,
        prompt_hash="h",
        modell="m",
        anbieter="a",
        ok=True,
        geliefert=list(geliefert),
        verworfen=dict(verworfen or {}),
        lauf_id="lauf-1",
    )


def _entscheidung(symbol, aktion, ts=_NACH_LAUF):
    return {"symbol": symbol, "signal_action": aktion, "timestamp": ts}


def _je_symbol(trichter):
    return {e["symbol"]: e for e in trichter["symbole"]}


def test_zaehlstufen():
    from core.engine.llm_trichter import baue_trichter

    lauf = _lauf(
        ["AAPL", "MSFT", "NVDA", "AMD"],
        {"XYZ1": "unbekannt", "foo bar": "formfehler"},
    )
    decisions = [
        _entscheidung("AAPL", "BUY"),
        _entscheidung("MSFT", "BUY"),
        _entscheidung("NVDA", "HOLD"),
    ]
    iron_dome = {"AAPL": "executed", "MSFT": "blocked:order_value"}

    trichter = baue_trichter(lauf, decisions, iron_dome)
    stufen = trichter["zaehlstufen"]

    assert stufen == {
        "vorgeschlagen": 6,
        "verworfen_vorab": 2,
        "an_round_table": 4,
        "bewertet": 3,
        "handelssignal": 2,
        "ausgefuehrt": 1,
    }
    assert trichter["n_vorgeschlagen"] == 6
    assert trichter["n_universum"] == 4
    assert trichter["n_round_table"] == 2
    assert trichter["n_iron_dome"] == 1


def test_jeder_vorschlag_hat_endstatus():
    from core.engine.llm_trichter import ENDSTATUS, baue_trichter

    lauf = _lauf(
        ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"],
        {"XYZ1": "unbekannt", "META": "ueber_top_k"},
    )
    decisions = [
        _entscheidung("AAPL", "BUY"),
        _entscheidung("MSFT", "SELL"),
        _entscheidung("NVDA", "HOLD"),
        _entscheidung("TSLA", "BUY"),
    ]
    iron_dome = {"AAPL": "executed", "MSFT": "blocked:churn", "TSLA": "pending"}

    trichter = baue_trichter(lauf, decisions, iron_dome)
    je_symbol = _je_symbol(trichter)

    vorschlaege = set(lauf.geliefert) | set(lauf.verworfen)
    assert set(je_symbol) == vorschlaege
    assert len(trichter["symbole"]) == len(vorschlaege)
    for eintrag in trichter["symbole"]:
        assert eintrag["status"] in ENDSTATUS
        assert eintrag["grund"], eintrag
    assert je_symbol["AAPL"]["status"] == "zugelassen"
    assert je_symbol["AAPL"]["grund"] == "executed"
    assert je_symbol["MSFT"]["status"] == "abgelehnt_iron_dome"
    assert je_symbol["MSFT"]["grund"] == "blocked:churn"
    assert je_symbol["NVDA"]["status"] == "abgelehnt_round_table"
    assert je_symbol["NVDA"]["grund"] == "signal_hold"
    assert je_symbol["TSLA"]["status"] == "zugelassen"
    assert je_symbol["TSLA"]["grund"] == "pending"
    assert je_symbol["XYZ1"] == {
        "symbol": "XYZ1",
        "status": "verworfen_vorab",
        "grund": "unbekannt",
    }
    assert je_symbol["META"]["grund"] == "ueber_top_k"


def test_iron_dome_grund_je_symbol():
    from core.engine.llm_trichter import baue_trichter

    lauf = _lauf(["AAPL", "MSFT", "AMD"])
    decisions = [
        _entscheidung("AAPL", "BUY"),
        _entscheidung("MSFT", "BUY"),
        _entscheidung("AMD", "SELL"),
    ]
    iron_dome = {
        "AAPL": "blocked:order_value",
        "MSFT": "blocked:kill_switch",
        "AMD": "hitl_held",
    }

    je_symbol = _je_symbol(baue_trichter(lauf, decisions, iron_dome))

    assert je_symbol["AAPL"]["status"] == "abgelehnt_iron_dome"
    assert je_symbol["AAPL"]["grund"] == "blocked:order_value"
    assert je_symbol["MSFT"]["status"] == "abgelehnt_iron_dome"
    assert je_symbol["MSFT"]["grund"] == "blocked:kill_switch"
    assert je_symbol["AMD"]["status"] == "abgelehnt_iron_dome"
    assert je_symbol["AMD"]["grund"] == "hitl_held"


def test_nicht_bewertet_ist_eigener_status():
    from core.engine.llm_trichter import baue_trichter

    lauf = _lauf(["AAPL", "MSFT"], {"XYZ1": "unbekannt"})
    # MSFT wurde nur VOR dem Lauf bewertet: diese Entscheidung gehoert nicht zu diesem Lauf.
    decisions = [_entscheidung("MSFT", "BUY", ts=_VOR_LAUF)]

    trichter = baue_trichter(lauf, decisions, {"MSFT": "executed"})
    je_symbol = _je_symbol(trichter)

    for sym in ("AAPL", "MSFT"):
        assert je_symbol[sym]["status"] == "noch_nicht_bewertet"
        assert je_symbol[sym]["grund"] == "keine_round_table_entscheidung"
    assert je_symbol["XYZ1"]["status"] == "verworfen_vorab"
    assert trichter["zaehlstufen"]["bewertet"] == 0


def test_ohne_lauf_leerer_trichter_mit_status_keiner():
    from core.engine.llm_trichter import baue_trichter

    trichter = baue_trichter(None, [_entscheidung("AAPL", "BUY")], {"AAPL": "executed"})

    assert trichter["lauf_id"] is None
    assert trichter["liste_status"] == "keiner"
    assert trichter["liste_alter_s"] is None
    assert trichter["symbole"] == []
    assert set(trichter["zaehlstufen"].values()) == {0}
    assert trichter["n_vorgeschlagen"] == 0
    assert trichter["n_universum"] == 0
    assert trichter["n_round_table"] == 0
    assert trichter["n_iron_dome"] == 0


def test_gherkin_szenario_trichter_4656():
    """Gherkin aus #4656:
    Angenommen der letzte Lauf schlug 30 Ticker vor, 5 wurden vor dem Funnel verworfen
    Und der Round Table entschied HOLD für 20, BUY für 5, und der Iron Dome lehnte 1 BUY mit 'max_position_pct' ab
    Wenn GET /llm-vorauswahl/trichter abgefragt wird
    Dann liefert er n_vorgeschlagen=30, n_universum=25, n_round_table=5, n_iron_dome=4
    Und je Symbol einen Status aus {verworfen_vorab, abgelehnt_round_table, abgelehnt_iron_dome, zugelassen, noch_nicht_bewertet} mit Grund
    Und Alter und Status der aktuellen Liste
    """
    from collections import Counter

    from core.engine.llm_trichter import ENDSTATUS, baue_trichter

    geliefert = [f"SYM{i}" for i in range(1, 26)]
    verworfen = {f"BAD{i}": "qualitaet" for i in range(1, 6)}
    lauf = _lauf(geliefert, verworfen)

    decisions = []
    for i in range(1, 21):
        decisions.append(_entscheidung(f"SYM{i}", "HOLD"))
    for i in range(21, 26):
        decisions.append(_entscheidung(f"SYM{i}", "BUY"))

    iron_dome = {
        "SYM21": "executed",
        "SYM22": "executed",
        "SYM23": "executed",
        "SYM24": "executed",
        "SYM25": "max_position_pct",
    }

    trichter = baue_trichter(
        lauf, decisions, iron_dome, liste_status="frisch", liste_alter_s=42.0
    )

    assert trichter["n_vorgeschlagen"] == 30
    assert trichter["n_universum"] == 25
    assert trichter["n_round_table"] == 5
    assert trichter["n_iron_dome"] == 4
    assert trichter["liste_status"] == "frisch"
    assert trichter["liste_alter_s"] == 42.0

    je_sym = _je_symbol(trichter)
    assert len(je_sym) == 30
    for e in trichter["symbole"]:
        assert e["status"] in ENDSTATUS
        assert e["grund"]

    counts = Counter(e["status"] for e in trichter["symbole"])
    assert counts["verworfen_vorab"] == 5
    assert counts["abgelehnt_round_table"] == 20
    assert counts["abgelehnt_iron_dome"] == 1
    assert counts["zugelassen"] == 4
    assert counts["noch_nicht_bewertet"] == 0
    assert je_sym["SYM25"]["grund"] == "max_position_pct"


def test_gatekeeper_veto_wird_nicht_als_signal_hold_maskiert():
    """#4656 FINDING-02: Gatekeeper-/Drawdown-Veto mit signal_action=None
    muss den Gatekeeper-Grund ausweisen und nicht als 'signal_hold' enden."""
    from core.engine.llm_trichter import baue_trichter

    lauf = _lauf(["AAPL", "NVDA"])
    decisions = [
        {
            "symbol": "AAPL",
            "signal_action": None,
            "gatekeeper_approved": False,
            "gatekeeper_reason": "Agent-Veto durch: DrawdownGuardAgent",
            "timestamp": _NACH_LAUF,
        },
        {
            "symbol": "NVDA",
            "signal_action": None,
            "gatekeeper_approved": False,
            "gatekeeper_reason": "Missing core ML votes (LSTM/RL failed or excluded)",
            "timestamp": _NACH_LAUF,
        },
    ]

    trichter = baue_trichter(lauf, decisions, {})
    je_sym = _je_symbol(trichter)

    assert je_sym["AAPL"]["status"] == "abgelehnt_round_table"
    assert je_sym["AAPL"]["grund"] == "Agent-Veto durch: DrawdownGuardAgent"
    assert je_sym["NVDA"]["status"] == "abgelehnt_round_table"
    assert (
        je_sym["NVDA"]["grund"] == "Missing core ML votes (LSTM/RL failed or excluded)"
    )


# ── #4861: eigene S&P-500-Liste im QA-Trichter ──────────────────────────────────────────


def test_liste_als_lauf_stufen_und_verworfen_vorab():
    from core.engine.llm_trichter import DAUERHAFT, baue_trichter, liste_als_lauf

    lauf = liste_als_lauf(
        _LAUF_ZEIT,
        ["AAPL"],
        {"SAP": "nicht_im_universum", "12X": "formfehler"},
        sim=False,
        text="AAPL, SAP, 12X",
    )
    assert lauf.anbieter == "liste" and lauf.modell is None and lauf.ok is True
    assert lauf.prompt_hash.startswith("sha256:")

    trichter = baue_trichter(
        lauf,
        [_entscheidung("AAPL", "HOLD")],
        {},
        liste_status=DAUERHAFT,
        quelle="liste",
    )
    assert DAUERHAFT == "dauerhaft"
    assert trichter["liste_status"] == "dauerhaft"
    assert trichter["quelle"] == "liste"
    assert trichter["hinweis"] is None
    assert trichter["belastbar"] is True
    je_sym = _je_symbol(trichter)
    assert je_sym["SAP"] == {
        "symbol": "SAP",
        "status": "verworfen_vorab",
        "grund": "nicht_im_universum",
    }
    assert je_sym["12X"]["status"] == "verworfen_vorab"
    assert je_sym["12X"]["grund"] == "formfehler"
    assert trichter["zaehlstufen"]["vorgeschlagen"] == 3
    assert trichter["zaehlstufen"]["verworfen_vorab"] == 2


def test_liste_unter_sim_hinweis_und_belastbar():
    from core.engine.llm_trichter import baue_trichter, liste_als_lauf

    lauf = liste_als_lauf(_LAUF_ZEIT, ["AAPL"], {}, sim=True, text="AAPL")
    trichter = baue_trichter(lauf, [], {}, quelle="liste")
    assert trichter["sim"] is True
    assert trichter["hinweis"] == "liste_heutiges_wissen"
    assert trichter["belastbar"] is True
    assert trichter["belastbar_grund"] is None


def test_beide_rueckgabepfade_tragen_quelle_und_hinweis():
    from core.engine.llm_trichter import baue_trichter

    leer = baue_trichter(None, [], {}, quelle="liste")
    voll = baue_trichter(_lauf(["AAPL"]), [], {}, quelle="llm")
    assert set(leer) == set(voll)
    assert leer["quelle"] == "liste" and leer["hinweis"] is None
    assert voll["quelle"] == "llm" and voll["hinweis"] is None
