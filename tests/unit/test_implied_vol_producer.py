# tests/unit/test_implied_vol_producer.py
# TRD-10 (#3038) — der Erzeuger, der die implizite Volatilität besorgt (TDD Red→Green).
#
# #3044 hat den Agenten und die drei State-Kanäle gebaut, aber niemanden, der sie
# füllt. Das hier ist die fehlende Hälfte: Tages-Cache, Vortagsreferenz, gefilterter
# Kettenabruf, Enthaltung bei Ausfall.
#
# Gemessen (RESULTS_ABRUFKOSTEN.md, 25.08.2026): ein serverseitig gefilterter Abruf
# kostet 0,15 s im Median — 4,5 s für 30 Kandidaten, 0,5 % eines 15-Minuten-Zyklus.
# Alpaca liefert `implied_volatility` fertig mit; keine Black-Scholes-Inversion.
#
# Der Erzeuger folgt dem Hausmuster #1949 (`_regime_conditioner_state_keys`):
# Flag AUS ⇒ alle Kanäle None UND kein Netzverkehr.

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest

HEUTE = dt.date(2026, 6, 26)
VORTAG = dt.date(2026, 6, 25)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


class _Snap:
    """Ein Kettenglied wie Alpaca es liefert — IV fertig, keine Inversion nötig."""

    def __init__(self, iv):
        self.implied_volatility = iv


def _kette(spot: float, ivs: dict) -> dict:
    """OCC-Schlüssel → Snapshot. Der Strike steckt in den letzten 8 Stellen × 1000."""
    return {
        f"AAPL260717C{int(round(k * 1000)):08d}": _Snap(iv) for k, iv in ivs.items()
    }


class _Client:
    """Zählt Abrufe — der Tages-Cache ist nur belegt, wenn der Zähler stehen bleibt."""

    def __init__(self, kette=None, fehler=None):
        self.rufe = []
        self._kette = kette or {}
        self._fehler = fehler

    def get_option_chain(self, request):
        self.rufe.append(request)
        if self._fehler:
            raise self._fehler
        return self._kette


def _arm(monkeypatch, enabled: bool):
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(VIXAWARE_IMPLIED_VOL_ENABLED=enabled),
    )


@pytest.fixture()
def store(tmp_path):
    from core.implied_vol import ImpliedVolStore

    return ImpliedVolStore(tmp_path / "implied_vol.json")


# ---------------------------------------------------------------------------
# Flag AUS — kein Kanal, kein Netzverkehr
# ---------------------------------------------------------------------------


def test_flag_aus_liefert_alle_kanaele_none(monkeypatch, store):
    from core.implied_vol import implied_vol_state_keys

    _arm(monkeypatch, False)
    cl = _Client(_kette(100.0, {100.0: 0.30}))
    keys = implied_vol_state_keys("AAPL", 100.0, HEUTE, store=store, client=cl)
    assert keys == {
        "implied_vol": None,
        "implied_vol_reference": None,
        "implied_vol_reference_date": None,
    }


def test_flag_aus_macht_keinen_netzverkehr(monkeypatch, store):
    """Der teuerste Fehler wäre ein Erzeuger, der bei AUS trotzdem abruft.

    Bei 30 Kandidaten und 26 Zyklen je Tag wären das 780 Abrufe für nichts —
    und der Zustand wäre nicht mehr byte-identisch zum Ist.
    """
    from core.implied_vol import implied_vol_state_keys

    _arm(monkeypatch, False)
    cl = _Client(_kette(100.0, {100.0: 0.30}))
    implied_vol_state_keys("AAPL", 100.0, HEUTE, store=store, client=cl)
    assert cl.rufe == []


def test_flag_aus_baut_keinen_client(monkeypatch, store):
    """Auch die Client-Erzeugung muss ausbleiben — sie liest Schlüssel aus dem
    Keychain und kostet einen Handshake."""
    import core.implied_vol as iv_mod

    _arm(monkeypatch, False)
    gerufen = []
    monkeypatch.setattr(
        iv_mod, "_default_client", lambda: gerufen.append(1) or _Client()
    )
    iv_mod.implied_vol_state_keys("AAPL", 100.0, HEUTE, store=store)
    assert gerufen == []


