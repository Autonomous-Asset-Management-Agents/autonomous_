import pathlib

files = [
    "test_clean_weight_sizing_3284.py",
    "test_desktop_settings_passthrough.py",
    "test_displacement_session_cap.py",
    "test_earnings_guard_wiring.py",
    "test_regime_throttle_wiring_3361.py",
    "test_settings_deviation_ack_3151.py",
    "test_trading_settings_clamp.py",
    "test_trading_settings_registry_parity.py",
]
for f in files:
    p = pathlib.Path("tests/unit/" + f)
    t = p.read_text(encoding="utf-8")
    t = t.replace(
        "from settings import REGISTRY", "from core.trading_settings import REGISTRY"
    ).replace(
        'pytest.importorskip("settings")',
        'pytest.importorskip("core.trading_settings")',
    )
    p.write_text(t, encoding="utf-8")
