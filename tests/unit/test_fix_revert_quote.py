"""#3368 (ARC-E3) — die Messung des Fix- und Revert-Anteils darf nicht einschlafen.

Die Definition of Done von Epic #3368 verlangt: „die Messung des Fix- und Revert-Anteils
läuft automatisch". ADR-023 nennt 26,4 % (16.09.2026) und sagt dazu, die Zahl werde
„erst belastbar, wenn die laufende Messung Teil der CI ist".

**Warum hier kein Drift-Abgleich steht.** Das Messfenster wandert (90 Tage), die Zahl
ändert sich täglich. Eine Prüfung „Register == heutige Messung" wäre an jedem zweiten Tag
rot, ohne dass jemand etwas falsch gemacht hätte — und eine Prüfung, die grundlos rot
wird, schaltet man ab. Geprüft wird deshalb das **Alter** des jüngsten Eintrags: Die
Messung muss stattfinden, nicht zu jeder Minute stimmen.

**Was das hier NICHT leistet:** Es rechnet die Zahl nicht selbst nach. Das ginge nur mit
der vollen Historie; die CI checkt flach aus (kein ``fetch-depth`` in
``.github/workflows/ci.yml``), ein Nachrechnen sähe dort genau einen Commit. Der Weg
dahin wäre ein ``fetch-depth: 0`` im Workflow — eine Owner-Aktion mit
``governance-bypass``. Bis dahin ist dieser Test die Bremse gegen das Vergessen.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
REGISTER = _REPO / "docs" / "5_engineering_and_devops" / "FIX_REVERT_QUOTE.md"

#: Höchstalter der jüngsten Messung. Ein Monat: Der Anteil ist ein Trend über Monate,
#: eine wöchentliche Pflicht wäre Zeremonie ohne Erkenntnisgewinn.
HOECHSTALTER_TAGE = 35

ZEILE = re.compile(
    r"^\| (?P<datum>\d{4}-\d{2}-\d{2}) \| (?P<fenster>\d+) Tage \| (?P<gesamt>\d+) \| "
    r"(?P<treffer>\d+) \| \*\*(?P<anteil>[\d,.]+) %\*\* \|"
)

pytestmark = pytest.mark.vc0


def _messungen() -> list[re.Match]:
    text = REGISTER.read_text(encoding="utf-8")
    return [m for m in (ZEILE.match(z) for z in text.splitlines()) if m]


def test_das_register_existiert_und_traegt_die_definition():
    assert REGISTER.exists(), f"{REGISTER} fehlt — `python scripts/mess_fix_revert.py`"
    text = REGISTER.read_text(encoding="utf-8")
    assert "--since=90.days" in text, "die eingefrorene Definition fehlt"
    assert "Grenze der Aussage" in text, "die Grenze der Aussage fehlt"


def test_mindestens_eine_messung_steht_darin():
    assert _messungen(), "keine Messzeile im Register"


def test_die_zahlen_passen_zueinander():
    """Ein Register, dessen Anteil nicht zu seinen Zahlen passt, ist schlimmer als keins."""
    for m in _messungen():
        gesamt, treffer = int(m["gesamt"]), int(m["treffer"])
        anteil = float(m["anteil"].replace(",", "."))
        assert treffer <= gesamt, m.group(0)
        erwartet = treffer / gesamt * 100
        assert (
            abs(erwartet - anteil) < 0.1
        ), f"{m['datum']}: {anteil} statt {erwartet:.1f}"


def test_die_messung_ist_nicht_eingeschlafen():
    juengste = max(date.fromisoformat(m["datum"]) for m in _messungen())
    alter = (date.today() - juengste).days
    assert alter <= HOECHSTALTER_TAGE, (
        f"Die jüngste Messung ist {alter} Tage alt (erlaubt: {HOECHSTALTER_TAGE}). "
        "Führe `python scripts/mess_fix_revert.py` aus und committe das Register — "
        "eine Kennzahl, die niemand mehr erhebt, ist keine Kennzahl (#3368)."
    )


def test_der_erste_eintrag_haelt_den_ausgangswert_fest():
    """Ohne Ausgangswert ist jede spätere Zahl ein Eindruck, kein Trend."""
    erste = min(_messungen(), key=lambda m: date.fromisoformat(m["datum"]))
    assert date.fromisoformat(erste["datum"]) <= date(2026, 9, 30)
    assert 20.0 < float(erste["anteil"].replace(",", ".")) < 35.0


def test_das_hoechstalter_laesst_eine_monatliche_messung_zu():
    assert timedelta(days=HOECHSTALTER_TAGE) > timedelta(days=31)
