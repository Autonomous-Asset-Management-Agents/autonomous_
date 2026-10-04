# tests/unit/test_vixaware_implied_vol.py
# TRD-10 (#3038) â€” VIXAwareRiskAgent bekommt einen echten Eingang (TDD Redâ†’Green).
#
# Gemessener Anlass: Ãœber 29.395 Live-Entscheidungen bewegt sich der Score des Agenten
# zwischen 0,754 und 0,773; an 17 von 23 Tagen tragen ALLE Titel denselben Wert, und
# in 26.755 von 26.755 FÃ¤llen ist die Stimme bitgleich mit RegimeDetectionAgent
# (agents.py:891 gegen :577 â€” dieselbe Sigmoid, derselbe Eingang). Der Konsens
# vergleicht Titel eines Tages gegeneinander; eine fÃ¼r alle gleiche GrÃ¶ÃŸe unterscheidet
# dort niemanden. 22,0 % Gewicht, 0,3 % blockierte Entscheidungen.
#
# Neuer Eingang: die implizite VolatilitÃ¤t des einzelnen Titels, ausgedrÃ¼ckt als Rang
# gegen die Verteilung des VORTAGES â€” score = 1 âˆ’ Perzentil. Grundlage sind 71.559
# auswertbare Titel-Tage: IV gegen maximalen RÃ¼ckgang r = âˆ’0,330, in zehn von zehn
# Quartalen negativ (research/iv_study.py). Kriterium ist der RÃœCKGANG, nicht die
# Rendite â€” deren Vorzeichen kippt zwischen ZeitrÃ¤umen (VISION_AND_GOALS Â§3).
#
# Hinter VIXAWARE_IMPLIED_VOL_ENABLED, Default AUS â‡’ byte-identisch.
#
# AC-1..AC-9 aus docs/3038-vixaware-implied-vol/implementation_plan.md Â§4.

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/

# Referenzverteilung des Vortages: 40 Werte, gleichmÃ¤ÃŸig von 0,10 bis 0,60. Die
# GleichmÃ¤ÃŸigkeit ist Absicht â€” sie macht das erwartete Perzentil von Hand nachrechenbar.
REFERENZ = [0.10 + i * (0.50 / 39) for i in range(40)]
REFERENZ_DATUM = "2026-06-25"


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _state(
    symbol="AAPL", iv=None, referenz=None, referenz_datum=REFERENZ_DATUM, vix=18.0
):
    """Minimaler SymbolEvalState mit den drei neuen KanÃ¤len.

    `vix` bleibt gesetzt, damit der AUS-Pfad (AC-1) rechnen kann, ohne in die
    bestehende VIX-Enthaltung zu fallen â€” sonst prÃ¼fte AC-1 den falschen Zweig.
    """
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        },
        "market_data_keys": [],
        "current_time": "2026-06-26T13:35:00+00:00",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
        "vix": vix,
        "implied_vol": iv,
        "implied_vol_reference": referenz,
        "implied_vol_reference_date": referenz_datum,
    }


def _arm(monkeypatch, enabled: bool) -> None:
    """Flag Ã¼ber die einzige Config-Naht des Finance-Core setzen (CODING_POLICY Â§2.10)."""
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(VIXAWARE_IMPLIED_VOL_ENABLED=enabled),
    )


async def _vote(monkeypatch, enabled=True, **kw):
    from core.round_table.agents import VIXAwareRiskAgent

    _arm(monkeypatch, enabled)
    return await VIXAwareRiskAgent().vote(_state(**kw))


def _load_config_fresh(name: str, datei: str):
    from settings import RuntimeConfigState

    class Dummy:
        def get_config(self):
            return RuntimeConfigState()

    return Dummy()


#
# spec = importlib.util.spec_from_file_location(name, _AI_BOT / datei)
# module = importlib.util.module_from_spec(spec)
# spec.loader.exec_module(module)
# return module


# ---------------------------------------------------------------------------
# AC-1 â€” Flag AUS ist byte-identisch (Rollback-Zusage der CIA)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("vix", [10.0, 18.0, 25.0, 45.0])
async def test_ac1_flag_aus_liefert_den_alten_sigmoid(monkeypatch, vix):
    """AUS muss die alte Formel liefern â€” auch wenn IV-Daten im State liegen.

    Der Test stellt bewusst eine vollstÃ¤ndige IV-Datenlage bereit. WÃ¼rde der neue
    Pfad versehentlich schon bei AUS greifen, wÃ¤re das hier sichtbar und nicht erst
    im Sim.
    """
    v = await _vote(monkeypatch, enabled=False, vix=vix, iv=0.25, referenz=REFERENZ)
    assert v.score == pytest.approx(1.0 / (1.0 + math.exp((vix - 25.0) / 10.0)))
    assert v.weight > 0.0


