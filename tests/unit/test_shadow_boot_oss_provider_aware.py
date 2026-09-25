"""Bug A (0.3.5 hotfix): the OSS shadow-boot LLM pre-flight must be PROVIDER-AWARE.

The shipped desktop runs ``scripts/shadow_boot.oss.py`` (renamed to ``shadow_boot.py`` at
snapshot). Its old ``_check_gemini`` hard-failed a LIVE boot whenever ``GEMINI_API_KEY`` was
absent — REGARDLESS of ``LLM_PROVIDER=ollama`` — so an Ollama-only desktop could never switch
to live (it degraded straight back to paper, emitting the OTel reason "Gemini: Gemini check
failed"). The provider-aware ``_check_llm`` already existed in the Enterprise ``shadow_boot.py``
but was never ported to the ``.oss`` twin — a BORA parity gap (predates 0.3.1).

These tests lock in the port: with ``LLM_PROVIDER=ollama`` and Ollama reachable, a keyless LIVE
boot PASSES; the Gemini hard-fail asymmetry is preserved only when the provider actually is
gemini; and the check is registered as "LLM" (mirroring the Enterprise file), so the degrade
reason is no longer the misleading "Gemini: …".
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
# Master ships the OSS twin as shadow_boot.oss.py; the OSS snapshot renames it to shadow_boot.py
# (scripts/oss_make_snapshot.sh). Resolve to whichever is present so this test is correct in BOTH
# editions (the sibling test_shadow_boot_oss.py hard-codes the .oss path and would FileNotFound in
# the snapshot — this variant does not).
_OSS = _SCRIPTS / "shadow_boot.oss.py"
if not _OSS.is_file():
    _OSS = _SCRIPTS / "shadow_boot.py"


def _load():
    spec = importlib.util.spec_from_file_location("shadow_boot_oss_mod", str(_OSS))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


def test_ollama_provider_live_boot_passes_without_gemini_key(mod, monkeypatch):
    """Ollama-only desktop going LIVE with no Gemini key → must PASS (not degrade to paper).

    This is the exact operator failure: LLM_PROVIDER=ollama, GEMINI_API_KEY absent, live boot.
    The old _check_gemini returned False here → the live switch degraded straight back to paper.
    """
    monkeypatch.setattr(mod, "resolved_provider_name", lambda: "ollama")

    async def _reachable(*a, **k):
        return True

    monkeypatch.setattr(mod, "ollama_reachable", _reachable)
    monkeypatch.setattr(mod.config, "PAPER_TRADING", False, raising=False)
    monkeypatch.setattr(mod.config, "GEMINI_API_KEY", None, raising=False)
    assert asyncio.run(mod._check_llm()) is True


def test_ollama_unreachable_live_boot_hard_fails(mod, monkeypatch):
    """The safety asymmetry survives: NO LLM reachable (Ollama down) AND live → abort boot."""
    monkeypatch.setattr(mod, "resolved_provider_name", lambda: "ollama")

    async def _reachable(*a, **k):
        return False

    monkeypatch.setattr(mod, "ollama_reachable", _reachable)
    monkeypatch.setattr(mod.config, "PAPER_TRADING", False, raising=False)
    assert asyncio.run(mod._check_llm()) is False


def test_ollama_unreachable_paper_boot_degrades(mod, monkeypatch):
    """Paper boot with Ollama down → degrade (boot allowed), never abort."""
    monkeypatch.setattr(mod, "resolved_provider_name", lambda: "ollama")

    async def _reachable(*a, **k):
        return False

    monkeypatch.setattr(mod, "ollama_reachable", _reachable)
    monkeypatch.setattr(mod.config, "PAPER_TRADING", True, raising=False)
    assert asyncio.run(mod._check_llm()) is True


def test_gemini_provider_live_boot_without_key_still_hard_fails(mod, monkeypatch):
    """The Gemini hard-fail is preserved ONLY when the provider actually is gemini."""
    monkeypatch.setattr(mod, "resolved_provider_name", lambda: "gemini")
    monkeypatch.setattr(mod.config, "PAPER_TRADING", False, raising=False)
    monkeypatch.setattr(mod.config, "GEMINI_API_KEY", None, raising=False)
    assert asyncio.run(mod._check_llm()) is False


def test_gemini_provider_paper_boot_without_key_degrades(mod, monkeypatch):
    """Gemini provider, no key, paper → degrade (boot allowed)."""
    monkeypatch.setattr(mod, "resolved_provider_name", lambda: "gemini")
    monkeypatch.setattr(mod.config, "PAPER_TRADING", True, raising=False)
    monkeypatch.setattr(mod.config, "GEMINI_API_KEY", None, raising=False)
    assert asyncio.run(mod._check_llm()) is True


def test_check_registered_as_LLM_not_gemini(mod):
    """BORA parity: the runner registers the provider-aware "LLM" check (not "Gemini"),
    so a degrade reason reads "LLM: …" and the check honours LLM_PROVIDER."""
    src = inspect.getsource(mod.run_shadow_boot_result)
    assert (
        "_check_llm" in src
    ), "run_shadow_boot_result must call the provider-aware _check_llm"
    assert (
        '"LLM"' in src or "'LLM'" in src
    ), "the check must be registered under the name 'LLM'"
    assert '"Gemini"' not in src and "'Gemini'" not in src, (
        "the provider-unaware 'Gemini' check name must be gone (it produced the misleading "
        "'Gemini: Gemini check failed' degrade reason on Ollama desktops)"
    )


def test_no_literal_config_paper_trading_env_key(mod):
    """The .oss twin must not resurrect the os.environ.get("config.PAPER_TRADING") literal-key
    bug (an env var literally named "config.PAPER_TRADING" never exists → silent fallback). The
    Enterprise file already fixed this by reading config.PAPER_TRADING directly."""
    src = Path(_OSS).read_text(encoding="utf-8")
    assert (
        'os.environ.get("config.PAPER_TRADING"' not in src
    ), "the literal-key env read must be gone — read config.PAPER_TRADING directly"
    assert "os.environ.get('config.PAPER_TRADING'" not in src


def test_bora_parity_both_editions_provider_aware():
    """BORA (feedback_bora_stringent): the OSS twin and the Enterprise file must NOT drift on
    the LLM pre-flight — both expose a provider-aware ``_check_llm`` and register it as "LLM".
    """
    ent = _SCRIPTS / "shadow_boot.py"
    files = {_OSS}
    if ent.is_file():
        files.add(
            ent
        )  # in the OSS snapshot _OSS already IS shadow_boot.py → the set dedups
    for f in files:
        src = f.read_text(encoding="utf-8")
        assert (
            "async def _check_llm(" in src
        ), f"{f.name} missing provider-aware _check_llm"
        assert "resolved_provider_name" in src, f"{f.name} must resolve the provider"
        assert "ollama_reachable" in src, f"{f.name} must probe Ollama"
