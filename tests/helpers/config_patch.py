"""Canonical config-patch helpers for tests (CI-Prevention rules R2 + R3).

Origin: CI failure report 2026-07-23 (PRs #2415 / #2418).
Binding documentation: docs/5_engineering_and_devops/CI_PREVENTION.md.

Why this exists
---------------
``get_config()`` is importable via (at least) three paths:

1. ``config`` — the root module (tests run with ``pythonpath = ["."]``)
2. ``ai_trading_bot.config`` — the package-qualified alias (when the repo
   root is on ``sys.path``, e.g. some CI jobs and IDE runners)
3. the *consumer module* — any module that did
   ``from config import get_config`` holds its **own reference**; patching
   ``config.get_config`` alone does NOT rebind it.

Tests that patch only one path go green locally and red in CI (or vice
versa). ``_patch_get_config`` patches all of them in one call.

``safe_iter_modules`` is the pydantic-safe idiom for scanning
``sys.modules``: pydantic 2.x installs lazy/proxy modules whose attribute
access can raise (``PydanticImportError``), and Python caches failed
relative imports as ``None`` entries — both must be skipped defensively.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Callable, Iterable, Iterator, Tuple, Union

__all__ = ["_patch_get_config", "safe_iter_modules"]

#: Canonical import paths of the runtime config module (rule R2).
_CANONICAL_CONFIG_MODULES: Tuple[str, ...] = ("config", "ai_trading_bot.config")


def _resolve_module(candidate: Union[str, types.ModuleType]) -> types.ModuleType | None:
    """Resolve a dotted name (or pass through a module object) via sys.modules.

    Deliberately does NOT import: an unloaded module cannot hold a stale
    ``get_config`` reference, so there is nothing to patch (and importing as
    a side effect of test setup would mask real import-order bugs).
    """
    if isinstance(candidate, types.ModuleType):
        return candidate
    return sys.modules.get(candidate)


def _patch_get_config(
    monkeypatch: Any,
    cfg_obj: Any,
    consumer_modules: Iterable[Union[str, types.ModuleType]] = (),
) -> Callable[[], Any]:
    """Patch ``get_config`` on ALL relevant import paths (rule R2).

    Parameters
    ----------
    monkeypatch:
        The pytest ``monkeypatch`` fixture (or a ``monkeypatch.context()``).
    cfg_obj:
        The config object (e.g. a ``SimpleNamespace`` or a real
        ``RuntimeConfigState``) that the patched ``get_config()`` returns.
    consumer_modules:
        Dotted module names and/or module objects of the code under test —
        every module that does ``from config import get_config`` MUST be
        listed here, otherwise it keeps its stale reference.

    Returns
    -------
    The factory installed as ``get_config`` (``lambda: cfg_obj``), so tests
    can assert identity if needed.

    Notes
    -----
    All setattr calls use ``raising=False``: an alias that is not loaded in
    the current environment (e.g. ``ai_trading_bot.config`` under the
    ``pythonpath="."`` test runner) is silently skipped — that asymmetry
    between local and CI environments is exactly the failure mode this
    helper neutralises.
    """

    def _factory() -> Any:
        return cfg_obj

    for candidate in (*_CANONICAL_CONFIG_MODULES, *consumer_modules):
        module = _resolve_module(candidate)
        if module is None:
            continue
        monkeypatch.setattr(module, "get_config", _factory, raising=False)

    return _factory


def safe_iter_modules(
    exclude_prefixes: Tuple[str, ...] = ("pydantic",),
) -> Iterator[Tuple[str, types.ModuleType]]:
    """Iterate ``sys.modules`` safely (rule R3).

    Skips:
    - modules whose name starts with any of ``exclude_prefixes`` (default:
      ``pydantic*`` — their lazy import machinery raises
      ``PydanticImportError`` on speculative attribute access),
    - ``None`` placeholders (cached failed relative imports),
    - entries whose retrieval raises for any reason.

    Yields ``(name, module)`` pairs. Iterates over a snapshot of the keys so
    callers may import modules (mutating ``sys.modules``) mid-scan.
    """
    for name in list(sys.modules):
        if any(name.startswith(prefix) for prefix in exclude_prefixes):
            continue
        try:
            module = sys.modules.get(name)
        except Exception:  # pragma: no cover - exotic dict subclasses
            continue
        if module is None:
            continue
        yield name, module
