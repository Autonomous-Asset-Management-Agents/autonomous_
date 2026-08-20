"""SEC M6 (INV-01): _clean_env treats an empty/whitespace env var as *unset* — it
returns the default (None), never "" — so Optional[SecretStr] config fields can
distinguish an unset secret from an empty one.
"""

import config


def test_clean_env_strips_a_real_value(monkeypatch):
    monkeypatch.setenv("AAA_M6_TEST", "  abc  ")
    assert config._clean_env("AAA_M6_TEST") == "abc"


def test_clean_env_empty_returns_default(monkeypatch):
    monkeypatch.setenv("AAA_M6_TEST", "")
    assert config._clean_env("AAA_M6_TEST") is None
    assert config._clean_env("AAA_M6_TEST", "fallback") == "fallback"


def test_clean_env_whitespace_returns_default(monkeypatch):
    monkeypatch.setenv("AAA_M6_TEST", "   ")
    assert config._clean_env("AAA_M6_TEST") is None
    assert config._clean_env("AAA_M6_TEST", "fallback") == "fallback"


def test_clean_env_unset_returns_default(monkeypatch):
    monkeypatch.delenv("AAA_M6_TEST", raising=False)
    assert config._clean_env("AAA_M6_TEST") is None
    assert config._clean_env("AAA_M6_TEST", "fallback") == "fallback"
