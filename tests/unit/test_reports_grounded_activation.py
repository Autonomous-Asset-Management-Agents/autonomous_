# tests/unit/test_reports_grounded_activation.py
"""Reports activation (2026-07-27): the grounded specialist cards are ON by default.

Root cause of "no reports shown": the console Reports page is card-only and gates on
`grounding_ok` (Direction A). `grounding_ok` stays null unless the specialist grounding
cluster is enabled — those flags shipped default-OFF and were NOT part of the round-table-v2
activation, so the backend served reports the frontend could never render.

This activation flips the 5 report/grounding flags ON by default in BOTH editions (BORA
parity), so the grounded synthesis runs and the cards render. The GPU cost is bounded by
`REPORT_REGISTRY_STANDALONE_ENABLED=True` (the registry covers the bounded ~33-symbol
`_CARD_REGISTRY_STOCKS` large-cap set, not the ~500 trading universe).

The whole cluster is display-only (SPECIALIST_ALPHA_WEIGHT stays 0.0) — it changes NO trading
decision. Each flag is env-reversible (`<FLAG>=False`) to the pre-activation dark path.
"""
import importlib.util
from pathlib import Path

from config import RuntimeConfigState

_ROOT = Path(__file__).resolve().parents[2]

_ACTIVATED = [
    "SPECIALIST_PROMPT_V2",
    "SPECIALIST_CARDS_ENABLED",
    "SPECIALIST_GROUNDING_ENABLED",
    "SPECIALIST_SYNTHESIS_CARD_ENABLED",
    "REPORT_REGISTRY_STANDALONE_ENABLED",
]


def _load_oss():
    spec = importlib.util.spec_from_file_location(
        "config_oss_reports", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enterprise_defaults_activated():
    cfg = RuntimeConfigState()
    for flag in _ACTIVATED:
        assert (
            getattr(cfg, flag) is True
        ), f"{flag} must default True (reports activation)"


def test_oss_defaults_activated():
    oss = _load_oss()
    for flag in _ACTIVATED:
        assert getattr(oss, flag) is True, f"{flag} must default True in OSS"


def test_bora_parity_enterprise_matches_oss():
    oss, ent = _load_oss(), RuntimeConfigState()
    for flag in _ACTIVATED:
        assert getattr(ent, flag) == getattr(
            oss, flag
        ), f"BORA parity broken for {flag}"


def test_synthesis_card_grounding_coupling_holds():
    # The hard-coupling validator must NOT force the card OFF now that grounding is ON.
    cfg = RuntimeConfigState()
    assert cfg.SPECIALIST_SYNTHESIS_CARD_ENABLED is True
    assert cfg.SPECIALIST_GROUNDING_ENABLED is True


def test_flags_are_env_reversible_to_dark_path(monkeypatch):
    # Each flag must remain OFF-able via env (rollback to the pre-activation dark path).
    for flag in _ACTIVATED:
        monkeypatch.setenv(flag, "False")
    cfg = RuntimeConfigState()
    for flag in _ACTIVATED:
        assert getattr(cfg, flag) is False, f"{flag} must be env-reversible to False"