# ---------------------------------------------------------------------------
# Der Abruf selbst
# ---------------------------------------------------------------------------


def test_kette_wird_serverseitig_gefiltert(monkeypatch, store):
    """Ungefiltert kostet der Abruf das Siebenfache (1,08 s gegen 0,15 s gemessen).

    Geprüft wird, dass Verfall- UND Strike-Grenzen mitgehen — ohne Strike-Grenzen
    kommen 1.028 bis 6.663 Kontrakte zurück statt 20 bis 82.
    """
    from core.implied_vol import fetch_atm30_iv

    cl = _Client(_kette(100.0, {100.0: 0.30}))
    fetch_atm30_iv("AAPL", 100.0, HEUTE, client=cl)
    r = cl.rufe[0]
    assert r.expiration_date_gte == HEUTE + dt.timedelta(days=20)
    assert r.expiration_date_lte == HEUTE + dt.timedelta(days=45)
    assert r.strike_price_gte == pytest.approx(95.0)
    assert r.strike_price_lte == pytest.approx(105.0)


def test_iv_ist_der_median_im_atm_band(monkeypatch, store):
    """Median, nicht Mittelwert: ein einzelner Ausreißer in der Kette (Fehlquote,
    illiquider Strike) verschöbe einen Mittelwert und damit den Rang des Titels."""
    from core.implied_vol import fetch_atm30_iv

    cl = _Client(_kette(100.0, {98.0: 0.28, 100.0: 0.30, 102.0: 0.32, 101.0: 9.99}))
    # 9.99 liegt außerhalb des plausiblen Bandes und wird verworfen, nicht gemittelt.
    assert fetch_atm30_iv("AAPL", 100.0, HEUTE, client=cl) == pytest.approx(0.30)


def test_strikes_ausserhalb_des_bands_zaehlen_nicht(monkeypatch, store):
    """Die serverseitige Filterung ist nicht garantiert exakt — clientseitig nochmal.

    Ein Strike 40 % neben dem Kurs trägt eine ganz andere IV (Volatilitäts-Smile);
    ihn mitzurechnen hieße, Titel über unterschiedliche Punkte der Kurve zu
    vergleichen.
    """
    from core.implied_vol import fetch_atm30_iv

    cl = _Client(_kette(100.0, {100.0: 0.30, 140.0: 0.80}))
    assert fetch_atm30_iv("AAPL", 100.0, HEUTE, client=cl) == pytest.approx(0.30)


@pytest.mark.parametrize("kette", [{}, {100.0: None}, {100.0: 0.0}, {100.0: 7.0}])
def test_unbrauchbare_kette_gibt_none(monkeypatch, store, kette):
    from core.implied_vol import fetch_atm30_iv

    cl = _Client(_kette(100.0, kette) if kette else {})
    assert fetch_atm30_iv("AAPL", 100.0, HEUTE, client=cl) is None


def test_abrufFehler_wird_nicht_weitergeworfen(monkeypatch, store):
    """Eine externe Abhängigkeit im Handelspfad darf den Zyklus nicht abbrechen.

    Plan §9.5: Fällt die Optionskette aus, enthält sich der Agent — bewusst der
    konservative Weg, keine Stimme statt einer geratenen.
    """
    from core.implied_vol import fetch_atm30_iv

    cl = _Client(fehler=RuntimeError("Alpaca down"))
    assert fetch_atm30_iv("AAPL", 100.0, HEUTE, client=cl) is None


def test_ohne_kurs_kein_abruf(monkeypatch, store):
    """Ohne Kurs gibt es kein ATM-Band — ein ungefilterter Abruf wäre das
    Siebenfache und das Ergebnis trotzdem unbrauchbar."""
    from core.implied_vol import fetch_atm30_iv

    cl = _Client(_kette(100.0, {100.0: 0.30}))
    assert fetch_atm30_iv("AAPL", 0.0, HEUTE, client=cl) is None
    assert cl.rufe == []


# ---------------------------------------------------------------------------
# Tages-Cache
# ---------------------------------------------------------------------------


