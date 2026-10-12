"""Abnahme auf Epic-Ebene für #4450 — beim Anlegen rot (#3785).

Diese Datei ist vom Maker erzeugt. Sie beschreibt, woran man erkennt, dass das
Epic erfüllt ist, und nicht, wie es umgesetzt wird. Wer ein Sub-Issue umsetzt,
ersetzt hier die Schritte, die sein Teil betrifft, durch echte Prüfungen.

Der xfail-Marker fällt erst, wenn alle Schritte echt sind — bis dahin hält er
die Suite grün, ohne die offene Abnahme zu verstecken."""

import re
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

REPO = Path(__file__).resolve().parents[4]

scenarios("../epic_4450.feature")

pytestmark = pytest.mark.xfail(
    reason="Epic #4450 ist nicht umgesetzt — die Abnahme ist offen.",
    strict=True,
)

_OFFEN = (
    "Schritt aus der Abnahme von #4450 ist noch nicht hinterlegt. "
    "Wer das zugehörige Sub-Issue umsetzt, ersetzt ihn durch eine echte Prüfung."
)


@given("der Stand von main")
def schritt_1():
    raise NotImplementedError(_OFFEN)


@when(
    "die Zweck-Kennzahl gezählt wird (Treffer von asset management, profitab*, safe, sicher, rendite* in den String-Werten von src/locales/en|de/translation.json, den JSX-Textknoten von src/components/views/LandingViewE.tsx und index.html außerhalb von Kommentaren)"
)
def schritt_2():
    raise NotImplementedError(_OFFEN)


@then("ist sie 0")
def schritt_3():
    raise NotImplementedError(_OFFEN)


@then("index.html enthält außerhalb von Kommentaren kein robots-Meta 'noindex'")
def schritt_4():
    raise NotImplementedError(_OFFEN)


@then(
    "index.html enthält einen parsebaren JSON-LD-Block mit '@type' 'SoftwareApplication'"
)
def schritt_5():
    raise NotImplementedError(_OFFEN)


@then("public/llms.txt existiert, beginnt mit '# ' und nennt 'execution framework'")
def schritt_6():
    raise NotImplementedError(_OFFEN)


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
