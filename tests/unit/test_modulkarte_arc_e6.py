"""#4433 (ARC-E6 N-2a) — die Orientierungsdokumente kennen die Module aus ARC-E6.

Plan: ``docs/4433-arc-e6-n-2a-a-modulkarte-llms-txt-architecture-u/implementation_plan.md``.

Die Umzuege H-1 bis H-5 (#4231 bis #4291) haben 36 Produktivmodule angelegt. Vor diesem
Sub-Issue stand keines davon in ``MODULE_MAP.md``, ``llms.txt``, ``oss/ARCHITECTURE.md``
oder ``AGENT_CATALOG.md``, und acht Zeilenanker zeigten in die geschrumpften
Hotspot-Dateien auf Zeilen, an denen inzwischen etwas anderes steht.

Die Liste ist fest und datiert (Option A des Plans), erhoben am 09.10.2026 auf ``main`` mit::

    git log --since=2026-10-05 --diff-filter=A --name-only --format="" -- ai_trading_bot/core \\
        | grep "\\.py$" | grep -v -E "__init__|tests/" | sort -u

Eine allgemeine Abdeckungsregel fuer alle ``core/``-Module gehoert zu N-3 #4435 und darf
diesen Test abloesen.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

_REPO = Path(__file__).resolve().parents[3]
_MODULE_MAP = _REPO / "docs" / "1_architecture_and_adr" / "MODULE_MAP.md"
_DOKUMENTE = (
    _MODULE_MAP,
    _REPO / "docs" / "llms.txt",
    _REPO / "docs" / "oss" / "ARCHITECTURE.md",
    _REPO / "docs" / "2_agentic_operations" / "AGENT_CATALOG.md",
)

#: Die 36 Module aus ARC-E6 H-1 bis H-5, Pfade relativ zu ``ai_trading_bot/``.
ARC_E6_MODULE = (
    # H-1 Order-Pfad
    "core/engine/order_aufbau.py",
    "core/engine/pm_zustand.py",
    "core/engine/bemessung_helfer.py",
    "core/engine/mandanten_zugang.py",
    "core/engine/hitl_freigabe.py",
    "core/engine/absendung_vorlauf.py",
    "core/engine/absendung_abgang.py",
    "core/engine/absendung_tor.py",
    "core/engine/signal_desktop_entscheid.py",
    "core/engine/signal_desktop_absendung.py",
    "core/engine/verdraengung.py",
    "core/engine/symbol_schluessel.py",
    # H-2 Handelsschleife
    "core/engine/zyklus.py",
    "core/engine/positionsbuch.py",
    "core/engine/schleifen_start.py",
    "core/engine/schleifen_stops.py",
    "core/engine/marktdaten.py",
    "core/engine/zyklus_vorlauf.py",
    "core/engine/zyklus_kontext.py",
    "core/engine/zyklus_bewertung.py",
    "core/engine/ausfuehrung.py",
    # H-3 Risk
    "core/risk_vorpruefung.py",
    "core/risk_deckel.py",
    "core/risk_bemessung.py",
    "core/risk_konto.py",
    "core/risk_skalierer.py",
    # H-4 Round Table
    "core/round_table/entscheidungs_zaehler.py",
    "core/round_table/signal_bau.py",
    "core/round_table/gate_stufen.py",
    "core/round_table/aufzeichnung.py",
    # H-5 Portfolio
    "core/portfolio_typen.py",
    "core/portfolio_bericht.py",
    "core/portfolio_handelsbuch.py",
    "core/portfolio_zulassung.py",
    "core/portfolio_verdraengung.py",
    "core/portfolio_bestand.py",
)

#: Zeilenanker (Raute-L- oder Doppelpunkt-Form) in die fuenf Hotspot-Dateien.
_HOTSPOT_ZEILENANKER = re.compile(
    r"(order_executor|trading_loop|risk_manager|portfolio_manager|runner)\.py(#L|:)[0-9]+"
)


def test_liste_hat_36_existierende_module():
    """Die feste Liste veraltet nicht still: jeder Pfad existiert, keiner doppelt."""
    assert len(ARC_E6_MODULE) == 36
    assert len(set(ARC_E6_MODULE)) == 36
    fehlend = [m for m in ARC_E6_MODULE if not (_REPO / "ai_trading_bot" / m).is_file()]
    assert fehlend == []


def test_jedes_arc_e6_modul_steht_in_der_modulkarte():
    text = _MODULE_MAP.read_text(encoding="utf-8")
    fehlend = [m for m in ARC_E6_MODULE if f"`{m}`" not in text]
    assert fehlend == [], f"{len(fehlend)} Module fehlen in MODULE_MAP.md: {fehlend}"


def test_kein_zeilenanker_in_hotspot_dateien():
    funde = [
        f"{dok.relative_to(_REPO)}:{nr}: {m.group(0)}"
        for dok in _DOKUMENTE
        for nr, zeile in enumerate(dok.read_text(encoding="utf-8").splitlines(), 1)
        for m in _HOTSPOT_ZEILENANKER.finditer(zeile)
    ]
    assert funde == [], "Zeilenanker in Hotspot-Dateien:\n" + "\n".join(funde)


def test_verdraengung_reihenfolge_steht_richtig():
    """#3279: erst ``register_displacement_leg``, dann Absenden ueber das Tor."""
    text = _MODULE_MAP.read_text(encoding="utf-8")
    alte_reihenfolge = (
        "`client.submit_order(req)` gefolgt von `register_displacement_leg"
    )
    assert not (
        alte_reihenfolge in text
    ), "MODULE_MAP.md nennt noch: erst senden, dann melden"
    assert "core/engine/verdraengung.py::submit_deferred_displacement_sell" in text


def test_compliance_check_ist_beim_aufruf_verortet():
    """HAR-4 und ComplianceGuardian zeigen auf den Ort des ``check_order``-Aufrufs."""
    text = _MODULE_MAP.read_text(encoding="utf-8")
    anker = "core/engine/absendung_abgang.py::AbsendungAbgangMixin._schritt_mandant_compliance"
    har4 = next(z for z in text.splitlines() if z.startswith("| **HAR-4**"))
    guardian = next(
        z
        for z in text.splitlines()
        if z.startswith("| **ComplianceGuardian (Iron Dome)**")
    )
    assert anker in har4
    assert anker in guardian
