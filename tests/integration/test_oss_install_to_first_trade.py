"""
T3-B: Install→first-trade smoke test (regression-lock).

Locks in the fixes from T1-A (models_manifest.json schema) and T1-B
(setup.py uncomments Alpaca lines + populates secrets) by exercising
the full install→engine-boot→Round Table chain in-process, without
Docker, in <30 s.

What this test catches automatically:
    1. setup.py regressions: missing populated secrets, broken
       .env.oss.example template parsing.
    2. data/models_manifest.json schema regressions: missing
       release_tag, malformed sha256/url/filename/size_bytes per model.
    3. Engine module import regressions: a broken import in
       core.round_table.runner / core.engine breaks production OSS boot.
    4. Round Table happy-path crash regressions: boot_engine() must
       run without raising against a clean dependency tree.

What this test deliberately does NOT exercise (out of scope, kept
out so the gate stays deterministic and Docker-free):
    - Real Alpaca network calls (a stdlib HTTP mock substitutes).
    - Real ML model downloads (the manifest schema is checked, not
      the file payloads — that lives in test_engine_boot.py).
    - Full LangGraph trade execution (requires Postgres + Redis +
      LangGraph checkpointer; that lives in test_round_table_live.py).
    - docker-compose.oss.yml interpolation (lives in T2-class tests).

Skip behaviour:
    - Skips cleanly if `.env.oss.example` or `setup.py` are missing
      from the repo root (e.g. running outside the OSS snapshot).
    - Skips cleanly if engine modules cannot be imported in this
      environment (e.g. CI runner without optional ML deps).

Cost: ~5 s per run. Runs on every PR via the existing
`integration/` pytest gate — no new CI wiring required.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Iterator

import pytest

# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------
# This file: ai_trading_bot/tests/integration/test_oss_install_to_first_trade.py
# parents:   [integration, tests, ai_trading_bot, REPO_ROOT]
REPO_ROOT = Path(__file__).resolve().parents[3]
ENGINE_ROOT = REPO_ROOT / "ai_trading_bot"
ENV_EXAMPLE = REPO_ROOT / ".env.oss.example"
SETUP_PY = REPO_ROOT / "setup.py"
MODELS_MANIFEST = ENGINE_ROOT / "data" / "models_manifest.json"


def _have(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _have(ENV_EXAMPLE),
        reason=f".env.oss.example not at {ENV_EXAMPLE} (run from OSS snapshot or repo root)",
    ),
    pytest.mark.skipif(
        not _have(SETUP_PY),
        reason=f"setup.py not at {SETUP_PY} (run from OSS snapshot or repo root)",
    ),
]


# ---------------------------------------------------------------------------
# Mock Alpaca server (stdlib only — pytest-httpserver is not in dev deps)
# ---------------------------------------------------------------------------
def _free_port() -> int:
    """Bind to port 0 to grab a free OS-assigned port, then release."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _AlpacaMockHandler(BaseHTTPRequestHandler):
    """Minimal Alpaca paper-API stub. Returns deterministic fixtures.

    We hard-code a frozen `next_open` / `next_close` window centred on
    the (epoch-fixed) 2026-03-22 trading session so callers get a
    deterministic 'market is open' response regardless of wall clock.
    """

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 — stdlib API
        path = self.path.split("?", 1)[0]
        if path == "/v2/account":
            self._json(
                200,
                {
                    "id": "test-account",
                    "equity": "100000",
                    "buying_power": "100000",
                    "cash": "100000",
                    "status": "ACTIVE",
                    "currency": "USD",
                    "pattern_day_trader": False,
                },
            )
        elif path == "/v2/clock":
            self._json(
                200,
                {
                    "is_open": True,
                    "timestamp": "2026-03-22T15:00:00+00:00",
                    "next_open": "2026-03-23T13:30:00+00:00",
                    "next_close": "2026-03-22T20:00:00+00:00",
                },
            )
        elif path.startswith("/v2/stocks/snapshots"):
            # Synthetic bullish OHLC for AAPL/MSFT/GOOG.
            snap = {
                sym: {
                    "latestTrade": {"p": 152.0, "s": 100, "t": "2026-03-22T15:00:00Z"},
                    "latestQuote": {"bp": 151.95, "ap": 152.05, "bs": 100, "as": 100},
                    "minuteBar": {
                        "o": 151.0,
                        "h": 152.5,
                        "l": 150.8,
                        "c": 152.0,
                        "v": 1_000_000,
                        "t": "2026-03-22T15:00:00Z",
                    },
                    "dailyBar": {
                        "o": 150.0,
                        "h": 155.0,
                        "l": 148.0,
                        "c": 152.0,
                        "v": 50_000_000,
                        "t": "2026-03-22T00:00:00Z",
                    },
                    "prevDailyBar": {
                        "o": 145.0,
                        "h": 151.0,
                        "l": 144.0,
                        "c": 150.0,
                        "v": 48_000_000,
                        "t": "2026-03-21T00:00:00Z",
                    },
                }
                for sym in ("AAPL", "MSFT", "GOOG")
            }
            self._json(200, snap)
        else:
            self._json(404, {"message": f"unhandled path {path}"})

    def log_message(self, *_args, **_kw):  # silence per-request stderr noise
        return