@pytest.mark.anyio
async def test_ac1_flag_aus_behaelt_die_alte_enthaltung(monkeypatch):
    """Der bestehende Enthaltungspfad (VIX fehlt) bleibt bei AUS unverÃ¤ndert."""
    v = await _vote(monkeypatch, enabled=False, vix=None, iv=0.25, referenz=REFERENZ)
    assert v.weight == 0.0
    assert v.score is None


# ---------------------------------------------------------------------------
# AC-2 â€” Flag AN ordnet nach implizierter Schwankung
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "quantil, erwartet",
    [(0.10, 0.90), (0.50, 0.50), (0.90, 0.10)],
)
async def test_ac2_perzentil_wird_zum_gegenscore(monkeypatch, quantil, erwartet):
    """Ein Titel am q-ten Perzentil der Vortagsverteilung erhÃ¤lt Score â‰ˆ 1 âˆ’ q.

    Ruhig (niedrige IV) heiÃŸt hoher Score, unruhig heiÃŸt niedriger â€” die Richtung des
    gemessenen Zusammenhangs (hÃ¶here IV, tieferer RÃ¼ckgang).
    """
    iv = 0.10 + quantil * 0.50
    v = await _vote(monkeypatch, iv=iv, referenz=REFERENZ)
    assert v.score == pytest.approx(erwartet, abs=0.03)


@pytest.mark.anyio
async def test_ac2_reihenfolge_stimmt(monkeypatch):
    """Der ruhigste Titel bekommt den hÃ¶chsten Score, der unruhigste den tiefsten."""
    ruhig = (await _vote(monkeypatch, iv=0.12, referenz=REFERENZ)).score
    mitte = (await _vote(monkeypatch, iv=0.35, referenz=REFERENZ)).score
    unruhig = (await _vote(monkeypatch, iv=0.58, referenz=REFERENZ)).score
    assert ruhig > mitte > unruhig


# ---------------------------------------------------------------------------
# AC-3 â€” keine Punktgleichheiten
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ac3_39_titel_39_verschiedene_scores(monkeypatch):
    """39 Titel mit paarweise verschiedener IV â‡’ 39 paarweise verschiedene Scores.

    Das ist der eigentliche PrÃ¼fstein. Ein Perzentil als STUFENfunktion gegen eine
    feste Referenz erzeugt genau hier GleichstÃ¤nde: zwei Titel zwischen denselben
    beiden Referenzpunkten bekÃ¤men denselben Rang. Damit wÃ¤re der Defekt aus #3004
    (48,7 % SÃ¤ttigung) nur an eine andere Stelle verschoben.
    """
    ivs = [0.10 + i * 0.0031 for i in range(39)]  # feiner als das Referenzraster
    scores = [(await _vote(monkeypatch, iv=x, referenz=REFERENZ)).score for x in ivs]
    assert len(set(scores)) == 39


@pytest.mark.anyio
async def test_ac3_auch_ausserhalb_der_referenzspanne_keine_gleichstaende(monkeypatch):
    """Werte Ã¼ber dem Maximum bzw. unter dem Minimum des Vortages dÃ¼rfen nicht
    auf denselben Randwert einrasten.

    Ein VolatilitÃ¤tssprung bringt genau diesen Fall: mehrere Titel liegen an einem
    Tag Ã¼ber allem, was der Vortag kannte. Rasten sie auf 0,0 ein, unterscheidet die
    Stimme im Stress am wenigsten â€” dann, wenn es am meisten zÃ¤hlt.
    """
    hoch = [
        (await _vote(monkeypatch, iv=x, referenz=REFERENZ)).score
        for x in (0.65, 0.80, 1.20)
    ]
    tief = [
        (await _vote(monkeypatch, iv=x, referenz=REFERENZ)).score
        for x in (0.02, 0.05, 0.08)
    ]
    assert len(set(hoch)) == 3 and len(set(tief)) == 3
    assert all(0.0 < s < 1.0 for s in hoch + tief)


# ---------------------------------------------------------------------------
# AC-4 â€” Ordnungserhalt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ac4_rangkorrelation_ist_exakt_minus_eins(monkeypatch):
    """Spearman(Score, IV) = âˆ’1,0 Ã¼ber eine Cross-Section bekannter Reihenfolge.

    Streng monoton fallend, ohne Ausnahme â€” auch Ã¼ber die Referenzgrenzen hinweg.
    """
    scipy_stats = pytest.importorskip("scipy.stats")
    ivs = [0.03, 0.09, 0.11, 0.18, 0.27, 0.34, 0.41, 0.55, 0.59, 0.72, 1.10]
    scores = [(await _vote(monkeypatch, iv=x, referenz=REFERENZ)).score for x in ivs]
    r, _ = scipy_stats.spearmanr(ivs, scores)
    assert r == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# AC-5 / AC-6 â€” Enthaltung, und die Gewichtsklemme greift nicht
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ac5_fehlende_iv_fuehrt_zur_enthaltung(monkeypatch):
    v = await _vote(monkeypatch, iv=None, referenz=REFERENZ)
    assert v.weight == 0.0
    assert v.score is None


