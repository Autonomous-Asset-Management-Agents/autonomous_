"""#4652 (Epic #4638, Sub-Issue 3) — der Rang-Trichter waehlt die Ranking-Quelle.

Mit ``LLM_RANKING_ENABLED`` und ``LLM_RANKING_QUELLE`` in {``llm``, ``llm_und_lstm``} liest
``_kontext_rang_trichter`` die gueltige Liste des letzten LLM-Laufs (``LaufSpeicher``) statt
oder neben dem LSTM-Panel. Rueckfallkaskade: LLM abgelaufen/keiner → LSTM-Panel wie heute →
Panel kalt/veraltet → ganzes Universum. ``apply_rank_cache_funnel`` bleibt unveraendert, die
Held-Invariante gilt also fuer jede Quelle. Flag aus → byte-identisch.
Plan: ``docs/4652-*/implementation_plan.md`` §5.1.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

JETZT = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
HEUTE = JETZT.date()
FRISCH_ALTER = timedelta(minutes=10)
UNIVERSUM = ["AAPL", "TSLA", "MSFT", "AMD", "NVDA"]
#: LSTM-Querschnitt: TSLA und AMD vorn — disjunkt zur LLM-Liste [AAPL, MSFT].
XSEC = {"TSLA": 0.9, "AMD": 0.8, "NVDA": 0.3, "AAPL": 0.2, "MSFT": 0.1}


def _mixin(speicher=None):
    from core.engine.trading_loop import TradingLoopMixin

    m = TradingLoopMixin.__new__(TradingLoopMixin)
    m._shutdown_event = threading.Event()
    m.api = MagicMock()
    m._rank_panel_producer = None
    m._llm_lauf_speicher = speicher
    return m


def _cfg(**werte):
    basis = {
        "SIM_MODE": False,
        "ROUND_TABLE_TOP_K_EVAL": 2,
        "ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS": 3,
        "ROUND_TABLE_RANK_REFRESH_MINUTES": 120,
        "LLM_RANKING_ENABLED": True,
        "LLM_RANKING_QUELLE": "llm",
        "LLM_RANKING_PROMPT": "Meine Idee",
    }
    basis.update(werte)
    return SimpleNamespace(**basis)


def _speicher(tmp_path, liste, alter=FRISCH_ALTER, ok=True):
    from core.engine.llm_ranking_lauf import LaufSpeicher, LlmRankingLauf

    speicher = LaufSpeicher(tmp_path / "laeufe.jsonl")
    if liste is not None:
        speicher.protokolliere(
            LlmRankingLauf(
                zeitpunkt_utc=JETZT - alter,
                prompt_hash="h",
                modell="m",
                anbieter="Stub",
                ok=ok,
                geliefert=list(liste),
            )
        )
    return speicher


def _zustand(held=("NVDA",), confirmed=True):
    from core.engine.zyklus import ZyklusZustand

    z = ZyklusZustand()
    z.current_time_utc = JETZT
    z.symbols_to_process = list(UNIVERSUM)
    z.symbols_order = list(UNIVERSUM)
    z.positions_by_symbol = {s: {"qty": 1.0} for s in held}
    z.position_confirmed = confirmed
    return z


def _store(latest=HEUTE):
    store = MagicMock()
    store.latest_snapshot_date.return_value = latest
    return store


def _trichter(m, z, cfg, store=None, xsec=None):
    """Laeuft den Rang-Trichter; ein faelliger LLM-Lauf wird hier nicht gestartet."""
    store = store if store is not None else _store()
    with patch("core.engine.trading_loop.get_config", return_value=cfg), patch(
        "core.report.lstm_panel_store.get_store", return_value=store
    ), patch(
        "core.report.lstm_panel_store.active_cross_section",
        return_value=dict(XSEC if xsec is None else xsec),
    ) as xsec_lesen, patch(
        "core.engine.zyklus_kontext._llm_lauf_ausloesen"
    ) as ausloesen:
        m._kontext_rang_trichter(z)
    return xsec_lesen, ausloesen


def test_llm_frisch_bestimmt_deep_eval(tmp_path):
    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"]))
    z = _zustand()
    xsec_lesen, _ = _trichter(m, z, _cfg())
    assert z.symbols_order == ["AAPL", "MSFT", "NVDA"]  # Universumsreihenfolge
    assert z.rang_quelle == "llm"
    assert z.llm_lauf_id == m._llm_lauf_speicher.letzter_erfolg().lauf_id
    xsec_lesen.assert_not_called()  # das LSTM-Panel wird bei frischer LLM-Liste nicht gelesen


def test_held_bleibt_bei_llm_quelle(tmp_path):
    m = _mixin(_speicher(tmp_path, ["AAPL"]))
    z = _zustand(held=("TSLA", "AMD"))
    _trichter(m, z, _cfg())
    assert z.symbols_order == ["AAPL", "TSLA", "AMD"]


def test_unbestaetigter_bestand_ganzes_universum(tmp_path):
    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"]))
    z = _zustand(confirmed=False)
    _trichter(m, z, _cfg())
    assert z.symbols_order == UNIVERSUM


def test_llm_abgelaufen_faellt_auf_lstm_mit_warning(tmp_path, caplog):
    # je_refresh bei 120 min → Hoechstdauer 240 min; 5 h alt ist abgelaufen.
    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"], alter=timedelta(hours=5)))
    z = _zustand()
    with caplog.at_level(logging.WARNING):
        _trichter(m, z, _cfg())
    assert z.symbols_order == ["TSLA", "AMD", "NVDA"]  # LSTM-Top-2 ∪ Bestand, wie heute
    assert z.rang_quelle == "lstm"
    assert z.llm_lauf_id is None
    assert any("[RankFunnel] LLM-Liste abgelaufen" in r.message for r in caplog.records)


def test_kein_llm_lauf_faellt_auf_lstm_mit_warning(tmp_path, caplog):
    m = _mixin(_speicher(tmp_path, None))
    z = _zustand()
    with caplog.at_level(logging.WARNING):
        _trichter(m, z, _cfg())
    assert z.symbols_order == ["TSLA", "AMD", "NVDA"]
    assert any("[RankFunnel] keine LLM-Liste" in r.message for r in caplog.records)


def test_alles_kalt_ganzes_universum(tmp_path, caplog):
    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"], alter=timedelta(hours=5)))
    z = _zustand()
    veraltet = _store(latest=(JETZT - timedelta(days=10)).date())
    with caplog.at_level(logging.WARNING):
        _trichter(m, z, _cfg(), store=veraltet)
    assert z.symbols_order == UNIVERSUM
    assert z.rang_quelle == "universum"
    assert any("LSTM panel stale" in r.message for r in caplog.records)


def test_llm_frisch_panel_veraltet_bleibt_llm(tmp_path):
    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"]))
    z = _zustand()
    veraltet = _store(latest=(JETZT - timedelta(days=10)).date())
    _trichter(m, z, _cfg(), store=veraltet)
    assert z.symbols_order == ["AAPL", "MSFT", "NVDA"]


def test_llm_und_lstm_vereinigung_gekappt(tmp_path):
    # top_k=3: LLM-Raenge zuerst (AAPL, MSFT), danach LSTM-Top ohne Doppelte (TSLA); AMD faellt.
    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"]))
    z = _zustand(held=())
    _trichter(m, z, _cfg(LLM_RANKING_QUELLE="llm_und_lstm", ROUND_TABLE_TOP_K_EVAL=3))
    assert z.symbols_order == ["AAPL", "TSLA", "MSFT"]
    assert z.rang_quelle == "llm_und_lstm"


def test_llm_und_lstm_ohne_panel_nur_llm(tmp_path):
    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"]))
    z = _zustand(held=())
    veraltet = _store(latest=None)
    _trichter(
        m,
        z,
        _cfg(LLM_RANKING_QUELLE="llm_und_lstm", ROUND_TABLE_TOP_K_EVAL=3),
        store=veraltet,
    )
    assert z.symbols_order == ["AAPL", "MSFT"]
    assert z.rang_quelle == "llm"


@pytest.mark.parametrize(
    "werte",
    [
        {"LLM_RANKING_ENABLED": False},
        {"LLM_RANKING_ENABLED": False, "LLM_RANKING_QUELLE": "llm"},
        {"LLM_RANKING_ENABLED": True, "LLM_RANKING_QUELLE": "lstm"},
        # #4861: die eigene Liste haengt am selben Rollback-Schalter
        {
            "LLM_RANKING_ENABLED": False,
            "LLM_RANKING_QUELLE": "liste",
            "LLM_RANKING_LISTE": "AAPL, MSFT",
        },
    ],
)
def test_flag_aus_byte_identisch(tmp_path, werte, caplog):
    """Flag aus (oder Quelle lstm): gleiche Reihenfolge, kein Lauf, keine neue Logzeile."""
    from core.engine.rank_funnel import apply_rank_cache_funnel

    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"]))
    z = _zustand()
    erwartet = apply_rank_cache_funnel(list(UNIVERSUM), {"NVDA"}, dict(XSEC), 2)
    with caplog.at_level(logging.DEBUG):
        _, ausloesen = _trichter(m, z, _cfg(**werte))
    assert z.symbols_order == erwartet == ["TSLA", "AMD", "NVDA"]
    assert z.rang_quelle is None and z.llm_lauf_id is None
    ausloesen.assert_not_called()
    assert not any("quelle=" in r.getMessage() for r in caplog.records)


def test_flag_aus_ohne_konfigfelder_byte_identisch():
    """Alte Konfigurationen ohne die neuen Felder (getattr-Default) bleiben unveraendert."""
    m = _mixin(None)
    z = _zustand()
    cfg = SimpleNamespace(
        ROUND_TABLE_TOP_K_EVAL=2, ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS=3
    )
    _trichter(m, z, cfg)
    assert z.symbols_order == ["TSLA", "AMD", "NVDA"]


def test_quelle_wird_je_zyklus_geloggt(tmp_path, caplog):
    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"]))
    z = _zustand()
    with caplog.at_level(logging.INFO):
        _trichter(m, z, _cfg())
    assert any(
        "[RankFunnel] quelle=llm status=frisch" in r.getMessage()
        for r in caplog.records
    )


def test_zweck_anteil_gueltiger_llm_vorschlaege(tmp_path):
    """Zweck-Kennzahl: Anteil gueltiger LLM-Vorschlaege im Deep-Eval-Satz (vorher 0 %)."""
    vorschlaege = ["AAPL", "MSFT", "AMD"]
    m = _mixin(_speicher(tmp_path, vorschlaege))
    z = _zustand()
    _trichter(m, z, _cfg(ROUND_TABLE_TOP_K_EVAL=60))
    im_satz = [s for s in vorschlaege if s in z.symbols_order]
    anteil = 100.0 * len(im_satz) / len(vorschlaege)
    print(f"Zweck-Kennzahl: {anteil:.0f} % gueltiger LLM-Vorschlaege im Deep-Eval-Satz")
    assert anteil == 100.0
    assert set(z.symbols_order) == set(vorschlaege) | {"NVDA"}


def test_rang_quelle_ist_benannter_schritt():
    from core.engine import zyklus_kontext

    assert callable(getattr(zyklus_kontext, "_rang_quelle_waehlen", None))


def test_lauf_ausloesen_wird_aufgerufen(tmp_path):
    m = _mixin(_speicher(tmp_path, ["AAPL", "MSFT"]))
    z = _zustand()
    _, ausloesen = _trichter(m, z, _cfg())
    ausloesen.assert_called_once()


# ── #4861: eigene S&P-500-Liste — ohne Ablauf, ohne Mischung, ohne LLM-Lauf ─────────────


def _liste_cfg(liste, **werte):
    return _cfg(LLM_RANKING_QUELLE="liste", LLM_RANKING_LISTE=liste, **werte)


def _speicher_verboten():
    """Ein Lauf-Speicher, dessen Zugriff den Test bricht: die Liste liest ihn nie."""
    speicher = MagicMock()
    speicher.gueltige_liste.side_effect = AssertionError("LaufSpeicher gelesen")
    speicher.letzter_erfolg.side_effect = AssertionError("LaufSpeicher gelesen")
    return speicher


def test_liste_bestimmt_deep_eval_in_listenreihenfolge():
    m = _mixin(_speicher_verboten())
    z = _zustand(held=())
    xsec_lesen, ausloesen = _trichter(
        m, z, _liste_cfg("NVDA, AAPL, MSFT", ROUND_TABLE_TOP_K_EVAL=5)
    )
    assert z.symbols_order == [
        "NVDA",
        "AAPL",
        "MSFT",
    ]  # Listen-, nicht Universumsreihenfolge
    assert z.rang_quelle == "liste"
    assert z.llm_lauf_id is None
    xsec_lesen.assert_not_called()  # kein LSTM-Auffuellen, obwohl die Liste kuerzer ist als K
    ausloesen.assert_not_called()  # kein LLM-Lauf
    zeitpunkt, rang, verworfen = m._liste_stand
    assert zeitpunkt == JETZT and rang == ["NVDA", "AAPL", "MSFT"] and verworfen == {}


def test_liste_gehaltene_positionen_bleiben():
    m = _mixin(_speicher_verboten())
    z = _zustand(held=("JPM",))
    z.symbols_to_process.append("JPM")
    z.symbols_order.append("JPM")
    _trichter(m, z, _liste_cfg("AAPL, MSFT"))
    assert z.symbols_order == ["AAPL", "MSFT", "JPM"]


def test_liste_laeuft_nicht_ab_und_veraltetes_panel_blockiert_nicht(caplog):
    m = _mixin(_speicher_verboten())
    z = _zustand(held=())
    z.current_time_utc = JETZT + timedelta(days=30)
    veraltet = _store(latest=(JETZT - timedelta(days=10)).date())
    with caplog.at_level(logging.INFO):
        _trichter(m, z, _liste_cfg("AAPL, MSFT"), store=veraltet)
    assert z.symbols_order == ["AAPL", "MSFT"]
    assert z.rang_quelle == "liste"
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert any(
        "[RankFunnel] quelle=liste status=dauerhaft" in r.getMessage()
        for r in caplog.records
    )


def test_liste_ohne_gueltigen_wert_im_zyklus_faellt_auf_lstm(caplog):
    m = _mixin(_speicher_verboten())
    m._liste_stand = (JETZT - timedelta(hours=1), ["AAPL"], {})  # Stand eines Vorzyklus
    z = _zustand()
    with caplog.at_level(logging.WARNING):
        _trichter(m, z, _liste_cfg("SAP, 12X"))
    assert z.symbols_order == ["TSLA", "AMD", "NVDA"]  # LSTM-Top-2 ∪ Bestand
    assert z.rang_quelle == "lstm"
    assert m._liste_stand is None
    assert any(
        "[RankFunnel] eigene Liste ohne gueltigen Wert" in r.getMessage()
        for r in caplog.records
    )
