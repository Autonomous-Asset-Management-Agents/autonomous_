import os
import sys
import asyncio
import logging

# Flat OSS layout: shadow_boot.py lives in scripts/; project root is one level up.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import (
    BASE_URL,
    API_KEY,
    API_SECRET,
    GEMINI_API_KEY,
)
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
    import redis.asyncio as aioredis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
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
    import aiohttp

    if not API_KEY or not API_SECRET:
        logger.error("Alpaca API_KEY or API_SECRET not configured.")
        return False

    # Offline / Shadow Boot mode: docker-compose.oss.yml sets ALPACA_API_KEY=offline_mode
    # as the default. Sending a real HTTP request with this sentinel would cause a 401
    # and block the boot — instead, skip the broker check and log a clear warning.
    if API_KEY == _OFFLINE_MODE_SENTINEL or API_SECRET == _OFFLINE_MODE_SENTINEL:
        logger.warning(
            "Alpaca offline mode detected (ALPACA_API_KEY='offline_mode'). "
            "Broker check skipped — engine will start without order execution capability. "
            "To enable paper trading, set real Alpaca Paper-Trading keys in .env.oss."
        )
        return True

    url = f"{BASE_URL}/v2/account"
    headers = {
        "APCA-API-KEY-ID": API_KEY,
        "APCA-API-SECRET-KEY": API_SECRET,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                logger.info("Alpaca API reachable and Auth valid (%s).", BASE_URL)
                return True
            elif resp.status == 429:
                logger.warning(
                    "Alpaca API reachable but Rate Limited (HTTP 429). Allowed since network is valid."
                )
                return True
            else:
                text = await resp.text()
                logger.error("Alpaca check failed: HTTP %s - %s", resp.status, text)
                return False


async def _check_gemini() -> bool:
    from google import genai

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not found.")
        return False

    client = genai.Client(api_key=GEMINI_API_KEY)

    def fetch():
        # Lightweight check for valid key and network route
        return client.models.get(model="models/gemini-2.5-flash")

    await asyncio.to_thread(fetch)
    logger.info("Gemini API reachable and Auth valid.")
    return True


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

    return success


if __name__ == "__main__":
    passed = asyncio.run(run_shadow_boot())
    if not passed:
        sys.exit(1)
    sys.exit(0)
