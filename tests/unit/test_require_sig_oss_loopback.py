"""REQUIRE_SIG must default OFF for a loopback/desktop/OSS engine (no proxy in front) and ON only under
Cloud Run — so the from-source browser path (incl. `npm run desktop:dev` → serve_public_api.py) no longer
fail-fasts on a missing PROXY_ENGINE_SHARED_SECRET. Cloud Run (K_SERVICE) is always enforced (ADR-SEC-02).
"""

from core.auth import resolve_require_sig


def test_local_unset_defaults_off():
    # No K_SERVICE (loopback/desktop/OSS) and REQUIRE_SIG unset → OFF (there is no proxy to sign).
    assert resolve_require_sig({}) is False


def test_cloud_run_unset_defaults_on():
    # Cloud Run has the proxy in front → REQUIRE_SIG defaults ON.
    assert resolve_require_sig({"K_SERVICE": "aaa-api"}) is True


def test_explicit_true_wins_locally():
    assert resolve_require_sig({"REQUIRE_SIG": "true"}) is True


def test_explicit_false_wins_locally():
    assert resolve_require_sig({"REQUIRE_SIG": "false"}) is False


def test_cloud_run_forbids_explicit_false():
    # ADR-SEC-02: REQUIRE_SIG=false is forbidden in Cloud Run — re-forced ON.
    assert resolve_require_sig({"REQUIRE_SIG": "false", "K_SERVICE": "aaa-api"}) is True
