"""Unit tests for the canonical config-patch test helper (CI-Prevention R2/R3).

Proves that ``_patch_get_config`` covers all three import paths — including
the from-import consumer trap: a module that did ``from config import
get_config`` holds its own reference, which patching ``config.get_config``
alone does NOT rebind.

Origin: CI failure report 2026-07-23 (PRs #2415 / #2418); rules documented in
docs/5_engineering_and_devops/CI_PREVENTION.md.
"""

import sys
import types
from types import SimpleNamespace

import config
from tests.helpers.config_patch import _patch_get_config, safe_iter_modules


def _make_from_import_consumer(name: str) -> types.ModuleType:
    """Simulate a module that did ``from config import get_config``."""
    consumer = types.ModuleType(name)
    consumer.get_config = config.get_config  # bound-by-value at import time
    return consumer


class TestPatchGetConfig:
    def test_patches_root_config_module(self, monkeypatch):
        sentinel = SimpleNamespace(MARKER="root")
        _patch_get_config(monkeypatch, sentinel)
        assert config.get_config() is sentinel

    def test_from_import_consumer_sees_patch(self, monkeypatch):
        """The critical R2 case: from-import consumers hold a stale reference
        unless their module attribute is patched too."""
        consumer = _make_from_import_consumer("_ci_prev_fake_consumer")
        monkeypatch.setitem(sys.modules, "_ci_prev_fake_consumer", consumer)

        sentinel = SimpleNamespace(MARKER="consumer")
        _patch_get_config(
            monkeypatch, sentinel, consumer_modules=("_ci_prev_fake_consumer",)
        )

        # The from-import reference inside the consumer now yields the sentinel
        assert consumer.get_config() is sentinel
        # ... and the canonical path is patched as well
        assert config.get_config() is sentinel

    def test_consumer_module_object_accepted(self, monkeypatch):
        """consumer_modules accepts module objects, not just dotted names."""
        consumer = _make_from_import_consumer("_ci_prev_fake_consumer_obj")
        monkeypatch.setitem(sys.modules, "_ci_prev_fake_consumer_obj", consumer)

        sentinel = SimpleNamespace(MARKER="obj")
        _patch_get_config(monkeypatch, sentinel, consumer_modules=(consumer,))
        assert consumer.get_config() is sentinel

    def test_missing_consumer_module_is_tolerated(self, monkeypatch):
        """raising=False semantics: an unloaded/nonexistent consumer path must
        not blow up the test setup (mirrors monkeypatch.setattr raising=False)."""
        sentinel = SimpleNamespace(MARKER="tolerant")
        _patch_get_config(
            monkeypatch, sentinel, consumer_modules=("_ci_prev_does_not_exist",)
        )
        assert config.get_config() is sentinel

    def test_patch_reverted_after_monkeypatch_undo(self, monkeypatch):
        sentinel = SimpleNamespace(MARKER="undo")
        original = config.get_config
        with monkeypatch.context() as mp:
            _patch_get_config(mp, sentinel)
            assert config.get_config() is sentinel
        assert config.get_config is original

    def test_consumer_without_get_config_attr_gets_one(self, monkeypatch):
        """A consumer that imports get_config lazily (inside a function) has no
        module-level attribute yet — raising=False must still install one so a
        later ``module.get_config`` lookup resolves to the sentinel factory."""
        consumer = types.ModuleType("_ci_prev_lazy_consumer")
        monkeypatch.setitem(sys.modules, "_ci_prev_lazy_consumer", consumer)

        sentinel = SimpleNamespace(MARKER="lazy")
        _patch_get_config(
            monkeypatch, sentinel, consumer_modules=("_ci_prev_lazy_consumer",)
        )
        assert consumer.get_config() is sentinel


class TestSafeIterModules:
    def test_excludes_pydantic_modules(self):
        names = [name for name, _mod in safe_iter_modules()]
        assert not any(
            name == "pydantic" or name.startswith("pydantic") for name in names
        )

    def test_yields_loaded_modules(self):
        names = [name for name, _mod in safe_iter_modules()]
        assert "sys" in names or "types" in names

    def test_skips_none_placeholders(self, monkeypatch):
        """Python caches failed relative imports as None in sys.modules —
        iterating must not yield them."""
        monkeypatch.setitem(sys.modules, "_ci_prev_none_placeholder", None)
        for _name, mod in safe_iter_modules():
            assert mod is not None

    def test_survives_module_that_raises_on_access(self, monkeypatch):
        """R3: lazy/proxy modules (pydantic-style) may raise on attribute
        access — the scan must not propagate such errors."""

        class _ExplodingModule(types.ModuleType):
            def __getattr__(self, item):  # noqa: D105
                raise RuntimeError("PydanticImportError-style trap")

        exploding = _ExplodingModule("_ci_prev_exploding")
        monkeypatch.setitem(sys.modules, "_ci_prev_exploding", exploding)

        # Consuming the full generator (incl. touching each module's __name__)
        # must not raise.
        for _name, mod in safe_iter_modules():
            try:
                getattr(mod, "__name__", None)
            except Exception:  # pragma: no cover - defensive
                raise AssertionError("safe_iter_modules leaked a raising module")

    def test_custom_exclude_prefixes(self):
        names = [name for name, _mod in safe_iter_modules(exclude_prefixes=("sys",))]
        assert "sys" not in names
