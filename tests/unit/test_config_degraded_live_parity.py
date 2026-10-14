"""LSR R2 (#2253) BORA parity: DEGRADED_LIVE exists byte-parallel in BOTH editions.

The desktop ships ``config.oss.py`` renamed to ``config.py`` (make-release.ps1). A flag that lives
only in the Enterprise ``config.py`` would be ``AttributeError`` on the desktop — exactly the
Finding-#12 divergence class. This test loads ``config.oss.py`` EXPLICITLY (importlib) so it can
never no-op against the Enterprise edition sitting at ``config.py``.
"""

import importlib.util
import os

_HERE = os.path.dirname(__file__)
_OSS_CONFIG_PATH = os.path.abspath(
    os.path.join(_HERE, os.pardir, os.pardir, "config.oss.py")
)
_ENT_CONFIG_PATH = os.path.abspath(
    os.path.join(_HERE, os.pardir, os.pardir, "config.py")
)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"Could not load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_oss_config_has_degraded_live_default_false():
    mod = _load(_OSS_CONFIG_PATH, "config_oss_deg")
    assert hasattr(
        mod, "DEGRADED_LIVE"
    ), "config.oss.py must define DEGRADED_LIVE (BORA parity)"
    assert mod.DEGRADED_LIVE is False


def test_enterprise_config_has_degraded_live_default_false():
    mod = _load(_ENT_CONFIG_PATH, "config_ent_deg")
    assert hasattr(mod, "DEGRADED_LIVE")
    assert mod.DEGRADED_LIVE is False


def test_both_editions_keep_live_enable_failed_reason():
    """The new flag sits next to the (now finally-read) LIVE_ENABLE_FAILED_REASON breadcrumb."""
    oss = _load(_OSS_CONFIG_PATH, "config_oss_deg2")
    ent = _load(_ENT_CONFIG_PATH, "config_ent_deg2")
    for mod in (oss, ent):
        assert hasattr(mod, "LIVE_ENABLE_FAILED_REASON")
        assert mod.LIVE_ENABLE_FAILED_REASON is None
