# core/engine/__main__.py
# Epic 1.7 / PR-C: Ermöglicht `python -m core.engine` im Docker/Cloud-Run-Container.
# Delegate to api_routes.main() which starts uvicorn on ENGINE_HOST:ENGINE_PORT.

import os

import uvicorn

from core.engine.api_routes import app
from core.engine.live_trading_guard import assert_live_trading_config


def _resolve_host() -> str:
    """Resolve the uvicorn bind host (G0b, #1050 / AUDIT-008, INV-03).

    Default is LOOPBACK: the old implicit ``0.0.0.0`` default exposed the
    engine API to the entire LAN on desktop machines. Cloud Run/Docker are
    unaffected — every deploy artifact sets ``ENGINE_HOST=0.0.0.0``
    EXPLICITLY (Dockerfile.backend:17, cloudbuild-backend-deploy.yaml:41,
    cloudbuild.yaml:74, cloudbuild-engine-only.yaml:51, docker-compose*.yml;
    verified before this flip). Binding all interfaces is now an explicit
    operator decision, never an accident.
    """
    return os.environ.get("ENGINE_HOST", "127.0.0.1")


if __name__ == "__main__":
    import config

    if hasattr(config, "init_logging"):
        config.init_logging()

    import logging

    from core.engine.health import check_startup_preconditions

    check_startup_preconditions()

    if "K_SERVICE" in os.environ and os.path.exists(".env"):
        raise RuntimeError(
            "CRITICAL: Local .env found in prod container! Aborting to prevent secret leakage."
        )

    # 1. Shadow Boot Gate aufrufen (Fail-Fast vor Port Binding)
    import asyncio
    import sys

    from scripts.shadow_boot import run_shadow_boot

    success = asyncio.run(run_shadow_boot())
    if not success:
        logging.critical("CRITICAL: Shadow Boot FAILED. Bricht Container-Start ab.")
        sys.exit(0)  # [OSS] Exit 0 prevents infinite restart loop

    assert_live_trading_config()  # ML-1 pre-live gate: blocks if PAPER_TRADING=False without SIP

    # GTM-1 T3 (#1466): seal a pending first-run EULA acceptance onto the WORM chain (idempotent,
    # best-effort — never blocks boot). The desktop first-run wizard wrote the deliberate acceptance.
    try:
        from core.eula_seal import seal_eula_acceptance

        asyncio.run(seal_eula_acceptance())
    except Exception as exc:  # pragma: no cover - audit must not crash the boot
        logging.warning("EULA acceptance seal skipped: %s", exc)

    host = _resolve_host()
    # Cloud Run always sets PORT. If it's missing, fallback to ENGINE_PORT or 8001.
    port = int(os.environ.get("PORT", os.environ.get("ENGINE_PORT", "8001")))
    uvicorn.run(app, host=host, port=port)