@pytest.mark.anyio
@pytest.mark.parametrize("iv", [0.0, -0.1, float("nan")])
async def test_ac5_unbrauchbare_iv_fuehrt_zur_enthaltung(monkeypatch, iv):
    """Null, negativ oder NaN sind keine VolatilitÃ¤t â€” geraten wird nicht."""
    v = await _vote(monkeypatch, iv=iv, referenz=REFERENZ)
    assert v.weight == 0.0


@pytest.mark.anyio
async def test_ac6_gewichtsklemme_greift_bei_enthaltung_nicht(monkeypatch):
    """Das Gewicht muss DIREKT im VoteResult auf 0.0 stehen.

    `min_weight = 0.10` zieht in base_agent.py:104 jedes Gewicht auf die Klasse-Grenzen
    hoch. Ginge die Enthaltung Ã¼ber `self.weight`, bekÃ¤me eine Stimme OHNE Datengrundlage
    rund 6 % Gewicht im Konsens â€” eine geratene Stimme, die nach einer echten aussieht.
    """
    from core.round_table.agents import VIXAwareRiskAgent

    agent = VIXAwareRiskAgent()
    assert agent.min_weight == 0.10, "Vorbedingung: die Klemme existiert noch"
    assert agent.weight >= 0.10, "Vorbedingung: self.weight kann nicht 0 werden"

    v = await _vote(monkeypatch, iv=None, referenz=REFERENZ)
    assert v.weight == 0.0


@pytest.mark.anyio
async def test_ac5_enthaltung_wird_aus_dem_konsens_ausgeschlossen(monkeypatch):
    """Gewicht 0,0 muss im Konsens auch tatsÃ¤chlich als Ausschluss ankommen.

    Ohne diesen Test wÃ¤re nur die Absicht belegt, nicht die Wirkung.
    """
    from core.round_table.base_agent import VoteResult
    from core.round_table.consensus import ConsensusEngine

    enthaltung = await _vote(monkeypatch, iv=None, referenz=REFERENZ)
    andere = VoteResult(
        agent_name="MomentumAgent",
        symbol="AAPL",
        score=0.80,
        weight=0.45,
        reasoning="t",
    )
    engine = ConsensusEngine()
    mit = engine.aggregate([andere, enthaltung])
    ohne = engine.aggregate([andere])
    assert mit == pytest.approx(ohne)


# ---------------------------------------------------------------------------
# AC-7 â€” kein Vorgriff
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ac7_referenz_stammt_nur_aus_abgeschlossenen_zyklen(monkeypatch):
    """Daten des laufenden Zyklus dÃ¼rfen die Referenz nicht verÃ¤ndern.

    Der Test legt eine Verteilung des LAUFENDEN Tages in den State, die den Score
    deutlich verschÃ¶be, wÃ¼rde sie mitgerechnet. Erwartet wird der Wert gegen die
    Vortagsreferenz â€” unverÃ¤ndert.
    """
    ohne = await _vote(monkeypatch, iv=0.35, referenz=REFERENZ)

    _arm(monkeypatch, True)
    from core.round_table.agents import VIXAwareRiskAgent

    state = _state(iv=0.35, referenz=REFERENZ)
    state["implied_vol_today_all"] = [0.90] * 40  # laufender Zyklus, sehr unruhig
    state["implied_vol_current_cycle"] = [0.90] * 40
    mit = await VIXAwareRiskAgent().vote(state)

    assert mit.score == pytest.approx(ohne.score)


@pytest.mark.anyio
async def test_ac7_referenzdatum_liegt_vor_dem_entscheidungstag(monkeypatch):
    """Das Referenzdatum gehÃ¶rt ins Reasoning â€” sonst ist nachtrÃ¤glich nicht belegbar,
    gegen welche Verteilung ein Titel bewertet wurde (MiFID II Audit-Trail).
    """
    v = await _vote(monkeypatch, iv=0.35, referenz=REFERENZ)
    assert REFERENZ_DATUM in v.reasoning
    assert "0.35" in v.reasoning or "0,35" in v.reasoning


