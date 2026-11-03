"""#4936 — kein Umami-/GrowthBook-Stack mehr im Repo.

Owner-Entscheid 28.10.2026 (ADR-026, #4907): Die Function ``edge`` zählt in Cloud Logging;
Umami und GrowthBook werden nicht gebaut. Was davon in Terraform stand, ist entfernt und soll
nicht unbemerkt zurückkommen.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

_REPO = Path(__file__).resolve().parents[3]
_TF = _REPO / "infra" / "terraform"


def test_kein_analytics_tf():
    assert not (_TF / "analytics.tf").exists()


@pytest.mark.parametrize("datei", sorted(p.name for p in _TF.glob("*.tf")))
def test_terraform_beschreibt_kein_umami_und_kein_growthbook(datei):
    text = (_TF / datei).read_text(encoding="utf-8")
    assert not re.search(r'resource "[a-z0-9_]+" "(umami|growthbook)[a-z_]*"', text)
    assert 'output "analytics_' not in text
