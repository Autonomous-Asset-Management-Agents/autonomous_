"""#3430 — die Abgleich-Sperre lässt sich sehen und aufheben (Plan: docs/3430-bedienstelle-abgleichsperre).

Bisher war die Sperre aus #3389 „ein Notausgang ohne Klinke": ``release_block`` hatte außer einem
Test keinen Aufrufer. Plan-Entscheide:

1. **Ein Engine-Endpunkt** für Konsole und Desktop — eine Prüfung, ein Protokolleintrag.
2. **Der Urheber kommt aus dem Auth-Kontext**, nicht vom Aufrufer. Ist die Kennung nicht signiert
   (Desktop, ``LocalMockAuth``), heißt er so, wie er ist.
3. **Die Anzeige zeigt den vollständigen Vergleich** — beide Seiten jeder Abweichung.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit

_KEY = "test-engine-key-3430"


def _abgleich(gesperrt=True):
    from core.contracts import ReconciliationBreak, ReconciliationRecord
    from core.reconciliation import ReconciliationService

    dienst = ReconciliationService(api=None, redis_client=None, block_on_break=True)
    dienst.reconciled_once = True
    jetzt = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    befund = ReconciliationRecord(
        run_id="lauf-1",
        started_at=jetzt,
        finished_at=jetzt,
        breaks=(
            ReconciliationBreak(
                kind="position_mismatch",
                symbol="AAPL",
                broker_side="qty=10",
                engine_side="qty=7",
                detail="Broker haelt 3 Stueck mehr",
            ),
        ),
    )
    if gesperrt:
        dienst.last_record = befund
        dienst._report(befund)
    return dienst


@pytest.fixture
def api(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import core.engine.api_routes as ar

    monkeypatch.setenv("ENGINE_API_KEY", _KEY)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("REQUIRE_SIG", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    dienst = _abgleich()
    monkeypatch.setattr(ar, "engine", SimpleNamespace(reconciler=dienst))
    return ar, dienst, tmp_path


def _client(ar):
    from fastapi.testclient import TestClient

    return TestClient(ar.app)


def _kopf(**mehr):
    return {"X-Engine-Key": _KEY, **mehr}


# ---------------------------------------------------------------------------
# Die Sperre ist sichtbar
# ---------------------------------------------------------------------------


def test_die_sperre_ist_mit_beiden_seiten_des_vergleichs_sichtbar(api) -> None:
    ar, _, _ = api
    antwort = _client(ar).get("/api/reconciliation/block", headers=_kopf())

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["entries_blocked"] is True
    abweichung = daten["breaks"][0]
    assert (
        abweichung["symbol"],
        abweichung["kind"],
        abweichung["broker_side"],
        abweichung["engine_side"],
    ) == ("AAPL", "position_mismatch", "qty=10", "qty=7")


def test_ohne_schluessel_keine_auskunft(api) -> None:
    ar, _, _ = api
    assert _client(ar).get("/api/reconciliation/block").status_code in (401, 403)


def test_ohne_abgleich_ehrliche_antwort(api, monkeypatch) -> None:
    """Laeuft kein Abgleich, gibt es keine Sperre — gesagt wird es trotzdem."""
    ar, _, _ = api
    monkeypatch.setattr(ar, "engine", None)
    daten = _client(ar).get("/api/reconciliation/block", headers=_kopf()).json()

    assert daten["entries_blocked"] is False
    assert daten["available"] is False


# ---------------------------------------------------------------------------
# Ein Mensch hebt die Sperre auf
# ---------------------------------------------------------------------------


def test_ein_mensch_hebt_die_sperre_auf(api) -> None:
    ar, dienst, _ = api
    antwort = _client(ar).post(
        "/api/reconciliation/release",
        headers=_kopf(),
        json={"reason": "Broker-Bestand von Hand geprueft"},
    )

    assert antwort.status_code == 200
    assert dienst.entries_blocked is False
    assert dienst.blocks("entry") is False


def test_die_aufhebung_ist_mit_zeitpunkt_und_urheber_festgehalten(api) -> None:
    """Dauerhaft, unter USER_DATA_DIR — wie das Kill-Switch-Protokoll."""
    ar, _, ablage = api
    _client(ar).post(
        "/api/reconciliation/release",
        headers=_kopf(),
        json={"reason": "Broker-Bestand von Hand geprueft"},
    )

    zeilen = (
        (ablage / "reconciliation_audit.log").read_text(encoding="utf-8").splitlines()
    )
    satz = json.loads(zeilen[-1])
    assert satz["event"] == "release"
    assert satz["ts"]
    assert satz["by"]
    assert satz["reason"] == "Broker-Bestand von Hand geprueft"
    assert (
        satz["breaks"][0]["symbol"] == "AAPL"
    ), "Festgehalten wird auch, WAS freigegeben wurde — nicht nur, dass."


def test_ohne_signatur_heisst_der_urheber_wie_er_ist(api) -> None:
    """Desktop: LocalMockAuth, keine signierte Kennung. Der Protokolleintrag darf keine
    Person vortaeuschen — auch nicht die, die der Aufrufer im Kopf behauptet."""
    ar, _, ablage = api
    _client(ar).post(
        "/api/reconciliation/release",
        headers=_kopf(**{"X-User-Id": "chefin"}),
        json={},
    )

    satz = json.loads(
        (ablage / "reconciliation_audit.log")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    assert "chefin" not in satz["by"]
    assert satz["by"].startswith("lokaler Bediener")


def _signiert(kennung: str, geheimnis: str) -> dict:
    import hashlib
    import hmac
    import time

    ts = str(int(time.time()))
    sig = hmac.new(
        geheimnis.encode("utf-8"), f"{kennung}:{ts}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {"X-User-Id": kennung, "X-User-Id-Sig": sig, "X-User-Id-Ts": ts}


def test_mit_signatur_ist_der_urheber_die_angemeldete_kennung(api, monkeypatch) -> None:
    ar, _, ablage = api
    monkeypatch.setenv("REQUIRE_SIG", "true")
    monkeypatch.setenv("PROXY_ENGINE_SHARED_SECRET", "geheim-3430")

    antwort = _client(ar).post(
        "/api/reconciliation/release",
        headers=_kopf(**_signiert("user-42", "geheim-3430")),
        json={},
    )

    assert antwort.status_code == 200
    satz = json.loads(
        (ablage / "reconciliation_audit.log")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    assert satz["by"] == "user-42"


def test_mit_falscher_signatur_bleibt_die_sperre(api, monkeypatch) -> None:
    ar, dienst, _ = api
    monkeypatch.setenv("REQUIRE_SIG", "true")
    monkeypatch.setenv("PROXY_ENGINE_SHARED_SECRET", "geheim-3430")

    antwort = _client(ar).post(
        "/api/reconciliation/release",
        headers=_kopf(**_signiert("user-42", "falsches-geheimnis")),
        json={},
    )

    assert antwort.status_code == 403
    assert dienst.entries_blocked is True


def test_ohne_protokoll_keine_aufhebung(api, monkeypatch) -> None:
    """Das Kriterium heisst „festgehalten". Laesst sich der Eintrag nicht schreiben, bleibt
    die Sperre — eine Freigabe ohne Spur waere genau der Nebeneffekt, den der Plan ausschliesst.
    """
    import core.reconciliation_audit as ra

    ar, dienst, _ = api

    def _kaputt(*_a, **_k):
        raise OSError("Datentraeger voll")

    monkeypatch.setattr(ra, "_anhaengen", _kaputt)
    antwort = _client(ar).post("/api/reconciliation/release", headers=_kopf(), json={})

    assert antwort.status_code == 500
    assert dienst.entries_blocked is True


def test_der_protokollausfall_behaelt_den_stacktrace(api, monkeypatch, caplog) -> None:
    """Review #3481, FINDING-02."""
    import logging

    import core.reconciliation_audit as ra

    ar, _, _ = api

    def _kaputt(*_a, **_k):
        raise OSError("Datentraeger voll")

    monkeypatch.setattr(ra, "_anhaengen", _kaputt)
    with caplog.at_level(logging.ERROR):
        _client(ar).post("/api/reconciliation/release", headers=_kopf(), json={})

    (satz,) = [r for r in caplog.records if "NICHT aufgehoben" in r.getMessage()]
    assert satz.exc_info and satz.exc_info[0] is OSError


