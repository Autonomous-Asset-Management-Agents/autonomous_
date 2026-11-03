"""#3145 (bundle) — offline snapshot: build from a warm cache, seed a cold one."""

import gzip
import json
import logging
import os
import tempfile

import pytest

from core.report import financials_snapshot as snap


def _vollstaendige_zeile(ende: str = "2025-12-31") -> dict:
    """Eine Jahreszeile, die ``pruefe_abbild`` als vollstaendig durchgehen laesst.

    #4642: Die Tests zu diesem Ticket pruefen das Tor mit, nicht nur die Symbolmenge
    — sonst waere nicht belegt, dass es ohne die Sperrliste **gruen** wird.
    """
    return {
        "end_date": ende,
        "financials": {
            "income_statement": {
                "revenues": {"value": 1_000_000_000},
                "net_income_loss": {"value": 100_000_000},
            }
        },
    }


def _write(cache_dir, symbol, payload):
    with open(os.path.join(cache_dir, f"{symbol}.json"), "w", encoding="utf-8") as h:
        json.dump(payload, h)


def _payload(rev):
    return {
        "symbol": "X",
        "results": [
            {
                "filing_date": "2026-01-01",
                "financials": {"income_statement": {"revenues": {"value": rev}}},
            }
        ],
    }


def test_build_then_seed_roundtrip_gz():
    warm = tempfile.mkdtemp()
    _write(warm, "MSFT", _payload(100))
    _write(warm, "AAPL", _payload(200))
    out = os.path.join(tempfile.mkdtemp(), "snap.json.gz")
    assert snap.build_snapshot(warm, out) == 2
    # gzip-compressed + reloads
    with gzip.open(out, "rt", encoding="utf-8") as h:
        assert set(json.load(h)) == {"MSFT", "AAPL"}

    cold = tempfile.mkdtemp()
    assert snap.seed_cache_from_snapshot(out, cold) == 2
    with open(os.path.join(cold, "MSFT.json"), encoding="utf-8") as h:
        assert (
            json.load(h)["results"][0]["financials"]["income_statement"]["revenues"][
                "value"
            ]
            == 100
        )


def test_seed_is_noop_on_warm_cache():
    """A warm cache is never overwritten — a nightly refresh always beats the bundle."""
    out = os.path.join(tempfile.mkdtemp(), "snap.json")
    src = tempfile.mkdtemp()
    _write(src, "MSFT", _payload(100))
    snap.build_snapshot(src, out)

    warm = tempfile.mkdtemp()
    _write(warm, "MSFT", _payload(999))  # already present, different value
    assert snap.seed_cache_from_snapshot(out, warm) == 0
    with open(os.path.join(warm, "MSFT.json"), encoding="utf-8") as h:
        assert (
            json.load(h)["results"][0]["financials"]["income_statement"]["revenues"][
                "value"
            ]
            == 999
        )


def test_seed_missing_snapshot_is_zero():
    assert (
        snap.seed_cache_from_snapshot("/no/such/snapshot.json.gz", tempfile.mkdtemp())
        == 0
    )


# ---------------------------------------------------------------------------
# #4571 Schritt 3.2 — die Erstsaat wirkt JE SYMBOL, nicht alles-oder-nichts.
#
# Vorher brach `seed_cache_from_snapshot` ab, sobald EINE einzige `.json` im
# Zielverzeichnis lag. Mit 478 Dateien war die Saat damit ein dauerhafter Leerlauf:
# der Aufruf beim Engine-Start lief, tat aber seit dem 03.09.2026 nichts. Kein Update,
# kein Release und keine Neuinstallation ueber eine bestehende hinweg konnte den Inhalt
# je veraendern — gemessen 476 Dateien, alle vom 03.09.2026 14:20.
#
# Die Absicht der Sperre ("a nightly refresh is always fresher than the bundled
# snapshot") war richtig — nur gab es den naechtlichen Lauf nie. Damit war sie nicht
# der Schutz einer frischeren Quelle, sondern das Einfrieren der einzigen.
#
# Der gepinnte Vertrag "niemals ueberschreiben" bleibt unberuehrt: er gilt jetzt je
# Symbol statt je Verzeichnis. `test_seed_is_noop_on_warm_cache` oben prueft ihn an
# einem Abbild, dessen Symbolmenge in der warmen Ablage vollstaendig enthalten ist, und
# bleibt deshalb OHNE ANPASSUNG gruen.
# ---------------------------------------------------------------------------


@pytest.mark.vc1
def test_seed_fuellt_fehlende_symbole_auf_warmem_cache():
    """Der gemessene Fall: 15 bewertete Titel ohne Datei, bei 476 vorhandenen."""
    out = os.path.join(tempfile.mkdtemp(), "snap.json")
    src = tempfile.mkdtemp()
    _write(src, "MSFT", _payload(100))
    _write(src, "AXON", _payload(200))  # einer der 15 gemessenen Faelle
    snap.build_snapshot(src, out)

    warm = tempfile.mkdtemp()
    _write(warm, "MSFT", _payload(999))  # vorhanden, anderer Wert

    assert snap.seed_cache_from_snapshot(out, warm) == 1, "nur das fehlende Symbol"
    assert os.path.exists(os.path.join(warm, "AXON.json")), "AXON wurde nicht gesaet"


