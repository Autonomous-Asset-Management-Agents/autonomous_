# tests/unit/test_panel_builder_uses_default_client.py
# #1907 — the offline panel builder must use the R10 default-client seam.
#
# THE DEFECT: scripts/build_full_universe_panel.py `_default_bars_fn` constructed
# `HistoricalDataProvider(api=None)`. An EXPLICITLY passed api always wins over the
# `_DEFAULT_API_SENTINEL` (data_provider.py:229-232) — that is the R10 contract, so
# the engine and tests can inject. But it means api=None BYPASSES
# `_default_data_client()` entirely: no Alpaca client is resolved from config, and
# the credential WARNING (§5.6) that the seam emits on missing keys never fires.
# Observed: a "full universe" build over 553 symbols returned in 133 ms with
# 0 rows and a clean log — an instant-empty panel that looked like a data gap.
#
# THE PIN: `_default_bars_fn` must let the sentinel default resolve the client
# (i.e. call `HistoricalDataProvider()` with NO api argument). Regression shape
# `api=None` is caught because `_default_data_client` is then never invoked.

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_databento(monkeypatch):
    """Keep these tests about the Alpaca-client seam only (mirrors
    test_data_provider_client_fallback.py)."""
    import core.data_provider as dp

    monkeypatch.setattr(dp, "DATABENTO_ENABLED", False, raising=False)


def _provider_behind(fetch):
    """Extract the HistoricalDataProvider the returned fetch closure captured."""
    import core.data_provider as dp

    return next(
        cell.cell_contents
        for cell in fetch.__closure__
        if isinstance(cell.cell_contents, dp.HistoricalDataProvider)
    )


class TestBuilderUsesTheR10Seam:
    def test_default_bars_fn_resolves_client_via_default_data_client(self, monkeypatch):
        """The pin: constructing the builder's provider MUST invoke the R10 seam.

        With the regression (`HistoricalDataProvider(api=None)`) the explicit
        None wins over _DEFAULT_API_SENTINEL and this spy is never called.
        """
        import core.data_provider as dp
        from scripts.build_full_universe_panel import _default_bars_fn

        calls = []
        resolved_client = object()

        def _spy_default_client():
            calls.append(True)
            return resolved_client

        monkeypatch.setattr(dp, "_default_data_client", _spy_default_client)

        fetch = _default_bars_fn()

        assert calls, (
            "_default_bars_fn bypassed _default_data_client() — an explicit "
            "api=None skips the R10 seam, so no client is resolved from config "
            "and no credential WARNING fires: the builder runs instant-empty "
            "(553 symbols / 133 ms / 0 rows) with a clean log"
        )
        assert len(calls) == 1
        assert _provider_behind(fetch).api is resolved_client, (
            "the provider must carry the client the seam resolved, "
            "not a hard-coded None"
        )

    def test_explicit_api_none_would_bypass_the_seam(self, monkeypatch):
        """Control: documents WHY the bug was silent. api=None is a legitimate
        injection for the engine/tests (explicit always wins), which is exactly
        why the builder must never pass it."""
        import core.data_provider as dp

        def _must_not_be_called():  # pragma: no cover - fails loudly if hit
            raise AssertionError("explicit api must win over the seam (R10)")

        monkeypatch.setattr(dp, "_default_data_client", _must_not_be_called)

        assert dp.HistoricalDataProvider(api=None).api is None
