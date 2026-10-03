"""#2783 Inkrement 1 — die WORM-Kette traegt die ``decision_id``.

**Der Bruch, den dieses Inkrement schliesst.** Es gibt zwei Speicher fuer dieselbe
Entscheidung, und sie lassen sich nicht verbinden:

* ``decision_outcomes`` traegt die ``decision_id`` (``capture.py:126``).
* Die hash-verkettete WORM-Datei traegt sie **nicht** — ``senate_log.py`` kennt den
  Begriff an keiner Stelle.

Verbinden liesse sie sich heute nur ueber ``(session_id, symbol)``. Das ist kein
Schluessel, sondern ein Zufall gemeinsamer Spalten: Eine Sitzung bewertet viele Symbole,
die Eindeutigkeit erzwingt niemand. Und weil die Kette auf nichts zeigen kann, **muss**
``votes_json`` dupliziert werden — in der groessten Audit-Datei 62 von 75 MB.

**Warum die Aenderung sicher ist — und warum das gepinnt gehoert, nicht geglaubt.**
``pyJsonDumps`` (``desktop/electron/audit-chain.cjs``) serialisiert rekursiv mit
sortierten Schluesseln und hat **keine Feldliste**. Ein zusaetzlicher Schluessel ist fuer
den JS-Verifier damit durchsichtig, und **alte Datensaetze ohne das Feld verifizieren
weiter**, weil jeder Datensatz sein eigenes Preimage aus seinen eigenen Schluesseln
bildet. Genau das prueft ``test_ein_alter_datensatz_ohne_das_feld_bleibt_pruefbar``.
"""

from __future__ import annotations

import hashlib
import json

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def audit_verzeichnis(tmp_path, monkeypatch):
    verzeichnis = tmp_path / "oss_audit_logs"
    monkeypatch.setenv("SENATE_LOG_DIR", str(verzeichnis))
    return verzeichnis


def _sitzung(**abweichend):
    from core.round_table.senate_log import SenateSession

    felder = {
        "session_id": "sess-1",
        "symbol": "AAPL",
        "timestamp": "2026-09-18T06:00:00+00:00",
        "votes": [{"agent": "a", "action": "BUY"}],
        "consensus_score": 0.71,
        "gatekeeper_approved": True,
        "gatekeeper_reason": "ok",
        "signal_action": "BUY",
    }
    felder.update(abweichend)
    return SenateSession(**felder)


def _eintraege(verzeichnis):
    dateien = sorted(verzeichnis.glob("audit_log_*.jsonl"))
    zeilen = []
    for d in dateien:
        zeilen += [
            json.loads(z)
            for z in d.read_text(encoding="utf-8").splitlines()
            if z.strip()
        ]
    return zeilen


# ---------------------------------------------------------------------------
# Der fuehrende rote Fall
# ---------------------------------------------------------------------------


async def test_der_audit_datensatz_traegt_die_decision_id(audit_verzeichnis) -> None:
    """Szenario: Jeder WORM-Datensatz nennt die Entscheidung, aus der er stammt.

    Heute rot: ``_async_log_to_jsonl`` baut sein Dict aus acht Feldern, ``decision_id``
    ist keines davon. Damit ist die Kette nicht auf ``decision_outcomes`` beziehbar —
    der Bruch 1 aus #2783.
    """
    from core.round_table.senate_log import LocalJSONAuditLogger

    protokoll = LocalJSONAuditLogger()
    await protokoll._async_log_to_jsonl(_sitzung(decision_id="entscheidung-42"))

    eintraege = _eintraege(audit_verzeichnis)
    assert eintraege, "Es wurde ueberhaupt kein Datensatz geschrieben."
    assert eintraege[-1].get("decision_id") == "entscheidung-42", (
        "Der WORM-Datensatz traegt keine decision_id. Ohne sie laesst sich die "
        "hash-verkettete Datei nur ueber (session_id, symbol) mit decision_outcomes "
        "verbinden — 'a coincidence of shared columns, not an identity' (#2783)."
    )