def test_zweiter_zyklus_ruft_nicht_erneut_ab(monkeypatch, store):
    """26 Zyklen je Handelstag ⇒ ohne Cache 780 statt 30 Abrufe.

    Der Cache ist nach der Messung keine Notwendigkeit für die Zykluszeit (0,5 %),
    aber eine für die Rate-Limits — und fachlich richtig, weil die Referenz aus dem
    Vortag stammt: ein über den Tag wandernder Wert gegen eine feste Referenz ließe
    den Rang eines Titels schwanken, ohne dass sich die Vergleichsbasis bewegt.
    """
    from core.implied_vol import implied_vol_state_keys

    _arm(monkeypatch, True)
    cl = _Client(_kette(100.0, {100.0: 0.30}))
    a = implied_vol_state_keys("AAPL", 100.0, HEUTE, store=store, client=cl)
    b = implied_vol_state_keys("AAPL", 100.0, HEUTE, store=store, client=cl)
    assert len(cl.rufe) == 1
    assert a["implied_vol"] == b["implied_vol"] == pytest.approx(0.30)


def test_neuer_tag_ruft_wieder_ab(monkeypatch, store):
    from core.implied_vol import implied_vol_state_keys

    _arm(monkeypatch, True)
    cl = _Client(_kette(100.0, {100.0: 0.30}))
    implied_vol_state_keys("AAPL", 100.0, VORTAG, store=store, client=cl)
    implied_vol_state_keys("AAPL", 100.0, HEUTE, store=store, client=cl)
    assert len(cl.rufe) == 2


def test_cache_ueberlebt_den_neustart(monkeypatch, tmp_path):
    """Die Engine startet täglich neu. Ein Cache nur im Speicher wäre bei jedem
    Neustart leer — und die Vortagsreferenz gäbe es nie."""
    from core.implied_vol import ImpliedVolStore, implied_vol_state_keys

    _arm(monkeypatch, True)
    pfad = tmp_path / "iv.json"
    cl = _Client(_kette(100.0, {100.0: 0.30}))
    implied_vol_state_keys("AAPL", 100.0, HEUTE, store=ImpliedVolStore(pfad), client=cl)
    implied_vol_state_keys("AAPL", 100.0, HEUTE, store=ImpliedVolStore(pfad), client=cl)
    assert len(cl.rufe) == 1


def test_beschaedigte_cachedatei_bricht_nicht(monkeypatch, tmp_path):
    """Eine halb geschriebene Datei (Stromausfall im Zyklus) darf den Boot nicht
    verhindern — schlimmstenfalls fehlt die Referenz und der Agent enthält sich."""
    from core.implied_vol import ImpliedVolStore

    pfad = tmp_path / "iv.json"
    pfad.write_text('{"2026-06-25": {"AAPL": 0.3', encoding="utf-8")
    s = ImpliedVolStore(pfad)
    assert s.reference(HEUTE) == (None, None)


# ---------------------------------------------------------------------------
# Vortagsreferenz — AC-7, kein Vorgriff
# ---------------------------------------------------------------------------


def test_referenz_ist_der_vortag_nicht_heute(monkeypatch, store):
    """AC-7. Nähme der Erzeuger die Werte des laufenden Tages, würde ein Titel
    gegen eine Verteilung bewertet, die ihn selbst schon enthält — der Sim zeigte
    dann einen Vorteil, den es live nicht gibt."""
    from core.implied_vol import implied_vol_state_keys

    _arm(monkeypatch, True)
    for sym, iv in (("AAPL", 0.20), ("MSFT", 0.30), ("NVDA", 0.40)):
        implied_vol_state_keys(
            sym, 100.0, VORTAG, store=store, client=_Client(_kette(100.0, {100.0: iv}))
        )
    keys = implied_vol_state_keys(
        "TSLA", 100.0, HEUTE, store=store, client=_Client(_kette(100.0, {100.0: 0.99}))
    )
    assert sorted(keys["implied_vol_reference"]) == [0.20, 0.30, 0.40]
    assert 0.99 not in keys["implied_vol_reference"]
    assert keys["implied_vol_reference_date"] == "2026-06-25"


