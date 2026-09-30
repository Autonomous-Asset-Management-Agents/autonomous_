"""#3726 — das Siegel muss seinen Inhalt beglaubigen, nicht nur seine Position.

Das Siegel aus #3722 belegt mit seinem Anker, **dass die Kette an dieser Stelle noch
diesen Hash trägt**. Es belegt nicht, dass die Liste ``nonces`` aus genau diesem
Kettenabschnitt gewonnen wurde. Wer die Liste leert und den Anker unangetastet lässt,
macht eine benutzte Nonce wieder „unbenutzt" — nachgestellt in
``test_geleertes_siegel_hebt_die_sperre_nicht_auf``.

**Was das ist und was nicht** (Korrektur am ersten Befund, siehe #3726): Der Weg über die
Kette war vorher genauso billig — eine gelöschte Tagesdatei entfernt ihre Nonces aus jedem
Scan, und ``verifyAuditChain`` prüft nur den jüngsten ``live_enablement``-Eintrag, nicht
die Vollständigkeit der Historie. Neu ist also keine Angriffsklasse, sondern

* eine **unauffällige** zweite Tür (die Kette bleibt unangetastet) und
* ein Weg, auf dem ein **Unfall** die Sperre aufweicht: halb geschriebene Datei,
  kopiertes Profil, gekürzte Datei.

Eine Prüfsumme über den Inhalt fängt genau das. Eine Signatur würde nicht mehr bringen,
solange die gleich billige Tür „Tagesdatei löschen" offen steht — deshalb bleibt sie
draußen.

Die Zeilenzahl prüfen diese Tests an der **Umsetzung** (``letzte_lesestatistik``), nicht
an der Vorrichtung: Der Abnahmetest aus #3722 las den Zähler aus dem Siegel, das er selbst
geschrieben hatte, und wäre auch grün geblieben, wenn die ganze Kette gelesen würde.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

pytestmark = pytest.mark.vc4

SIEGEL = ".live_enable_nonces_seal.json"


@pytest.fixture
def kette(tmp_path, monkeypatch):
    """Eine verkettete Prüfkette mit einer benutzten Nonce, wie der echte Schreiber sie legt."""
    from core import hitl_gate

    eintraege = []
    prev = None
    for i in range(20):
        eintrag = {"event_type": "heartbeat", "i": i, "prev_hash": prev}
        if i == 5:
            eintrag["event_type"] = "live_enablement"
            eintrag["nonce"] = "NONCE-BENUTZT"
        h = hashlib.sha256(json.dumps(eintrag, sort_keys=True).encode()).hexdigest()
        eintrag["hash"] = h
        prev = h
        eintraege.append(eintrag)
    (tmp_path / "audit_log_2026-09-20.jsonl").write_text(
        "\n".join(json.dumps(e) for e in eintraege) + "\n", encoding="utf-8"
    )

    class Logger:
        _log_dir = tmp_path

    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: Logger())
    return tmp_path


def _lauf() -> set[str]:
    from core import hitl_gate

    return asyncio.run(hitl_gate.collect_live_enablement_nonces())


def test_geleertes_siegel_hebt_die_sperre_nicht_auf(kette):
    """Der Vorfall: Anker gültig, Liste geleert — die Nonce muss trotzdem gefunden werden."""
    assert "NONCE-BENUTZT" in _lauf(), "erster Lauf muss die Nonce aus der Kette lesen"

    pfad = kette / SIEGEL
    siegel = json.loads(pfad.read_text(encoding="utf-8"))
    siegel["nonces"] = []
    pfad.write_text(json.dumps(siegel), encoding="utf-8")

    assert "NONCE-BENUTZT" in _lauf(), (
        "Ein Siegel mit geleerter Liste darf nicht geglaubt werden — sonst gilt eine "
        "benutzte Nonce als unbenutzt und die Wiederholungssperre ist offen."
    )


def test_eine_zusaetzlich_untergeschobene_nonce_wird_nicht_geglaubt(kette):
    """Die andere Richtung: Ein Siegel darf keine Nonce erfinden, die nie in der Kette stand
    — sonst sperrt eine gefaelschte Datei ein legitimes erstes Scharfschalten aus."""
    _lauf()
    pfad = kette / SIEGEL
    siegel = json.loads(pfad.read_text(encoding="utf-8"))
    siegel["nonces"] = list(siegel["nonces"]) + ["NIE-BENUTZT"]
    pfad.write_text(json.dumps(siegel), encoding="utf-8")

    assert "NIE-BENUTZT" not in _lauf()


def test_ein_beschaedigtes_siegel_wird_verworfen_und_gemeldet(kette, caplog):
    _lauf()
    (kette / SIEGEL).write_text("{kaputt", encoding="utf-8")
    from core import hitl_gate

    assert "NONCE-BENUTZT" in _lauf()
    assert hitl_gate.letzte_lesestatistik()["siegel"] == "verworfen"


def test_die_pruefung_liest_nach_dem_siegel_nur_den_zuwachs(kette):
    """Gezaehlt in der Umsetzung, nicht im Test: das ist der Unterschied zu #3722."""
    from core import hitl_gate

    _lauf()
    voll = hitl_gate.letzte_lesestatistik()
    assert voll["zeilen"] == 20 and voll["siegel"] == "fehlt"

    _lauf()
    mit_siegel = hitl_gate.letzte_lesestatistik()
    assert mit_siegel["siegel"] == "genutzt"
    assert (
        mit_siegel["zeilen"] == 0
    ), "Nach dem Siegel gibt es keinen Zuwachs — es darf keine Zeile mehr gelesen werden."


def test_die_zahl_gelesener_zeilen_haengt_nicht_an_der_kettengroesse(kette):
    """Dieselbe Zusicherung wie im Abnahmeszenario, hier mit Zahlen statt mit `pass`."""
    from core import hitl_gate

    _lauf()  # Siegel anlegen
    _lauf()
    klein = hitl_gate.letzte_lesestatistik()["zeilen"]

    # Die Kette waechst um 500 Zeilen VOR dem Siegel wuerde sie nicht mehr; also waechst
    # sie dahinter — und nur dieser Zuwachs darf zaehlen.
    datei = kette / "audit_log_2026-09-20.jsonl"
    prev = json.loads(datei.read_text(encoding="utf-8").splitlines()[-1])["hash"]
    with datei.open("a", encoding="utf-8") as f:
        for i in range(500):
            e = {"event_type": "heartbeat", "i": 1000 + i, "prev_hash": prev}
            h = hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()
            e["hash"] = h
            prev = h
            f.write(json.dumps(e) + "\n")

    _lauf()
    gross = hitl_gate.letzte_lesestatistik()["zeilen"]
    assert gross == 500, f"Zuwachs 500 erwartet, gelesen {gross}"
    _lauf()
    danach = hitl_gate.letzte_lesestatistik()["zeilen"]
    assert (
        danach == klein == 0
    ), "Nach dem Nachziehen des Siegels darf die naechste Pruefung wieder null Zeilen lesen"


def test_das_siegel_liegt_nicht_in_der_kette(kette):
    _lauf()
    assert (kette / SIEGEL).exists()
    assert not list(kette.glob("audit_log_*seal*"))
    for datei in kette.glob("audit_log_*.jsonl"):
        for zeile in datei.read_text(encoding="utf-8").splitlines():
            assert "nonces" not in zeile
