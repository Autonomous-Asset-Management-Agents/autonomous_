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
        "core/round_table/runner.py",
        "Default (``GATEKEEPER_STRICT_ML_REQUIRES_RL=True``)",
    ),
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
    ("core/engine/trading_loop.py", "(default is 20 = ACTIVE"),
]


@pytest.mark.parametrize("datei,aussage", VERALTET)
def test_die_veraltete_aussage_ist_korrigiert(datei, aussage):
    text = (PAKET / datei).read_text(encoding="utf-8")
    assert aussage not in text, f"{datei}: '{aussage}' widerspricht dem Default."
