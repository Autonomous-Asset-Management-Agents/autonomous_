# tests/unit/test_specialist_synthesis_card.py
# Rich-synthesis card (Direction A) — config coupling + serializer flag gate.
#
# Gherkin (plan):
#   Szenario: Karte ohne Grounding ist verboten (Fail-safe)
#     Angenommen SPECIALIST_SYNTHESIS_CARD_ENABLED ist ON, GROUNDING ist OFF
#     Wenn die Config lädt
#     Dann wird die Karte zwangsweise auf OFF gesetzt (nie ungebremste Prosa)
#   Szenario: Serializer emittiert Karten-Keys nur bei ON
#     Angenommen die Karte ist ON (mit Grounding)
#     Dann trägt das DTO synthesis_citations/dropped/grounding_ok/vol_band_* /
#          scenario_basis
#     Und bei OFF fehlen genau diese Keys (byte-identisch)
#
# HARD INVARIANT: the card can NEVER surface ungrounded prose — the config
# validator forces the card OFF when grounding is off, so a fabricated sentence
# has no path to the DTO.

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import allure
import pytest

import core.engine.api_routes as api_routes
from config import RuntimeConfigState


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Config Coupling")
class TestConfigCoupling:
    def test_card_forced_off_without_grounding(self):
        with patch.dict(
            os.environ,
            {
                "SPECIALIST_SYNTHESIS_CARD_ENABLED": "True",
                "SPECIALIST_GROUNDING_ENABLED": "False",
            },
            clear=False,
        ):
            cfg = RuntimeConfigState()
            assert cfg.SPECIALIST_SYNTHESIS_CARD_ENABLED is False

    def test_card_stays_on_with_grounding(self):
        with patch.dict(
            os.environ,
            {
                "SPECIALIST_SYNTHESIS_CARD_ENABLED": "True",
                "SPECIALIST_GROUNDING_ENABLED": "True",
            },
            clear=False,
        ):
            cfg = RuntimeConfigState()
            assert cfg.SPECIALIST_SYNTHESIS_CARD_ENABLED is True
            assert cfg.SPECIALIST_GROUNDING_ENABLED is True


_CARD_KEYS = {
    "synthesis_citations",
    "synthesis_dropped",
    "grounding_ok",
    "vol_band_low_pct",
    "vol_band_base_pct",
    "vol_band_high_pct",
    "vol_band_sigma_pct",
    "scenario_basis",
}


def _stub_report():
    return SimpleNamespace(
        symbol="NVDA",
        sentiment_score=61.0,
        recommendation="hold",
        confidence=0.4,
        escalate=False,
        escalate_reason="",
        reasons=["r1"],
        bull_case="Nvidia revenue hit a record.",
        bear_case="",
        investment_thesis="",
        synthesis_citations=[
            {
                "kind": "bull",
                "claim": "Nvidia revenue hit a record.",
                "headline_index": 1,
                "headline_text": "Nvidia revenue record",
            }
        ],
        synthesis_dropped=[
            {
                "kind": "bull",
                "claim": "Cerebras deal.",
                "reason": "entity 'Cerebras' not in any source",
            }
        ],
        grounding_ok=False,
        vol_band_low_pct=-8.94,
        vol_band_base_pct=0.0,
        vol_band_high_pct=8.94,
        vol_band_sigma_pct=8.94,
        scenario_basis="realized_vol_har",
    )


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Serializer Gate")
class TestSerializerGate:
    def test_card_keys_absent_when_flag_off(self):
        cfg = SimpleNamespace(
            REPORT_QUALITY_BADGE_ENABLED=False,
            REPORT_GENERATOR_V2_ENABLED=False,
            SPECIALIST_SYNTHESIS_CARD_ENABLED=False,
            SPECIALIST_FRESHNESS_ENABLED=False,
        )
        with patch.object(api_routes.config, "get_config", return_value=cfg):
            dto = api_routes._serialize_specialist_report("NVDA", _stub_report())
        assert _CARD_KEYS.isdisjoint(dto.keys()), (
            "flag OFF must not emit card keys: " f"{_CARD_KEYS & set(dto.keys())}"
        )

    def test_card_keys_present_and_typed_when_flag_on(self):
        cfg = SimpleNamespace(
            REPORT_QUALITY_BADGE_ENABLED=False,
            REPORT_GENERATOR_V2_ENABLED=False,
            SPECIALIST_SYNTHESIS_CARD_ENABLED=True,
            SPECIALIST_FRESHNESS_ENABLED=False,
        )
        with patch.object(api_routes.config, "get_config", return_value=cfg):
            dto = api_routes._serialize_specialist_report("NVDA", _stub_report())
        assert _CARD_KEYS.issubset(dto.keys())
        assert dto["grounding_ok"] is False
        assert dto["scenario_basis"] == "realized_vol_har"
        # P0-1: a 0.0 base survives (never `or`-masked).
        assert dto["vol_band_base_pct"] == 0.0
        assert dto["vol_band_high_pct"] == 8.94
        assert len(dto["synthesis_citations"]) == 1
        assert dto["synthesis_dropped"][0]["reason"].startswith("entity")
        # The dropped claim NEVER leaks into the displayed prose.
        assert "Cerebras" not in dto.get("bull_case", "")