@pytest.mark.vc1
def test_seed_laesst_vorhandene_datei_byte_gleich():
    """Nicht nur inhaltlich gleich — byte-gleich. Sonst waere jede Saat ein Schreibzugriff
    auf 476 Dateien und der Vertrag nur noch scheinbar erfuellt."""
    out = os.path.join(tempfile.mkdtemp(), "snap.json")
    src = tempfile.mkdtemp()
    _write(src, "MSFT", _payload(100))
    _write(src, "AXON", _payload(200))
    snap.build_snapshot(src, out)

    warm = tempfile.mkdtemp()
    _write(warm, "MSFT", _payload(999))
    pfad = os.path.join(warm, "MSFT.json")
    vorher = open(pfad, "rb").read()
    mtime_vorher = os.stat(pfad).st_mtime_ns

    snap.seed_cache_from_snapshot(out, warm)

    assert open(pfad, "rb").read() == vorher, "die vorhandene Datei wurde angefasst"
    assert os.stat(pfad).st_mtime_ns == mtime_vorher, "sie wurde neu geschrieben"


@pytest.mark.vc1
def test_4642_fremde_datei_wird_kein_titel():
    """#4642: build_snapshot nimmt den Dateinamen als Symbol — jede *.json wird ein Titel.

    Die Rueckkauf-Sperrliste (#3604) liegt im selben Verzeichnis, weil
    ``DEFAULT_CACHE_DIR`` der einzige upgrade-feste Pfad ist. Gemessen am 21.10.2026 an
    der Live-Ablage meldete ``pruefe_abbild`` daraufhin einen Titel ``REENTRY_LOCKOUT``
    ohne Umsatz — und blieb damit dauerhaft rot.
    """
    cache = tempfile.mkdtemp()
    for sym in ("AAPL", "MSFT"):
        with open(os.path.join(cache, f"{sym}.json"), "w", encoding="utf-8") as h:
            json.dump({"results": [_vollstaendige_zeile()]}, h)
    with open(os.path.join(cache, "reentry_lockout.json"), "w", encoding="utf-8") as h:
        json.dump({"LOW": {"until": "2026-09-30"}}, h)

    out = os.path.join(tempfile.mkdtemp(), "abbild.json.gz")
    anzahl = snap.build_snapshot(cache, out)

    abbild = snap.load_snapshot(out)
    assert set(abbild) == {
        "AAPL",
        "MSFT",
    }, f"Fremdschluessel im Abbild: {sorted(abbild)}"
    assert anzahl == 2, f"Symbolzahl {anzahl} zaehlt die Fremddatei mit"

    # Plan §4, Szenario 4: Das Tor ist GRUEN, obwohl die Sperrliste im Verzeichnis
    # liegt. Vor dem Filter meldete es ``REENTRY_LOCKOUT`` als Titel ohne Umsatz.
    befunde = snap.pruefe_abbild(abbild, min_symbole=2)
    assert befunde == [], f"Tor nicht gruen: {befunde}"


@pytest.mark.vc1
def test_4642_seed_ignoriert_fremde_schluessel(caplog):
    """#4642: Die Erstsaat schreibt je Schluessel eine Datei — auch fuer Fremdschluessel.

    Ein Abbild, das je einmal aus einer Live-Ablage gebaut wurde, traegt
    ``REENTRY_LOCKOUT``; die Saat wuerde daraus ``REENTRY_LOCKOUT.json`` in **jeder
    Neuinstallation** anlegen — Handelszustand einer fremden Maschine als
    Produktinhalt. Das ausgelieferte Abbild ist heute sauber (500 Schluessel, alle
    Titel, geprueft am 22.10.2026) — aber nichts prueft das.
    """
    quelle = os.path.join(tempfile.mkdtemp(), "abbild.json.gz")
    with gzip.open(quelle, "wt", encoding="utf-8") as h:
        json.dump(
            {
                "AAPL": {"results": [_vollstaendige_zeile()]},
                "REENTRY_LOCKOUT": {"LOW": {"until": "2026-09-30"}},
            },
            h,
        )

    ziel = tempfile.mkdtemp()
    with caplog.at_level(logging.DEBUG):
        gesaet = snap.seed_cache_from_snapshot(quelle, ziel)

    assert os.path.exists(os.path.join(ziel, "AAPL.json"))
    assert not os.path.exists(
        os.path.join(ziel, "REENTRY_LOCKOUT.json")
    ), "Fremdschluessel als Titeldatei gesaet"
    assert gesaet == 1, f"gesaet={gesaet} zaehlt den Fremdschluessel mit"

    # Plan §3.4: die Zaehlung muss gemeldet werden — ein stiller Filter waere ein
    # zweiter blinder Fleck. Im AUSGELIEFERTEN Abbild ist ein Fremdschluessel eine
    # Auffaelligkeit, deshalb WARNING und nicht DEBUG.
    laut = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and "non-symbol keys" in r.getMessage()
    ]
    assert laut, (
        "keine WARNING ueber den Fremdschluessel: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    assert "1 non-symbol keys" in laut[0], laut[0]
