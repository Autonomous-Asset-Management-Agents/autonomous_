"""#4463 — ADR-026: Analytics- und A/B-Werkzeug fuer die Landing ist entschieden.

Plan: ``docs/4463-analytics-und-a-b-werkzeug-waehlen-self-hosted-c/implementation_plan.md``.

Die vorhandenen Doku-Waechter (``test_doku_index.py``, ``test_doc_anchors.py``) pruefen nur,
dass Verweise ein Ziel haben — eine fehlende ADR faellt ihnen nicht auf. Dieser Test haelt
die Gherkin-Abnahme aus #4463 fest: Es gibt die ADR, sie vergleicht PostHog und GrowthBook
an den vier Kriterien, nennt Werkzeug, EU-Region und Rechtsgrundlage, und der Index kennt sie.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

_REPO = Path(__file__).resolve().parents[3]
_DOCS = _REPO / "docs"
_ADR_NAME = "ADR-026-Website-Analytics-AB.md"
_ADR = _DOCS / "1_architecture_and_adr" / _ADR_NAME


def _text() -> str:
    assert _ADR.exists(), f"ADR fehlt: {_ADR.relative_to(_REPO)}"
    return _ADR.read_text(encoding="utf-8")


def test_adr_vergleicht_posthog_und_growthbook_an_den_vier_kriterien():
    text = _text()
    for kandidat in ("PostHog", "GrowthBook"):
        assert kandidat in text, kandidat
    for kriterium in (
        "cookieless",
        "Flicker",
        "Metrik-API",
        "Betriebskosten",
    ):
        assert kriterium in text, f"Kriterium fehlt: {kriterium}"


def test_adr_nennt_werkzeug_region_und_rechtsgrundlage():
    text = _text()
    assert "## Entscheidung" in text
    assert "europe-west3" in text, "Hosting-Ort (EU-Region) fehlt"
    assert "§ 25 TDDDG" in text
    assert "Art. 6 Abs. 1 lit. f DSGVO" in text, "Rechtsgrundlage der Messung fehlt"


def test_adr_traegt_einen_status():
    kopf = _text().split("## ", 1)[0]
    assert "**Status:**" in kopf


@pytest.mark.parametrize("index", ["llms.txt", "index.md"])
def test_index_verweist_auf_die_adr(index):
    assert _ADR_NAME in (_DOCS / index).read_text(encoding="utf-8")