@pytest.mark.parametrize(
    "koerper", [{"reason": 42}, {"reason": "x" * 501}, {"grund": "unbekanntes Feld"}]
)
def test_ein_ungueltiger_koerper_wird_abgewiesen(api, koerper) -> None:
    """Review #3481, FINDING-01: der Koerper ist typisiert. Was nicht passt, wird abgewiesen
    — und die Sperre bleibt."""
    ar, dienst, _ = api
    antwort = _client(ar).post(
        "/api/reconciliation/release", headers=_kopf(), json=koerper
    )

    assert antwort.status_code == 422
    assert dienst.entries_blocked is True


def test_ein_neuer_befund_waehrend_des_schreibens_hebt_nichts_auf(
    api, monkeypatch
) -> None:
    """Freigegeben wird nur, was protokolliert wurde. Setzt ein Abgleichlauf waehrend des
    Schreibens einen neuen Befund, bleibt die Sperre."""
    import core.reconciliation_audit as ra
    from core.contracts import ReconciliationBreak, ReconciliationRecord

    ar, dienst, _ = api
    echt = ra._anhaengen
    jetzt = datetime(2026, 9, 18, 14, 10, tzinfo=timezone.utc)
    neu = ReconciliationRecord(
        run_id="lauf-3",
        started_at=jetzt,
        finished_at=jetzt,
        breaks=(ReconciliationBreak(kind="unknown_position", symbol="MSFT"),),
    )

    def _mit_neuem_lauf(pfad, zeile):
        echt(pfad, zeile)
        dienst._report(neu)

    monkeypatch.setattr(ra, "_anhaengen", _mit_neuem_lauf)
    antwort = _client(ar).post("/api/reconciliation/release", headers=_kopf(), json={})

    assert antwort.status_code == 409
    assert dienst.entries_blocked is True
    assert dienst.block_record is neu


