import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import List

# Flat OSS layout: shadow_boot.py lives in scripts/; project root is one level up.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config
from core.llm.health import (  # G4a-3: shared Ollama probe + provider resolution
    ollama_reachable,
    resolved_provider_name,
)

# NOTE: secrets_loader is intentionally NOT imported in the OSS build.
# All credentials are read directly from environment variables or .env.oss.

if hasattr(config, "init_logging"):
    config.init_logging()
else:
    logging.basicConfig(
        level=logging.INFO, format="[SHADOW BOOT OSS] %(levelname)s: %(message)s"
    )

logger = logging.getLogger("shadow_boot")


# LSR R2 (#2253): structured pre-flight outcome so the boot-degrade branch can tell a TERMINAL
# failure (write a compensating WORM disable → self-heal to paper) from a TRANSIENT one (keep the
# arm, retry next boot). A check may still return a bare bool — it is normalised to a transient
# failure (bias → transient; mis-flagging terminal→transient stays paper, which is the safe side).
@dataclass
class CheckOutcome:
    """One pre-flight check's outcome. ``terminal`` ⇒ heals never without operator action."""

    ok: bool
    terminal: bool = False
    reason: str = ""

    def __bool__(
        self,
    ) -> bool:  # bool-compatible so any legacy `if not out:` still reads ok
        return bool(self.ok)


@dataclass
class CheckFailure:
    """A named, classified pre-flight failure (terminal vs transient)."""

    name: str
    reason: str
    terminal: bool = False


@dataclass
class ShadowBootResult:
    """Structured pre-flight result. ``ok`` is the bool the thin wrapper returns."""

    ok: bool
    failures: List[CheckFailure] = field(default_factory=list)

    def __bool__(self) -> bool:  # bool-compatible for cloud/existing callers
        return bool(self.ok)


def _normalize_outcome(name: str, raw) -> CheckOutcome:
    """Coerce a check's return (``CheckOutcome`` or a bare bool) into a ``CheckOutcome``.

    A bare bool loses the terminal/transient signal, so a bool-``False`` is classified TRANSIENT
    (bias → transient). Only a real ``CheckOutcome(terminal=True)`` — e.g. Alpaca 401/403 — is
    terminal.
    """
    if isinstance(raw, CheckOutcome):
        return raw
    ok = bool(raw)
    return CheckOutcome(
        ok=ok, terminal=False, reason="" if ok else f"{name} check failed"
    )


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