def test_erster_tag_hat_keine_referenz(monkeypatch, store):
    """AC-8: keine Vortagsverteilung ⇒ der Agent enthält sich, statt zu raten."""
    from core.implied_vol import implied_vol_state_keys

    _arm(monkeypatch, True)
    keys = implied_vol_state_keys(
        "AAPL", 100.0, HEUTE, store=store, client=_Client(_kette(100.0, {100.0: 0.30}))
    )
    assert keys["implied_vol"] == pytest.approx(0.30)
    assert keys["implied_vol_reference"] is None
    assert keys["implied_vol_reference_date"] is None


def test_referenz_nimmt_den_letzten_verfuegbaren_tag(monkeypatch, store):
    """Wochenenden und Feiertage: der Vortag ist nicht immer gestern.

    Ein starrer „heute minus ein Tag" hätte montags nie eine Referenz — der Agent
    enthielte sich an einem von fünf Handelstagen.
    """
    from core.implied_vol import implied_vol_state_keys

    _arm(monkeypatch, True)
    freitag = dt.date(2026, 6, 19)
    for sym, iv in (("AAPL", 0.21), ("MSFT", 0.31)):
        implied_vol_state_keys(
            sym, 100.0, freitag, store=store, client=_Client(_kette(100.0, {100.0: iv}))
        )
    montag = dt.date(2026, 6, 22)
    keys = implied_vol_state_keys(
        "NVDA", 100.0, montag, store=store, client=_Client(_kette(100.0, {100.0: 0.40}))
    )
    assert keys["implied_vol_reference_date"] == "2026-06-19"
    assert sorted(keys["implied_vol_reference"]) == [0.21, 0.31]


def test_zu_kleine_referenz_wird_nicht_geliefert(monkeypatch, store):
    """Ein einzelner Vortagswert ist keine Verteilung — daraus ließe sich kein Rang
    bilden, nur ein Münzwurf mit Nachkommastellen (AC-8)."""
    from core.implied_vol import implied_vol_state_keys

    _arm(monkeypatch, True)
    implied_vol_state_keys(
        "AAPL", 100.0, VORTAG, store=store, client=_Client(_kette(100.0, {100.0: 0.30}))
    )
    keys = implied_vol_state_keys(
        "MSFT", 100.0, HEUTE, store=store, client=_Client(_kette(100.0, {100.0: 0.35}))
    )
    assert keys["implied_vol_reference"] is None


def test_alte_tage_werden_aufgeraeumt(monkeypatch, store):
    """Ohne Begrenzung wüchse die Datei unbegrenzt. Gebraucht wird der Vortag."""
    from core.implied_vol import implied_vol_state_keys

    _arm(monkeypatch, True)
    for i in range(12):
        tag = dt.date(2026, 6, 1) + dt.timedelta(days=i)
        for sym in ("AAPL", "MSFT"):
            implied_vol_state_keys(
                sym,
                100.0,
                tag,
                store=store,
                client=_Client(_kette(100.0, {100.0: 0.30})),
            )
    daten = json.loads(store.path.read_text(encoding="utf-8"))
    assert len(daten) <= 5, daten.keys()


# ---------------------------------------------------------------------------
# Verdrahtung im Handelszyklus
# ---------------------------------------------------------------------------


def test_trading_loop_hat_den_erzeuger(monkeypatch):
    """Ohne Aufruf im Zyklus bleiben die Kanäle leer, egal wie gut der Erzeuger ist —
    und genau das wäre im Sim ein sauber aussehendes Nullergebnis."""
    import core.engine.trading_loop as tl

    assert hasattr(tl, "_implied_vol_state_keys")


def test_trading_loop_erzeuger_ist_flag_zuerst(monkeypatch):
    """Hausmuster #1949: AUS ⇒ alle Kanäle None, ohne Netz-I/O."""
    import core.engine.trading_loop as tl

    _arm(monkeypatch, False)
    keys = tl._implied_vol_state_keys("AAPL", 100.0)
    assert keys == {
        "implied_vol": None,
        "implied_vol_reference": None,
        "implied_vol_reference_date": None,
    }


