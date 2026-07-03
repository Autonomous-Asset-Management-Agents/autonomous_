import asyncio
import logging
import os
import sys

# Flat OSS layout: shadow_boot.py lives in scripts/; project root is one level up.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config

# NOTE: secrets_loader is intentionally NOT imported in the OSS build.
# All credentials are read directly from environment variables or .env.oss.

if hasattr(config, "init_logging"):
    config.init_logging()
else:
    logging.basicConfig(
        level=logging.INFO, format="[SHADOW BOOT OSS] %(levelname)s: %(message)s"
    )

logger = logging.getLogger("shadow_boot")


async def _check_redis() -> bool:
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        logger.warning(
            "REDIS_URL is empty — skipping Redis pre-flight check (local mode)."
        )
        return True

    import redis.asyncio as aioredis

    use_tls = redis_url.startswith("rediss://")
    kwargs: dict = {"decode_responses": True}
    if use_tls:
        kwargs["ssl_cert_reqs"] = None

    try:
        async with aioredis.from_url(redis_url, **kwargs) as r:
            await r.ping()
        logger.info("Redis reachable: %s", redis_url)
        return True
    except Exception as exc:
        logger.error("Redis check failed: %s", exc)
        return False


# Sentinel value used by docker-compose.oss.yml when no real Alpaca keys are
# configured. Detected here to enable the documented "Offline / Shadow Boot"
# mode: the engine starts fully, ML agents run normally, no orders are placed.
_OFFLINE_MODE_SENTINEL = "offline_mode"