async def _check_alpaca() -> CheckOutcome:
    import httpx

    # INC-1 (P0): read the account-SELECTED creds (config.ALPACA_*), NOT the paper
    # aliases (config.API_KEY/API_SECRET/BASE_URL). On a live desktop boot config.ALPACA_API_KEY
    # holds the live key while config.API_KEY still holds the PAPER key → the old pre-flight sent
    # PK… to the live endpoint → HTTP 401 → the live boot silently degraded to paper.
    if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
        # Not configured yet → transient (heals the moment the operator sets a key; keep the arm).
        logger.error("Alpaca ALPACA_API_KEY or ALPACA_SECRET_KEY not configured.")
        return CheckOutcome(
            ok=False, terminal=False, reason="Alpaca credentials not configured"
        )

    api_key_str = (
        config.ALPACA_API_KEY.get_secret_value()
        if hasattr(config.ALPACA_API_KEY, "get_secret_value")
        else config.ALPACA_API_KEY
    )
    api_secret_str = (
        config.ALPACA_SECRET_KEY.get_secret_value()
        if hasattr(config.ALPACA_SECRET_KEY, "get_secret_value")
        else config.ALPACA_SECRET_KEY
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
        return CheckOutcome(ok=True)

    url = f"{config.ALPACA_BASE_URL}/v2/account"
    headers = {
        "APCA-API-KEY-ID": str(api_key_str),
        "APCA-API-SECRET-KEY": str(api_secret_str),
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                logger.info(
                    "Alpaca API reachable and Auth valid (%s).", config.ALPACA_BASE_URL
                )
                return CheckOutcome(ok=True)
            elif resp.status_code == 429:
                logger.warning(
                    "Alpaca API reachable but Rate Limited (HTTP 429). Allowed since network is valid."
                )
                return CheckOutcome(ok=True)
            elif resp.status_code in (401, 403):
                # LSR R2 (#2253): auth REJECTED on the account-selected (live) creds — a rotated or
                # wrong key heals never on retry. TERMINAL → write a compensating WORM disable so
                # the next boot re-arms PAPER instead of silently re-arming live (#01/#10).
                logger.error(
                    "Alpaca auth REJECTED: HTTP %s — live credentials invalid/rotated "
                    "(terminal, will not self-heal).",
                    resp.status_code,
                )
                return CheckOutcome(
                    ok=False,
                    terminal=True,
                    reason=f"Alpaca auth rejected (HTTP {resp.status_code}) — live credentials invalid",
                )
            else:
                # 5xx / unexpected: reachable but unhealthy — a blip, not a bad key. TRANSIENT.
                logger.error(
                    "Alpaca check failed: HTTP %s - %s", resp.status_code, resp.text
                )
                return CheckOutcome(
                    ok=False,
                    terminal=False,
                    reason=f"Alpaca HTTP {resp.status_code} (transient)",
                )
    except Exception as e:
        # Network/timeout — transient by nature.
        logger.error("Alpaca check HTTP request failed: %s", e)
        return CheckOutcome(
            ok=False, terminal=False, reason=f"Alpaca network error: {e}"
        )


async def _check_llm() -> bool:
    """Provider-aware LLM pre-flight (G4a-3, ADR-014) — BORA parity with the Enterprise
    ``scripts/shadow_boot.py::_check_llm``.

    Bug A (0.3.5): the shipped desktop runs THIS (.oss) file, whose old ``_check_gemini`` hard-
    failed a LIVE boot whenever ``GEMINI_API_KEY`` was absent — even on an ``LLM_PROVIDER=ollama``
    desktop with no Gemini key at all — so the live switch degraded straight back to paper. The
    check now honours the resolved provider:

      * ``LLM_PROVIDER=ollama`` → probe ``GET {OLLAMA_BASE_URL}/api/tags``.
      * otherwise → Gemini (byte-compatible with the previous key check).

    Both paths keep the SAME degrade asymmetry: unreachable/no-key DEGRADES to paper
    (``return True``) and HARD-FAILS the boot (``return False``) only when Live Trading is active —
    because the real "unguided live trades" risk is NO LLM reachable, not "not Gemini".
    """
    provider = resolved_provider_name()
    if provider == "ollama":
        if await ollama_reachable():
            logger.info("Ollama API reachable.")
            return True
        # A desktop Ollama daemon may simply not be running yet → same asymmetry as the
        # Gemini key-missing path: degrade on paper, hard-fail on live.
        if not config.PAPER_TRADING:
            logger.critical(
                "Ollama unreachable AND Live Trading is active. "
                "Aborting boot to prevent unguided live trades."
            )
            return False
        logger.warning(
            "Ollama unreachable. Booting in Degraded Sentiment Mode (Paper Trading only)."
        )
        return True

    from google import genai

    if not config.GEMINI_API_KEY:
        # GAP fix (audit #1183, ported to .oss for Bug A): read the config module's boolean
        # DIRECTLY. The old code read an env var whose NAME was the literal string
        # config-dot-PAPER_TRADING (which never exists) → it always hit the silent fallback.
        if not config.PAPER_TRADING:
            logger.critical(
                "config.GEMINI_API_KEY not found AND Live Trading is active (LLM_PROVIDER=gemini). "
                "Aborting boot to prevent unguided live trades. "
                "Set config.PAPER_TRADING=True in .env.oss to start in Degraded Sentiment Mode."
            )
            return False
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


async def run_shadow_boot_result() -> ShadowBootResult:
    """Executes the 5-second timeout pre-flight checks and returns a STRUCTURED result.

    ``ShadowBootResult(ok, failures:[CheckFailure(name, reason, terminal)])`` so the boot-degrade
    branch (core/engine/__main__.py) can decide terminal (compensating WORM disable → self-heal to
    paper) vs transient (keep the arm, retry next boot). LSR R2 (#2253).

    CI Bypass: When IS_CI=true, only Redis is checked (no real broker credentials needed).
    """
    logger.info("Starting Shadow Boot pre-flight checks...")

    # CI bypass: only verify Redis connectivity — Alpaca/Gemini creds are not available in CI.
    # This keeps fail-fast behavior intact in production while allowing the OSS stack to
    # boot successfully during automated smoke tests.
    if os.environ.get("IS_CI", "").lower() == "true":
        logger.warning(
            "IS_CI=true detected — skipping Alpaca and LLM checks (CI mode)."
        )
        try:
            out = _normalize_outcome(
                "Redis", await asyncio.wait_for(_check_redis(), timeout=5.0)
            )
            if out.ok:
                logger.info("Shadow Boot CI mode: Redis OK. Boot allowed.")
                return ShadowBootResult(ok=True)
            logger.error("Shadow Boot CI mode: Redis check failed.")
            return ShadowBootResult(
                ok=False,
                failures=[
                    CheckFailure(
                        "Redis", out.reason or "Redis check failed", out.terminal
                    )
                ],
            )
        except Exception as e:
            logger.error("Shadow Boot CI mode: Redis check crashed: %s", e)
            return ShadowBootResult(
                ok=False, failures=[CheckFailure("Redis", f"Redis crashed: {e}", False)]
            )

    TIMEOUT = 5.0

    # Run all checks in parallel — max total wait time is 1x TIMEOUT (5s),
    # not 3x (15s) as with sequential execution.
    check_names = ["Redis", "Alpaca", "LLM"]
    check_coros = [_check_redis(), _check_alpaca(), _check_llm()]

    results = await asyncio.gather(
        *[asyncio.wait_for(c, timeout=TIMEOUT) for c in check_coros],
        return_exceptions=True,
    )

    failures: List[CheckFailure] = []
    for name, result in zip(check_names, results):
        if isinstance(result, asyncio.TimeoutError):
            logger.error(
                "%s check timed out after %.0fs (blackhole suspected!)", name, TIMEOUT
            )
            failures.append(
                CheckFailure(name, f"{name} timed out after {TIMEOUT}s", False)
            )
        elif isinstance(result, Exception):
            logger.error("%s check crashed: %s", name, result)
            failures.append(CheckFailure(name, f"{name} crashed: {result}", False))
        else:
            out = _normalize_outcome(name, result)
            if out.ok:
                logger.info("%s check OK.", name)
            else:
                logger.error("%s check failed.", name)
                failures.append(
                    CheckFailure(
                        name, out.reason or f"{name} check failed", out.terminal
                    )
                )

    # TFT provisioning boot-verify (model-provenance Issue 3) — diagnostic only, NEVER
    # affects boot success (the per-load verify gate enforces). Dormant without a manifest.
    await _check_tft_models()

    return ShadowBootResult(ok=(len(failures) == 0), failures=failures)


async def run_shadow_boot() -> bool:
    """Thin bool-compatible wrapper (cloud/existing callers byte-unaffected). LSR R2 (#2253)."""
    return (await run_shadow_boot_result()).ok


if __name__ == "__main__":
    passed = asyncio.run(run_shadow_boot())
    if not passed:
        sys.exit(1)
    sys.exit(0)