# ---------------------------------------------------------------------------
# Die Zusage, die den Umbau ueberhaupt erst sicher macht
# ---------------------------------------------------------------------------


async def test_ein_alter_datensatz_ohne_das_feld_bleibt_pruefbar(
    audit_verzeichnis,
) -> None:
    """Szenario: Bestandsdatensaetze verifizieren weiter.

    Die Sicherheit dieses Umbaus haengt an einer Eigenschaft des Verifiers: Er bildet
    das Preimage jedes Datensatzes aus **dessen eigenen** Schluesseln, sortiert, ohne
    feste Feldliste. Ein neues Feld aendert deshalb nur die Hashes der **neuen**
    Datensaetze — die alten bleiben gueltig, und die Verkettung vertraegt gemischte
    Staende.

    **Das ist die Annahme, auf der alles ruht, und sie wird hier gemessen statt
    geglaubt.** Der Test schreibt einen Datensatz OHNE das Feld, danach einen MIT, und
    prueft beide Hashes nach derselben Regel, die der Verifier anwendet.
    """
    from core.round_table.senate_log import LocalJSONAuditLogger

    protokoll = LocalJSONAuditLogger()
    await protokoll._async_log_to_jsonl(_sitzung(symbol="MSFT"))  # ohne decision_id
    await protokoll._async_log_to_jsonl(
        _sitzung(symbol="NVDA", decision_id="entscheidung-7")
    )

    eintraege = _eintraege(audit_verzeichnis)
    assert len(eintraege) >= 2, f"Erwartet zwei Datensaetze, gefunden {len(eintraege)}."

    vorheriger = "0" * 64
    for i, eintrag in enumerate(eintraege):
        # Exakt die Regel aus `_write_to_hash_chain`: der Eintrag EINSCHLIESSLICH
        # `prev_hash`, aber ohne `hash`, mit sortierten Schluesseln und den
        # Vorgabe-Trennzeichen von `json.dumps`. Eine erste Fassung dieses Tests nahm
        # die kompakten Trennzeichen `(",", ":")` an — geraten statt gelesen, und der
        # Test war rot, obwohl der Code stimmte.
        nutzlast = {k: v for k, v in eintrag.items() if k != "hash"}
        erwartet = hashlib.sha256(
            json.dumps(nutzlast, sort_keys=True).encode()
        ).hexdigest()
        assert eintrag["hash"] == erwartet, (
            f"Datensatz {i} verifiziert nicht mehr. Ein zusaetzlicher Schluessel darf "
            "die Pruefbarkeit nicht brechen — sonst waere der gesamte Bestand von "
            "175.489 Zeilen entwertet (#2783)."
        )
        assert eintrag.get("prev_hash") == vorheriger, (
            f"Die Verkettung bricht bei Datensatz {i}. Gemischte Staende — mit und "
            "ohne das neue Feld — muessen sich verketten lassen."
        )
        vorheriger = eintrag["hash"]


async def test_ohne_decision_id_bleibt_der_datensatz_gueltig(audit_verzeichnis) -> None:
    """Ein Aufrufer ohne Entscheidungskontext darf den Audit-Pfad nicht brechen.

    Die Ausfallrichtung ist bewusst: Ein Datensatz **ohne** Bezug ist schlechter als
    einer mit — aber unendlich viel besser als **kein** Datensatz. Der Audit-Pfad darf
    den Handelspfad nie zum Stehen bringen (``_write_to_hash_chain``: „failures are
    logged, never raised").
    """
    from core.round_table.senate_log import LocalJSONAuditLogger

    protokoll = LocalJSONAuditLogger()
    await protokoll._async_log_to_jsonl(_sitzung())

    eintraege = _eintraege(audit_verzeichnis)
    assert eintraege, "Ohne decision_id wurde gar nichts geschrieben."
    assert eintraege[-1]["symbol"] == "AAPL"
    assert "hash" in eintraege[-1] and "prev_hash" in eintraege[-1]
