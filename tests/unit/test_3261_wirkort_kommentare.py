"""#3261 Phase 4 — Wirkort-Kommentare nennen den Default, den die Konfiguration hat.

Jede Zeile hier war ein Kommentar im Code, der ein eingeschaltetes Flag als "default OFF" bzw.
dormant beschrieb. Plan: ``docs/3261-flag-kommentar-widersprueche/implementation_plan.md`` §1.6.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]

# (Datei, veraltete Aussage) — keine davon darf zurueckkehren.
VERALTET = [
    ("core/round_table/gatekeeper.py", "GATEKEEPER_PDT_FINRA_ACCURATE (default OFF)"),
    (
        "core/data_integrity/__init__.py",
        "``DATA_INTEGRITY_GUARD_ENABLED`` (default OFF)",
    ),
    ("core/decision_capture/capture.py", "``DECISION_CAPTURE_ENABLED`` (default OFF)"),
    ("core/database/models.py", "(default OFF — owner-only, local DB, no egress)"),
    ("core/orchestration/graph.py", "REGIME_CONDITIONER_ENABLED is off (default)"),
    (
        "core/orchestration/graph.py",
        "ROUND_TABLE_POSITION_CONTEXT_ENABLED is off (default)",
    ),
    ("core/orchestration/graph.py", "dormant default OFF"),
    ("core/engine/base.py", "Report-only decoupling (dormant, default OFF)"),
    ("core/engine/base.py", "(report_only + weight 0)"),
    ("core/engine/base.py", "SPECIALIST_REGISTRY_ENABLED,\n        default OFF"),
    ("core/risk_manager.py", "default OFF => vol_scaler == 1.0 exactly"),
    ("core/risk_manager.py", "is set (default\n    OFF => the sizer"),
]


@pytest.mark.parametrize("datei,aussage", VERALTET)
def test_die_veraltete_aussage_ist_korrigiert(datei, aussage):
    if datei == "core/risk_manager.py":
        # #4264 (H-3b): über alle Themen-Module der Risikoverwaltung, nicht nur den Kern.
        from tests.unit._risiko_quelle import text_risikoverwaltung

        text = text_risikoverwaltung()
    else:
        text = (PAKET / datei).read_text(encoding="utf-8")
    assert aussage not in text, f"{datei}: '{aussage}' widerspricht dem Default."


def test_die_veraltete_aussage_der_handelsschleife_ist_korrigiert():
    """#4243 (H-2b): über alle Themen-Module der Handelsschleife, nicht nur den Kern."""
    from tests.unit import _schleifen_quelle

    aussage = "(default is 20 = ACTIVE"
    assert (
        aussage not in _schleifen_quelle.text_handelsschleife()
    ), f"Handelsschleife: '{aussage}' widerspricht dem Default."


def test_die_veraltete_aussage_des_round_table_ist_korrigiert():
    """#4275 (H-4b): über Kern und Zielmodule des Round Table, nicht nur ``runner.py``."""
    from tests.unit import _round_table_quelle

    aussage = "Default (``GATEKEEPER_STRICT_ML_REQUIRES_RL=True``)"
    assert (
        aussage not in _round_table_quelle.text_round_table()
    ), f"Round Table: '{aussage}' widerspricht dem Default."
