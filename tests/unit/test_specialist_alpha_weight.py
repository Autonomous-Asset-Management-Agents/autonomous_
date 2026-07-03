# tests/unit/test_specialist_alpha_weight.py
# #1346: the SpecialistAlphaAgent vote weight is config-gated. Default 0.0 keeps it
# DORMANT (byte-identical to today — clamped to 0, excluded from consensus). Setting
# SPECIALIST_ALPHA_WEIGHT restores a real weighted vote (once the registry is enabled +
# the #76 shadow gate clears). The os.environ read lives in config.py/config.oss.py
# (CODING_POLICY §2.10); the finance-core reads it via config.get_config().
import importlib
from types import SimpleNamespace

import config
import core.round_table.agents as agents


def _patch_weight(monkeypatch, value):
    monkeypatch.setattr(
        config, "get_config", lambda: SimpleNamespace(SPECIALIST_ALPHA_WEIGHT=value)
    )


def test_specialist_alpha_weight_helper_reads_config(monkeypatch):
    _patch_weight(monkeypatch, 0.0)
    assert agents._specialist_alpha_weight() == 0.0
    _patch_weight(monkeypatch, 0.55)
    assert agents._specialist_alpha_weight() == 0.55
    _patch_weight(monkeypatch, "garbage")
    assert agents._specialist_alpha_weight() == 0.0  # invalid -> dormant, never crash


def test_specialist_alpha_dormant_by_default(monkeypatch):
    _patch_weight(monkeypatch, 0.0)
    importlib.reload(agents)
    try:
        # default_weight 0.0 AND max_weight 0.0 -> the weight property clamps to 0
        assert agents.SpecialistAlphaAgent.default_weight == 0.0
        assert agents.SpecialistAlphaAgent.max_weight == 0.0
    finally:
        monkeypatch.undo()
        importlib.reload(agents)  # restore module to the default (dormant) state


def test_specialist_alpha_weight_restored_when_configured(monkeypatch):
    _patch_weight(monkeypatch, 0.55)
    importlib.reload(agents)
    try:
        assert agents.SpecialistAlphaAgent.default_weight == 0.55
        assert agents.SpecialistAlphaAgent.max_weight == 2.0  # now effective
    finally:
        monkeypatch.undo()
        importlib.reload(agents)  # restore module to the default (dormant) state