async def _check_alpaca() -> bool:
    import httpx

    if not config.API_KEY or not config.API_SECRET:
        logger.error("Alpaca API_KEY or API_SECRET not configured.")
        return False

    api_key_str = (
        config.API_KEY.get_secret_value()
        if hasattr(config.API_KEY, "get_secret_value")
        else config.API_KEY
    )
    api_secret_str = (
        config.API_SECRET.get_secret_value()
        if hasattr(config.API_SECRET, "get_secret_value")
        else config.API_SECRET
    )

    if (
        api_key_str == _OFFLINE_MODE_SENTINEL
        or api_secret_str == _OFFLINE_MODE_SENTINEL
    ):
        logger.warning(
            "Alpaca offline mode detected (ALPACA_API_KEY='offline_mode'). "
            "Broker check skipped — engine will start without order execution capability. "
            "To enable paper trading, set real Alpaca Paper-Trading keys in .env.oss."
        )
        return True

    url = f"{config.BASE_URL}/v2/account"
    headers = {
        "APCA-API-KEY-ID": str(api_key_str),
        "APCA-API-SECRET-KEY": str(api_secret_str),
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                logger.info(
                    "Alpaca API reachable and Auth valid (%s).", config.BASE_URL
                )
                return True
            elif resp.status_code == 429:
                logger.warning(
                    "Alpaca API reachable but Rate Limited (HTTP 429). Allowed since network is valid."
                )
                return True
            else:
                logger.error(
                    "Alpaca check failed: HTTP %s - %s", resp.status_code, resp.text
                )
                return False
    except Exception as e:
        logger.error("Alpaca check HTTP request failed: %s", e)
        return False


async def _check_gemini() -> bool:
    from google import genai

    if not config.GEMINI_API_KEY:
        # Use env var first, then fall back to the module-level config constant.
        # The env var wins so that docker-compose.oss.yml or .env.oss can override
        # the config default at runtime without rebuilding the image.
        paper_trading_env = os.environ.get(
            "config.PAPER_TRADING", str(config.PAPER_TRADING)
        )
        is_paper_trading = paper_trading_env.lower() == "true"

        if not is_paper_trading:
            logger.critical(
                "config.GEMINI_API_KEY not found AND config.PAPER_TRADING is False (live trading active). "
                "Aborting boot to prevent unguided live trades. "
                "Set config.PAPER_TRADING=True in .env.oss to start in Degraded Sentiment Mode."
            )
            return False
        else:
            logger.warning(
                "config.GEMINI_API_KEY not found. Booting in Degraded Sentiment Mode "
                "(paper trading only — GeminiSentimentAgent and NewsContextAgent disabled). "
                "Set config.GEMINI_API_KEY in .env.oss to enable full sentiment-augmented signals."
            )
            return True

    api_key_str = (
        config.GEMINI_API_KEY.get_secret_value()
        if hasattr(config.GEMINI_API_KEY, "get_secret_value")
        else config.GEMINI_API_KEY
    )
    client = genai.Client(api_key=api_key_str)

    def fetch():
        # Lightweight check for valid key and network route
        return client.models.get(model="models/gemini-2.5-flash")

    await asyncio.to_thread(fetch)
    logger.info("Gemini API reachable and Auth valid.")
    return True


async def _check_tft_models() -> None:
    """Diagnostic boot-verify of the provisioned TFT serving tree against its SHA-256
    manifest (model-provenance Issue 3). Dormant (no-op) without a manifest; logs a
    WARNING on mismatch. NEVER blocks boot — the per-load verify gate is the enforcement.
    """
    try:
        from smoke_test_tft_models import (
            _manifest_path,
            _models_root,
            verify_tft_provisioning,
        )

        root = _models_root()
        _ok, report = await asyncio.to_thread(
            verify_tft_provisioning, root, _manifest_path(root)
        )
        status = report.get("status")
        if status not in ("ok", "no-manifest-dormant"):
            logger.warning("TFT provisioning boot-verify: %s (%s)", status, report)
    except Exception as exc:
        logger.warning("TFT boot-verify skipped (non-fatal): %s", exc)


async def run_shadow_boot() -> bool:
    """
    Executes the 5-second timeout pre-flight checks.
    Returns True if infrastructure is healthy, False if the container should fail-fast.

    CI Bypass: When IS_CI=true, only Redis is checked (no real broker credentials needed).
    """
    logger.info("Starting Shadow Boot pre-flight checks...")

    # CI bypass: only verify Redis connectivity — Alpaca/Gemini creds are not available in CI.
    # This keeps fail-fast behavior intact in production while allowing the OSS stack to
    # boot successfully during automated smoke tests.
    if os.environ.get("IS_CI", "").lower() == "true":
        logger.warning(
            "IS_CI=true detected — skipping Alpaca and Gemini checks (CI mode)."
        )
        try:
            result = await asyncio.wait_for(_check_redis(), timeout=5.0)
            if result:
                logger.info("Shadow Boot CI mode: Redis OK. Boot allowed.")
                return True
            else:
                logger.error("Shadow Boot CI mode: Redis check failed.")
                return False
        except Exception as e:
            logger.error("Shadow Boot CI mode: Redis check crashed: %s", e)
            return False

    TIMEOUT = 5.0

    # Run all checks in parallel — max total wait time is 1x TIMEOUT (5s),
    # not 3x (15s) as with sequential execution.
    check_names = ["Redis", "Alpaca", "Gemini"]
    check_coros = [_check_redis(), _check_alpaca(), _check_gemini()]

    results = await asyncio.gather(
        *[asyncio.wait_for(c, timeout=TIMEOUT) for c in check_coros],
        return_exceptions=True,
    )

    success = True
    for name, result in zip(check_names, results):
        if isinstance(result, asyncio.TimeoutError):
            logger.error(
                "%s check timed out after %.0fs (blackhole suspected!)", name, TIMEOUT
            )
            success = False
        elif isinstance(result, Exception):
            logger.error("%s check crashed: %s", name, result)
            success = False
        elif not result:
            logger.error("%s check failed.", name)
            success = False
        else:
            logger.info("%s check OK.", name)

    # TFT provisioning boot-verify (model-provenance Issue 3) — diagnostic only, NEVER
    # affects boot success (the per-load verify gate enforces). Dormant without a manifest.
    await _check_tft_models()

    return success


if __name__ == "__main__":
    passed = asyncio.run(run_shadow_boot())
    if not passed:
        sys.exit(1)
    sys.exit(0)
