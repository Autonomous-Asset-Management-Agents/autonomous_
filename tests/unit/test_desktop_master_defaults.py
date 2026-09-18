"""#2839 — Desktop-Launcher-Profil ist der Master: die Config-Defaults beider
Editionen tragen exakt die Werte, die die Electron-Shell injiziert.

Owner-Entscheid 13.08.: es wird ausschließlich mit dem Desktop getestet, also
dürfen Läufe OHNE Launcher (Cloud, OSS, Sim, Sweep, Tests) nicht stillschweigend
ein anderes — ungetestetes — Profil fahren. Vorher wichen 10 Werte ab; jede
Abweichung hier ist eine Regression Richtung Drei-Schichten-Drift.

Subprozess-Probes (frischer Interpreter), damit weder Env-Reste noch der
Global-State-Canary (tests/conftest.py) hineinspielen.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_BOT_ROOT = Path(__file__).resolve().parents[2]

# GOLDEN: der eine Satz Werte, den Desktop-Installationen seit dem
# Launcher-Profil (#2678-Linie) tatsächlich fahren. Änderungen hier nur mit
# Owner-Entscheid + Registerzeile in docs/6_runbooks/ENGINE_TUNABLES.md.
GOLDEN_DESKTOP_MASTER = {
    "SMART_EXIT_MIN_HOLD_DAYS": 20.0,
    "SMART_EXIT_EXIT_RANK_HYSTERESIS": 5.0,
    "ROTATION_MAX_EXITS_PER_SESSION": 2,
    "EXIT_TRAIL_PROFILE": "midterm",
    "EXIT_POLICY_TRAILING_ENABLED": True,
    "DECISION_CAPTURE_ENABLED": True,
    "SPECIALIST_ALPHA_WEIGHT": 0.40,
    "SPECIALIST_COVERAGE_DYNAMIC": True,
    # 11. Angleichung (Messung, nicht in der ursprünglichen 10er-Liste): der
    # Launcher schreibt True; ohne Registry wäre das 0.40-Gewicht wirkungslos.
    # Heilt zugleich eine Alt-Paritätsverletzung (ent=False vs oss=True).
    "SPECIALIST_REGISTRY_ENABLED": True,
    "DESKTOP_FEATURE_SNAPSHOT_ENABLED": True,
}

# EXECUTION_BLOCK_TELEMETRY_ENABLED ist bewusst KEIN Settings-Feld, sondern
# eine Modul-Konstante (config.py Modulebene) — separat geprüft.
GOLDEN_MODULE_LEVEL = {"EXECUTION_BLOCK_TELEMETRY_ENABLED": True}

_PROBE = """
import json, os, shutil, sys, tempfile

edition_path, fields, mod_fields = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
d = tempfile.mkdtemp()
shutil.copy(edition_path, os.path.join(d, "config.py"))
sys.path.insert(0, d)
import config as cfg

c = cfg.get_config()
out = {k: getattr(c, k) for k in fields}
out.update({k: getattr(cfg, k) for k in mod_fields})
print(json.dumps(out))
"""


def _probe_defaults(edition_file: str) -> dict:
    """Import the given config edition in a clean interpreter, no env overrides."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in GOLDEN_DESKTOP_MASTER and k not in GOLDEN_MODULE_LEVEL
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _PROBE,
            str(_BOT_ROOT / edition_file),
            json.dumps(sorted(GOLDEN_DESKTOP_MASTER)),
            json.dumps(sorted(GOLDEN_MODULE_LEVEL)),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_BOT_ROOT),
        timeout=120,
    )
    assert (
        proc.returncode == 0
    ), f"{edition_file}: probe crashed:\n{proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("edition", ["config.py", "config.oss.py"])
def test_defaults_match_desktop_launcher_profile(edition):
    got = _probe_defaults(edition)
    expected = dict(GOLDEN_DESKTOP_MASTER, **GOLDEN_MODULE_LEVEL)
    mismatches = {k: (got[k], v) for k, v in expected.items() if got[k] != v}
    assert not mismatches, (
        f"{edition}: Defaults weichen vom Desktop-Master-Profil ab "
        f"(ist, soll): {mismatches} — siehe #2839 / ENGINE_TUNABLES.md"
    )


def test_editions_are_identical_on_master_profile():
    """BORA-Parität: beide Editionen liefern für die Master-Werte dasselbe."""
    ent = _probe_defaults("config.py")
    oss = _probe_defaults("config.oss.py")
    diff = {k: (ent[k], oss[k]) for k in ent if ent[k] != oss[k]}
    assert not diff, f"Editions-Drift (enterprise, oss): {diff}"