# ---------------------------------------------------------------------------
# Vorbefüllung — und ihre Kennzeichnung im Audit-Trail
# ---------------------------------------------------------------------------
#
# Beim Einschalten im Betrieb gibt es am ersten Tag keine Vortagsverteilung; der
# Agent enthielte sich auf allen Titeln. Das ist kein neutraler Warmlauf: 19,6 %
# der geloggten Käufe liegen nur deshalb über der Schwelle, weil VIXAware
# mitstimmt. Ein Tag ohne diese Stimme ist ein Tag mit deutlich anderem Verhalten.
#
# Die Vorbefüllung legt deshalb HEUTIGE Werte unter dem Datum des letzten
# Handelstages ab. Das ist eine Krücke, und sie MUSS als solche erkennbar sein:
# das Reasoning eines Agenten landet im Entscheidungslog (MiFID II). Stünde dort
# ein Datum, an dem diese Werte nie galten, behauptete der Audit-Trail etwas
# Falsches.


def test_vorbefuellter_tag_wird_als_referenz_genutzt(store):
    store.put_seeded_day(VORTAG, {"AAPL": 0.20, "MSFT": 0.30, "NVDA": 0.40})
    werte, datum = store.reference(HEUTE)
    assert sorted(werte) == [0.20, 0.30, 0.40]


def test_vorbefuellung_ist_im_referenzdatum_gekennzeichnet(store):
    """Ohne Kennzeichnung behauptet das Entscheidungslog ein Datum, an dem diese
    Werte nie galten."""
    store.put_seeded_day(VORTAG, {"AAPL": 0.20, "MSFT": 0.30})
    _, datum = store.reference(HEUTE)
    assert "vorbefüllt" in datum.lower()
    assert VORTAG.isoformat() in datum


@pytest.mark.anyio
async def test_kennzeichnung_erreicht_das_reasoning(monkeypatch, store):
    """Der Vermerk muss bis in den Text kommen, der geloggt wird — nicht nur in
    den Rückgabewert."""
    from core.round_table.agents import VIXAwareRiskAgent

    store.put_seeded_day(VORTAG, {"AAPL": 0.20, "MSFT": 0.30, "NVDA": 0.40})
    _arm(monkeypatch, True)
    import config
    import core.implied_vol as iv_mod

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(VIXAWARE_IMPLIED_VOL_ENABLED=True, SIM_MODE=False),
    )
    keys = iv_mod.implied_vol_state_keys(
        "AAPL", 100.0, HEUTE, store=store, client=_Client(_kette(100.0, {100.0: 0.25}))
    )
    v = await VIXAwareRiskAgent().vote(
        {"symbol": "AAPL", "ohlc": {}, "vix": 18.0, **keys}
    )
    assert "vorbefüllt" in v.reasoning.lower()


def test_echter_tag_traegt_keinen_vermerk(store):
    """Die Kennzeichnung darf nur dort stehen, wo sie zutrifft — sonst wäre sie
    ein Rauschen, das man nach zwei Tagen ignoriert."""
    store.put("AAPL", VORTAG, 0.20)
    store.put("MSFT", VORTAG, 0.30)
    _, datum = store.reference(HEUTE)
    assert datum == VORTAG.isoformat()


def test_echter_tag_verdraengt_die_vorbefuellung(store):
    """Sobald ein echter Handelstag vorliegt, ist er die bessere Referenz — auch
    wenn die Vorbefüllung jünger wäre."""
    store.put_seeded_day(dt.date(2026, 6, 20), {"AAPL": 0.90, "MSFT": 0.95})
    store.put("AAPL", VORTAG, 0.20)
    store.put("MSFT", VORTAG, 0.30)
    werte, datum = store.reference(HEUTE)
    assert datum == VORTAG.isoformat()
    assert sorted(werte) == [0.20, 0.30]


def test_marker_zaehlt_nicht_als_iv_wert(store):
    """Der Marker liegt in derselben Datei. Landete er in der Verteilung, würde
    gegen einen erfundenen Wert gerankt."""
    store.put_seeded_day(VORTAG, {"AAPL": 0.20, "MSFT": 0.30})
    werte, _ = store.reference(HEUTE)
    assert len(werte) == 2
    assert all(isinstance(w, float) for w in werte)