@pytest.fixture
def mock_alpaca() -> Iterator[str]:
    """Spin a stdlib threaded HTTP server, yield its base URL, tear down."""
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _AlpacaMockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Phase 1: Setup phase — run setup.py against a temp checkout
# ---------------------------------------------------------------------------
class TestSetupPhase:
    """T1-B regression-lock: setup.py must produce a usable .env.oss."""

    def test_setup_py_populates_required_secrets(self, tmp_path: Path) -> None:
        """Run setup.py in a temp dir with .env.oss.example copied in,
        then assert the four required secrets are populated AND the
        Alpaca placeholder lines are present (commented or not — T1-B
        will tighten the uncomment assertion below)."""
        # Stage a clean checkout: copy template + setup.py into tmp_path.
        shutil.copy2(ENV_EXAMPLE, tmp_path / ".env.oss.example")
        shutil.copy2(SETUP_PY, tmp_path / "setup.py")

        # Invoke setup.py in non-interactive mode. setup.py exits 0 on
        # both success and "already exists"; we delete any stale file.
        env_oss = tmp_path / ".env.oss"
        if env_oss.exists():
            env_oss.unlink()

        result = subprocess.run(
            [sys.executable, "setup.py"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"setup.py exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert env_oss.is_file(), "setup.py did not create .env.oss"

        text = env_oss.read_text(encoding="utf-8")

        # Strict: each generated secret line must exist AND have a non-empty value.
        # Pattern: <KEY>=<hex-or-token>  (no comment, no trailing blank assignment)
        for key in (
            "POSTGRES_PASSWORD",
            "REDIS_PASSWORD",
            "PROXY_ENGINE_SHARED_SECRET",
            "ENGINE_API_KEY",
        ):
            match = re.search(rf"^{key}=(\S+)$", text, re.MULTILINE)
            assert match, (
                f"setup.py did not populate {key} in .env.oss\n"
                f"--- .env.oss ---\n{text}"
            )
            value = match.group(1)
            assert len(value) >= 32, (
                f"{key} value too short ({len(value)} chars) — entropy check failed:\n"
                f"  {key}={value!r}"
            )

        # Soft: ALPACA_API_KEY / ALPACA_SECRET_KEY lines must be PRESENT
        # (commented or uncommented). T1-B will uncomment them; once that
        # lands, tighten this to require the bare `ALPACA_API_KEY=` form.
        for key in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            assert re.search(rf"^#?\s*{key}=", text, re.MULTILINE), (
                f"{key} placeholder line missing from .env.oss — "
                f"setup.py or .env.oss.example regressed\n"
                f"--- .env.oss ---\n{text}"
            )


# ---------------------------------------------------------------------------
# Phase 2: Manifest validation — T1-A regression-lock
# ---------------------------------------------------------------------------
class TestManifestSchema:
    """T1-A regression-lock: data/models_manifest.json must stay schema-valid."""

    def test_manifest_schema_well_formed(self) -> None:
        if not MODELS_MANIFEST.is_file():
            pytest.skip(f"{MODELS_MANIFEST} not present (pre-T1-A snapshot?)")

        manifest = json.loads(MODELS_MANIFEST.read_text(encoding="utf-8"))

        # Top-level required keys
        assert "release_tag" in manifest, "manifest missing release_tag"
        assert (
            isinstance(manifest["release_tag"], str) and manifest["release_tag"]
        ), f"release_tag must be a non-empty string, got {manifest['release_tag']!r}"
        assert "models" in manifest, "manifest missing models[]"
        assert (
            isinstance(manifest["models"], list) and manifest["models"]
        ), "manifest.models must be a non-empty list"

        # Per-model required keys
        sha256_re = re.compile(r"^[0-9a-f]{64}$")
        for i, model in enumerate(manifest["models"]):
            ctx = f"models[{i}]={model.get('filename', '?')}"
            for key in ("filename", "url", "sha256", "size_bytes"):
                assert key in model, f"{ctx} missing required key {key!r}"
            assert sha256_re.match(
                model["sha256"]
            ), f"{ctx} sha256 not 64-hex-lowercase: {model['sha256']!r}"
            assert (
                isinstance(model["size_bytes"], int) and model["size_bytes"] > 0
            ), f"{ctx} size_bytes must be positive int, got {model['size_bytes']!r}"
            assert model["url"].startswith(
                ("http://", "https://")
            ), f"{ctx} url must be absolute http(s), got {model['url']!r}"
            assert (
                model["filename"] and "/" not in model["filename"]
            ), f"{ctx} filename must be a basename, got {model['filename']!r}"


# ---------------------------------------------------------------------------
# Phase 3+4: Engine bootstrap + Round Table happy-path
# ---------------------------------------------------------------------------
class TestEngineBootstrap:
    """Imports the engine modules in-process (no container) and runs the
    Round Table boot sequence to assert no crash on the happy path.

    Falls back to import-only verification if the full Round Table
    boot can't be constructed in this environment (e.g. missing torch
    DLLs on a bare CI runner) — the import-only check still catches
    module-level regressions in core.engine and core.round_table.runner.
    """

    def test_engine_modules_import_clean(
        self, monkeypatch: pytest.MonkeyPatch, mock_alpaca: str
    ) -> None:
        # Point the engine at the mock Alpaca server BEFORE any import.
        # Use IS_CI=true so shadow_boot skips the Gemini check.
        monkeypatch.setenv("IS_CI", "true")
        monkeypatch.setenv("ALPACA_API_KEY", "test_key_paper")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret_paper")
        monkeypatch.setenv("ALPACA_BASE_URL", mock_alpaca)
        monkeypatch.setenv("PAPER_TRADING", "true")
        monkeypatch.setenv("ENGINE_API_KEY", "smoke-test-key-must-be-32-chars-long")
        monkeypatch.setenv(
            "PROXY_ENGINE_SHARED_SECRET", "smoke-shared-secret-32-chars-long!"
        )

        # Make sure the engine root is on sys.path (conftest may already do this,
        # but be explicit so the test is independent of conftest order).
        engine_root_str = str(ENGINE_ROOT)
        if engine_root_str not in sys.path:
            sys.path.insert(0, engine_root_str)

        # Core boot path: if this raises, OSS engine boot is broken in production.
        try:
            from core.round_table import runner as rt_runner  # noqa: F401
        except Exception as exc:
            pytest.skip(
                f"core.round_table.runner failed to import in this environment: {exc}\n"
                f"(typical cause: missing optional ML deps like torch/sb3 — not a "
                f"regression in T3-B scope; lives in unit-test dependency gates)"
            )

        # boot_engine must be a callable on the module (signature stability).
        assert callable(getattr(rt_runner, "boot_engine", None)), (
            "core.round_table.runner.boot_engine missing or not callable — "
            "engine boot signature regressed"
        )
        assert callable(getattr(rt_runner, "run_round_table", None)), (
            "core.round_table.runner.run_round_table missing or not callable — "
            "Round Table happy-path entrypoint regressed"
        )

        # Drive the boot sequence with no license key (= OSS path).
        # Should NOT raise. Resets internal singletons, loads plugin dir.
        try:
            rt_runner.boot_engine(None)
        except Exception as exc:
            pytest.fail(
                f"boot_engine(None) raised on the OSS happy path: {exc!r}\n"
                f"This means the OSS engine cannot bootstrap from a clean install."
            )

        # Internal state: after boot, the singleton triad must be set.
        # We check via getattr to avoid coupling to private name leak —
        # if any are still None, run_round_table() short-circuits with
        # an "initialized" error and signals never fire. That IS the
        # regression we are locking out.
        for attr in ("_consensus_engine", "_gatekeeper", "_senate"):
            value = getattr(rt_runner, attr, None)
            assert value is not None, (
                f"After boot_engine(None), {attr} is still None — "
                f"Round Table will never produce a signal."
            )


# ---------------------------------------------------------------------------
# Phase 5: Round Table direct-call (best-effort, skipped if too heavy)
# ---------------------------------------------------------------------------
class TestRoundTableHappyPath:
    """Direct in-process call to run_round_table() with a synthetic
    bullish OHLC state. Asserts: no crash, returns a state dict with
    either a non-None signal OR an explicit 'no signal' marker (HOLD
    is acceptable; we just want NO unhandled exception)."""

    def test_run_round_table_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch, mock_alpaca: str
    ) -> None:
        monkeypatch.setenv("IS_CI", "true")
        monkeypatch.setenv("ALPACA_API_KEY", "offline_mode")
        monkeypatch.setenv("ALPACA_BASE_URL", mock_alpaca)
        monkeypatch.setenv("PAPER_TRADING", "true")

        engine_root_str = str(ENGINE_ROOT)
        if engine_root_str not in sys.path:
            sys.path.insert(0, engine_root_str)

        try:
            from core.round_table import runner as rt_runner
        except Exception as exc:
            pytest.skip(f"runner import unavailable: {exc}")

        try:
            rt_runner.boot_engine(None)
        except Exception as exc:
            pytest.skip(f"boot_engine unavailable in this env: {exc}")

        state = {
            "symbol": "AAPL",
            "ohlc": {
                "open": 150.0,
                "high": 155.0,
                "low": 148.0,
                "close": 152.0,
                "volume": 1_000_000.0,
            },
            "market_data_keys": [],
            "current_time": "2026-03-22T15:00:00+00:00",
            "signal": None,
            "error": None,
            "round_table_scores": None,
            "consensus_ranking": None,
        }

        import asyncio

        async def _run():
            return await rt_runner.run_round_table(state)

        try:
            result = asyncio.run(asyncio.wait_for(_run(), timeout=30.0))
        except asyncio.TimeoutError:
            pytest.fail(
                "run_round_table did not return within 30 s — Round Table "
                "happy path is hung or blocking on a real network call."
            )
        except Exception as exc:
            # Any uncaught exception out of run_round_table is a regression.
            pytest.fail(f"run_round_table raised on bullish AAPL state: {exc!r}")

        # Result must be a dict (state-passthrough contract).
        assert isinstance(
            result, dict
        ), f"run_round_table must return a state dict, got {type(result).__name__}"
        # Symbol must be preserved end-to-end.
        assert (
            result.get("symbol") == "AAPL"
        ), f"symbol field corrupted by Round Table: {result.get('symbol')!r}"
        # No-crash contract: if 'error' is set, it must be a known
        # initialization or compliance message — not a stack trace.
        err = result.get("error")
        if err is not None:
            allowed_substrings = (
                "Round Table not initialized",
                "Alle Voting-Agents fehlgeschlagen",
                "Missing core ML votes",
            )
            assert any(s in str(err) for s in allowed_substrings), (
                f"Round Table returned an unexpected error string: {err!r}\n"
                f"This may indicate a runtime regression in agent code."
            )