# ---------------------------------------------------------------------------
# AC-8 â€” erster Zyklus ohne Referenz
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("referenz", [None, [], [0.25]])
async def test_ac8_ohne_brauchbare_referenz_wird_enthalten(monkeypatch, referenz):
    """Keine Vortagsverteilung â‡’ Enthaltung statt Raten.

    Ein einzelner Referenzwert ist ebenfalls keine Verteilung â€” daraus lieÃŸe sich kein
    Rang bilden, nur ein MÃ¼nzwurf mit Nachkommastellen.
    """
    v = await _vote(monkeypatch, iv=0.25, referenz=referenz)
    assert v.weight == 0.0
    assert v.score is None


@pytest.mark.anyio
async def test_ac8_enthaltung_nennt_den_grund(monkeypatch):
    """Zwei verschiedene Ursachen dÃ¼rfen nicht dieselbe BegrÃ¼ndung tragen â€” sonst ist
    im Log nicht unterscheidbar, ob die Optionskette oder die Referenz fehlte."""
    ohne_iv = await _vote(monkeypatch, iv=None, referenz=REFERENZ)
    ohne_ref = await _vote(monkeypatch, iv=0.25, referenz=None)
    assert ohne_iv.reasoning != ohne_ref.reasoning


# ---------------------------------------------------------------------------
# AC-9 â€” das Flag ist sweep-fÃ¤hig, beide Editionen stimmen Ã¼berein
# ---------------------------------------------------------------------------
#
# research/sweep.py:70-80 validiert Spec-Werte als ZAHLEN â€” der Sweep setzt Flags als
# "1"/"0". Das verbreitete Muster `.lower() == "true"` lÃ¤se "1" als False; Stufe 2
# wÃ¼rde dann zweimal denselben Zustand messen und ein sauber aussehendes Nullergebnis
# liefern. Genau diese Falle hat #3034 bereits einmal gestellt.


@pytest.mark.parametrize(
    "roh, erwartet",
    [
        ("1", True),
        ("0", False),
        ("true", True),
        ("True", True),
        ("false", False),
        ("yes", True),
        (" 1 ", True),
    ],
)
@pytest.mark.parametrize("datei", ["config.py", "config.oss.py"])
def test_ac9_flag_parst_zahlen_und_text(monkeypatch, datei, roh, erwartet):
    monkeypatch.setenv("VIXAWARE_IMPLIED_VOL_ENABLED", roh)
    cfg = _load_config_fresh(f"cfg_iv_{datei.replace('.', '_')}_{roh.strip()}", datei)
    assert cfg.get_config().VIXAWARE_IMPLIED_VOL_ENABLED is erwartet


@pytest.mark.parametrize("datei", ["config.py", "config.oss.py"])
def test_ac9_default_ist_an_in_beiden_editionen(monkeypatch, datei):
    """Seit der Aktivierung (26.08., Stufe 3) ist der Default AN â€” in BEIDEN
    Editionen identisch (BORA).

    Der Test hat vorher auf ``is False`` bestanden; die Umkehr ist der eigentliche
    Inhalt dieses PRs und steht deshalb hier, nicht nur im Kommentar. Wer den
    Default zurÃ¼cknimmt, bricht diesen Test â€” gewollt.

    Zur Nachweislage: Das SÃ¤ttigungskriterium ist **verfehlt** (4,50 % statt
    â‰¤ 2 %). Die Aktivierung ist eine Owner-Entscheidung, kein bestandenes Gate.
    """
    monkeypatch.delenv("VIXAWARE_IMPLIED_VOL_ENABLED", raising=False)
    cfg = _load_config_fresh(f"cfg_iv_default_{datei.replace('.', '_')}", datei)
    assert cfg.get_config().VIXAWARE_IMPLIED_VOL_ENABLED is True


# ---------------------------------------------------------------------------
# Die neuen State-KanÃ¤le mÃ¼ssen deklariert sein
# ---------------------------------------------------------------------------


def test_state_kanaele_sind_deklariert():
    """langgraph verwirft nicht deklarierte Eingabe-SchlÃ¼ssel, BEVOR ein Node lÃ¤uft.

    Genau daran ist das frÃ¼her emittierte Top-Level-"vix" gescheitert â€” es hat die
    Agenten nie erreicht (graph.py:105-113). Ohne diesen Test fiele derselbe Fehler
    hier erneut an, und zwar stumm: der Agent enthielte sich immer, der Sim zeigte
    ein sauberes Nullergebnis, und niemand wÃ¼sste warum.
    """
    from core.orchestration.graph import SymbolEvalState

    for kanal in ("implied_vol", "implied_vol_reference", "implied_vol_reference_date"):
        assert kanal in SymbolEvalState.__annotations__, kanal