# ---------------------------------------------------------------------------
# Sim-Weiche — der Sim darf NIE die Live-Kette abrufen
# ---------------------------------------------------------------------------
#
# Der Sim bootet die echte Engine, also läuft der Erzeuger dort mit. Ohne eigene
# Quelle würde er die Optionskette von HEUTE holen, während der Sim einen Tag im
# Januar nachspielt — ein Vorgriff auf die Zukunft, der jedes Ergebnis wertlos
# macht und dabei besser aussähe als die Wirklichkeit.


def _sim_an(monkeypatch, korpus, tag):
    import config
    import core.implied_vol as iv_mod

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            VIXAWARE_IMPLIED_VOL_ENABLED=True,
            SIM_MODE=True,
            SIM_CORPUS_DIR=str(korpus),
        ),
    )
    monkeypatch.setattr(iv_mod, "_sim_heute", lambda: tag)
    iv_mod._SIM_TABELLE = None  # Prozess-Cache je Test leeren


@pytest.fixture()
def korpus(tmp_path):
    """Minimaler Korpus mit IV-Reihe, wie research/build_iv_history.py sie schreibt."""
    pd = pytest.importorskip("pandas")

    d = tmp_path / "korpus" / "iv"
    d.mkdir(parents=True)
    zeilen = []
    for tag, werte in (
        (VORTAG, {"AAPL": 0.20, "MSFT": 0.30, "NVDA": 0.40}),
        (HEUTE, {"AAPL": 0.22, "MSFT": 0.33, "NVDA": 0.44}),
    ):
        for sym, iv in werte.items():
            zeilen.append({"sym": sym, "tag": pd.Timestamp(tag), "iv": iv})
    pd.DataFrame(zeilen).to_parquet(d / "atm30_implied_vol.parquet")
    return tmp_path / "korpus"


def test_sim_liest_den_korpus_und_ruft_nicht_ab(monkeypatch, korpus):
    import core.implied_vol as iv_mod

    _sim_an(monkeypatch, korpus, HEUTE)
    cl = _Client(_kette(100.0, {100.0: 0.99}))
    keys = iv_mod.implied_vol_state_keys("AAPL", 100.0, client=cl)
    assert keys["implied_vol"] == pytest.approx(0.22)
    assert cl.rufe == [], "der Sim darf die Live-Kette NICHT anfassen"


def test_sim_referenz_ist_der_vortag_aus_dem_korpus(monkeypatch, korpus):
    import core.implied_vol as iv_mod

    _sim_an(monkeypatch, korpus, HEUTE)
    keys = iv_mod.implied_vol_state_keys("AAPL", 100.0)
    assert sorted(keys["implied_vol_reference"]) == [0.20, 0.30, 0.40]
    assert keys["implied_vol_reference_date"] == "2026-06-25"


def test_sim_erster_korpustag_hat_keine_referenz(monkeypatch, korpus):
    """Der Korpus beginnt vor den Optionsdaten — rund drei Monate tragen keine IV.
    Dort muss der Agent sich enthalten, statt gegen nichts zu ranken."""
    import core.implied_vol as iv_mod

    _sim_an(monkeypatch, korpus, VORTAG)
    keys = iv_mod.implied_vol_state_keys("AAPL", 100.0)
    assert keys["implied_vol"] == pytest.approx(0.20)
    assert keys["implied_vol_reference"] is None


def test_sim_unbekannter_titel_enthaelt_sich(monkeypatch, korpus):
    import core.implied_vol as iv_mod

    _sim_an(monkeypatch, korpus, HEUTE)
    keys = iv_mod.implied_vol_state_keys("TSLA", 100.0)
    assert keys["implied_vol"] is None
    assert keys["implied_vol_reference"] is not None


def test_sim_ohne_korpusdatei_enthaelt_sich(monkeypatch, tmp_path):
    """Wer den Sweep fährt, muss die IV-Reihe vorher bauen. Fehlt sie, ist das
    Ergebnis eine Reihe von Enthaltungen — und das muss im Log stehen, nicht als
    sauber aussehendes Nullergebnis durchgehen."""
    import core.implied_vol as iv_mod

    _sim_an(monkeypatch, tmp_path / "leer", HEUTE)
    keys = iv_mod.implied_vol_state_keys("AAPL", 100.0)
    assert keys == {
        "implied_vol": None,
        "implied_vol_reference": None,
        "implied_vol_reference_date": None,
    }
