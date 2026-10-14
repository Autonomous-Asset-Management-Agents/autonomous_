"""Abnahme auf Epic-Ebene für #4450 — beim Anlegen rot (#3785).

Diese Datei ist vom Maker erzeugt. Sie beschreibt, woran man erkennt, dass das
Epic erfüllt ist, und nicht, wie es umgesetzt wird. Wer ein Sub-Issue umsetzt,
ersetzt hier die Schritte, die sein Teil betrifft, durch echte Prüfungen.

Seit #4481 sind alle Schritte echte Prüfungen und der xfail-Marker ist gefallen."""

import json
import re
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

REPO = Path(__file__).resolve().parents[4]

scenarios("../epic_4450.feature")

pytestmark = [pytest.mark.vc0]

# Zweck-Kennzahl aus epic_4450.feature: dieselben Begriffe, dieselben Quellen wie
# die Messung im Plan (vorher 38 auf main 2e0558531, Ziel 0). Kein Modul eines
# Sub-Issues zählt sie; die Prüfung steht deshalb hier (Option B im Plan).
_BEGRIFFE = re.compile(
    r"asset[ -]management|profitab\w*|\bsafe\b|\bsicher\b|rendite\w*", re.I
)
_KOMMENTAR = re.compile(r"<!--.*?-->", re.S)


def _html_ohne_kommentare() -> str:
    return _KOMMENTAR.sub("", (REPO / "index.html").read_text(encoding="utf-8"))


def _string_werte(knoten) -> list[str]:
    if isinstance(knoten, str):
        return [knoten]
    if isinstance(knoten, dict):
        knoten = list(knoten.values())
    if isinstance(knoten, list):
        return [s for kind in knoten for s in _string_werte(kind)]
    return []


@given("der Stand von main", target_fixture="quellen")
def schritt_1():
    quellen = {
        "en": REPO / "src/locales/en/translation.json",
        "de": REPO / "src/locales/de/translation.json",
        "landing": REPO / "src/components/views/LandingViewE.tsx",
        "index": REPO / "index.html",
    }
    fehlend = [str(p) for p in quellen.values() if not p.is_file()]
    assert fehlend == [], f"Quellen der Zweck-Kennzahl fehlen: {fehlend}"
    return quellen


@when(
    "die Zweck-Kennzahl gezählt wird (Treffer von asset management, profitab*, safe, sicher, rendite* in den String-Werten von src/locales/en|de/translation.json, den JSX-Textknoten von src/components/views/LandingViewE.tsx und index.html außerhalb von Kommentaren)",
    target_fixture="treffer",
)
def schritt_2(quellen):
    treffer = {}
    for sprache in ("en", "de"):
        daten = json.loads(quellen[sprache].read_text(encoding="utf-8"))
        treffer[sprache] = [
            t for s in _string_werte(daten) for t in _BEGRIFFE.findall(s)
        ]
    jsx = quellen["landing"].read_text(encoding="utf-8")
    treffer["landing"] = [
        t for s in re.findall(r">([^<>{}]+)<", jsx) for t in _BEGRIFFE.findall(s)
    ]
    treffer["index"] = _BEGRIFFE.findall(_html_ohne_kommentare())
    return treffer


@then("ist sie 0")
def schritt_3(treffer):
    gesamt = sum(len(t) for t in treffer.values())
    assert gesamt == 0, f"Zweck-Kennzahl {gesamt} statt 0: {treffer}"


@then('index.html enthält außerhalb von Kommentaren kein robots-Meta "noindex"')
def schritt_4():
    # #4459: die Landing ist indexierbar; noindex kommt nur per Header aus firebase.json.
    metas = re.findall(r"<meta\b[^>]*>", _html_ohne_kommentare(), flags=re.I)
    robots = [m for m in metas if re.search(r"name=[\"']robots[\"']", m, flags=re.I)]
    assert [m for m in robots if "noindex" in m.lower()] == []


@then(
    'index.html enthält einen parsebaren JSON-LD-Block mit "@type" "SoftwareApplication"'
)
def schritt_5():
    blöcke = re.findall(
        r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        _html_ohne_kommentare(),
        flags=re.S | re.I,
    )
    assert blöcke, "kein JSON-LD-Block in index.html"
    typen = []
    for block in blöcke:
        daten = json.loads(block)
        knoten = daten.get("@graph", [daten]) if isinstance(daten, dict) else daten
        typen += [k.get("@type") for k in knoten if isinstance(k, dict)]
    assert "SoftwareApplication" in typen, f"@type im JSON-LD: {typen}"


@then('public/llms.txt existiert, beginnt mit "# " und nennt "execution framework"')
def schritt_6():
    llms = REPO / "public/llms.txt"
    assert llms.is_file()
    text = llms.read_text(encoding="utf-8")
    assert text.startswith("# ")
    assert "execution framework" in text.lower()


@then(
    "src/lib/analytics.ts existiert und index.html lädt kein Skript von googletagmanager.com"
)
def schritt_7():
    # #4467: cookieless Tracking-Client vorhanden; kein Google-Tag-Skript im HTML.
    assert (REPO / "src/lib/analytics.ts").is_file()
    html = re.sub(
        r"<!--.*?-->", "", (REPO / "index.html").read_text(encoding="utf-8"), flags=re.S
    )
    skripte = re.findall(r"<script\b[^>]*\bsrc=[\"']([^\"']+)", html, flags=re.I)
    assert [s for s in skripte if "googletagmanager.com" in s] == []


@then(
    "scripts/ux_judge.py existiert und docs/ux_personas/ enthält mindestens drei Personas"
)
def schritt_8():
    # #4475: UX Judge vorhanden; Personas aus #4488 unter docs/ux_personas/.
    assert (REPO / "scripts/ux_judge.py").is_file()
    assert len(list((REPO / "docs/ux_personas").glob("*.md"))) >= 3