def test_ohne_koerper_geht_die_aufhebung_ohne_grund(api) -> None:
    ar, dienst, ablage = api
    antwort = _client(ar).post("/api/reconciliation/release", headers=_kopf())

    assert antwort.status_code == 200
    assert dienst.entries_blocked is False
    satz = json.loads(
        (ablage / "reconciliation_audit.log")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    assert satz["reason"] == ""


def test_aufheben_ohne_schluessel_geht_nicht(api) -> None:
    ar, dienst, _ = api
    antwort = _client(ar).post("/api/reconciliation/release", json={})

    assert antwort.status_code in (401, 403)
    assert dienst.entries_blocked is True


def test_ohne_abgleich_ist_aufheben_ein_fehler(api, monkeypatch) -> None:
    ar, _, _ = api
    monkeypatch.setattr(ar, "engine", None)

    assert (
        _client(ar)
        .post("/api/reconciliation/release", headers=_kopf(), json={})
        .status_code
        == 503
    )


# ---------------------------------------------------------------------------
# Ohne Bedienung bleibt die Sperre
# ---------------------------------------------------------------------------


def test_ohne_bedienung_bleibt_die_sperre(api) -> None:
    """Ein sauberer Folgelauf ist kein Mensch (#3389). Nur die Aufhebung gibt frei."""
    import asyncio

    from core.contracts import ReconciliationRecord

    _, dienst, _ = api
    jetzt = datetime(2026, 9, 18, 14, 5, tzinfo=timezone.utc)
    sauber = ReconciliationRecord(run_id="lauf-2", started_at=jetzt, finished_at=jetzt)
    dienst.last_record = sauber
    dienst._report(sauber)

    assert dienst.entries_blocked is True
    assert asyncio.iscoroutinefunction(dienst.run_once)  # Dienst unveraendert


def test_nach_sauberem_folgelauf_zeigt_die_anzeige_weiter_den_grund(api) -> None:
    """Der letzte Lauf ist sauber, die Sperre steht noch. Die Anzeige muss zeigen, WARUM
    gesperrt ist — sonst gibt der Bediener eine Sperre frei, deren Grund er nicht sieht.
    """
    from core.contracts import ReconciliationRecord

    ar, dienst, _ = api
    jetzt = datetime(2026, 9, 18, 14, 5, tzinfo=timezone.utc)
    sauber = ReconciliationRecord(run_id="lauf-2", started_at=jetzt, finished_at=jetzt)
    dienst.last_record = sauber
    dienst._report(sauber)

    daten = _client(ar).get("/api/reconciliation/block", headers=_kopf()).json()
    assert daten["entries_blocked"] is True
    assert daten["run_id"] == "lauf-1"
    assert daten["breaks"][0]["symbol"] == "AAPL"
    assert daten["last_run_id"] == "lauf-2"
    assert daten["last_run_clean"] is True


def test_nach_der_aufhebung_ist_der_grund_abgelegt(api) -> None:
    ar, dienst, _ = api
    _client(ar).post("/api/reconciliation/release", headers=_kopf(), json={})

    daten = _client(ar).get("/api/reconciliation/block", headers=_kopf()).json()
    assert daten["entries_blocked"] is False
    assert daten["breaks"] == []
    assert dienst.block_record is None
