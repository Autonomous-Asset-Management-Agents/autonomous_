# core/engine/__main__.py
# Epic 1.7 / PR-C: Ermöglicht `python -m core.engine` im Docker/Cloud-Run-Container.
# Delegate to api_routes.main() which starts uvicorn on ENGINE_HOST:ENGINE_PORT.

from core.engine.api_routes import app
from core.engine.live_trading_guard import assert_live_trading_config
import uvicorn
import os

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
    import sys
    import asyncio
    from scripts.shadow_boot import run_shadow_boot

    success = asyncio.run(run_shadow_boot())
    if not success:
        logging.critical("CRITICAL: Shadow Boot FAILED. Bricht Container-Start ab.")
        sys.exit(0)  # [OSS] Exit 0 prevents infinite restart loop in Docker compose!

    assert_live_trading_config()  # ML-1 pre-live gate: blocks if PAPER_TRADING=False without SIP

    host = os.environ.get("ENGINE_HOST", "0.0.0.0")
    # Cloud Run always sets PORT. If it's missing, fallback to ENGINE_PORT or 8080.
    port = int(os.environ.get("PORT", os.environ.get("ENGINE_PORT", "8080")))
    uvicorn.run(app, host=host, port=port)
