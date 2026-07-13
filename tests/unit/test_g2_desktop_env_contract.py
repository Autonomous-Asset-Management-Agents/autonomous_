"""G2 (#1050): CI gate for the desktop shell's mandatory engine-env contract.

The Electron shell (desktop/electron/native-engine-manager.cjs) spawns the
engine subprocess and is the ONE place that makes the desktop edition safe:
loopback bind, shadow mode, no auto-start, SQLite/LocalState switches, per-user
data dir, CORS allow-list, engine API key. The epic (Rev 3.2) mandates that
"each value is CI-grep-gated" — this module IS that gate. It runs in the
existing Python CI (no Node toolchain required) and fails if any mandatory
entry is removed or weakened in the JS source.

Scope note: behavioral JS tests (spawn flow, boot-failure parsing) live in
desktop/electron/__tests__/ and run via `node --test`; THIS file pins the
security-relevant literal contract so a silent edit cannot pass Python CI.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MANAGER = _REPO_ROOT / "desktop" / "electron" / "native-engine-manager.cjs"
_MAIN = _REPO_ROOT / "desktop" / "electron" / "main.cjs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class DesktopWorkspaceExists(unittest.TestCase):
    def test_manager_module_exists(self):
        self.assertTrue(
            _MANAGER.is_file(),
            f"G2 contract: {_MANAGER} missing — the desktop shell must live in "  # noqa: E501
            "desktop/electron (epic #1050 G2).",
        )

    def test_workspace_is_isolated_from_root_package_json(self):
        # Dual-Design B: the desktop workspace brings its OWN private
        # package.json; the repo-root package.json (the existing Vite web
        # console) must not be coupled to it via npm workspaces — the cloud
        # console build stays untouched by G2.
        import json

        desktop_pkg = json.loads(
            (_REPO_ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(desktop_pkg.get("name"), "aaagents-desktop")
        self.assertIs(desktop_pkg.get("private"), True)

        root_pkg_path = _REPO_ROOT / "package.json"
        if root_pkg_path.is_file():
            root_pkg = json.loads(root_pkg_path.read_text(encoding="utf-8"))
            self.assertNotIn(
                "desktop",
                json.dumps(root_pkg.get("workspaces", [])),
                "root package.json must not adopt desktop/ as an npm workspace",  # noqa: E501
            )


class MandatoryEnvBlock(unittest.TestCase):
    """Every entry of the epic's mandatory desktop env block, grep-gated."""

    @classmethod
    def setUpClass(cls):
        if not _MANAGER.is_file():
            raise unittest.SkipTest("manager missing — covered by existence test")
        cls.src = _read(_MANAGER)

    def _has(self, pattern: str, why: str):
        self.assertRegex(self.src, pattern, f"mandatory env contract violated: {why}")

    def test_loopback_bind(self):
        # SEC boundary: LAN exposure of the trading API. Unconditional, even
        # after G0b flips the engine default — defense in depth on every spawn.  # noqa: E501
        self._has(
            r'env\.ENGINE_HOST\s*=\s*"127\.0\.0\.1"',
            "ENGINE_HOST must be forced to 127.0.0.1 on every spawn",
        )

    def test_paper_trading_is_the_worm_gated_live_switch(self):
        # LIVE-1 T1 (#1424) + BORA: PAPER_TRADING is the ONE live gate — the same mechanism as the  # noqa: E501
        # cloud edition (it also drives ALPACA_BASE_URL paper-api↔api, config.py:53). It is the  # noqa: E501
        # confirmed-live ternary, DEFAULTING to "true" (the Alpaca PAPER account — fake money,  # noqa: E501
        # fail-closed) and is "false" (real money) ONLY when verifyAuditChain confirms an un-revoked  # noqa: E501
        # Art.-14 WORM live-enablement (T4). A live boot is impossible without that verified record.  # noqa: E501
        self._has(
            r'env\.PAPER_TRADING\s*=\s*\w+\s*\?\s*"false"\s*:\s*"true"',
            "PAPER_TRADING must be the confirmed-live ternary defaulting to true (fail-closed paper)",  # noqa: E501
        )
        # The live (false) branch must be gated on the audit-chain verifier, not a bare flag.  # noqa: E501
        self._has(
            r"verifyAuditChain\s*\(",
            "the PAPER_TRADING live branch must be gated on verifyAuditChain (WORM verifier)",  # noqa: E501
        )
        # NEGATIVE: PAPER_TRADING must never be an UNCONDITIONAL "false" (no ungated live trading).  # noqa: E501
        self.assertNotRegex(
            self.src,
            r'env\.PAPER_TRADING\s*=\s*"false"\s*;',
            "PAPER_TRADING must never be an unconditional false (fail-closed default required)",  # noqa: E501
        )

    def test_shadow_mode_forced_off_honest_paper(self):
        # BORA + honesty: paper trading round-trips to the Alpaca PAPER account (fake money) and is  # noqa: E501
        # already safe — it needs NO shadow interception. SHADOW_MODE (a pre-broker bypass) is NOT a  # noqa: E501
        # paper safety gate and was a desktop-only divergence from the cloud. It is forced OFF so  # noqa: E501
        # paper AND live both execute honestly; the single live gate is PAPER_TRADING above.  # noqa: E501
        self._has(
            r'env\.SHADOW_MODE\s*=\s*"false"',
            "SHADOW_MODE must be forced off (honest paper execution; PAPER_TRADING is the gate)",  # noqa: E501
        )
        # NEGATIVE: SHADOW_MODE must never be "true" — the dishonest shadow-sim (paper that never  # noqa: E501
        # reaches the broker) the cloud edition never uses.
        self.assertNotRegex(
            self.src,
            r'env\.SHADOW_MODE\s*=\s*"true"',
            "SHADOW_MODE must never be forced true (dishonest shadow-sim; use PAPER_TRADING)",  # noqa: E501
        )

    def test_live_boot_forces_hitl_enabled(self):
        # EU AI Act Art. 14: a live boot (PAPER_TRADING=False) MUST carry HITL_ENABLED — config.py  # noqa: E501
        # _enforce_hitl_boot_gate raises otherwise. The shell forces it on the verified-live path,  # noqa: E501
        # so live trading can never run without human-in-the-loop oversight.
        self._has(
            r'env\.HITL_ENABLED\s*=\s*"true"',
            "a live boot must force HITL_ENABLED=true (Art. 14 boot gate)",
        )

    def test_auto_start_strategy_false(self):
        # config.py defaults AUTO_START_STRATEGY to True — the desktop MUST
        # override it or the strategy auto-trades on first boot.
        self._has(
            r'env\.AUTO_START_STRATEGY\s*=\s*\w+\s*\?\s*"false"\s*:\s*"true"',
            "AUTO_START_STRATEGY must be forced to false on live (env default is True!)",  # noqa: E501
        )

    def test_require_sig_false(self):
        # ADR-SEC-02 local bypass; Cloud Run enforces true via K_SERVICE guard.
        self._has(
            r'env\.REQUIRE_SIG\s*=\s*"false"',
            "REQUIRE_SIG=false (no proxy HMAC on the loopback desktop)",
        )

    def test_sqlite_switch(self):
        self._has(
            r'env\.DATABASE_URL\s*=\s*""',
            'DATABASE_URL="" selects the SQLite/aiosqlite path',
        )

    def test_local_state_switch_is_redis_url(self):
        # main's switch is an EMPTY REDIS_URL (core/redis_client.py:43), not the  # noqa: E501
        # bundle's REDIS_DISABLED flag.
        self._has(
            r'env\.REDIS_URL\s*=\s*""',
            'REDIS_URL="" selects LocalStateClient (main-native switch)',
        )
        self.assertNotRegex(
            self.src,
            r"env\.REDIS_DISABLED\s*=",
            "bundle-only REDIS_DISABLED must not be assigned on main",
        )

    def test_deployment_mode_local(self):
        self._has(
            r'env\.DEPLOYMENT_MODE\s*=\s*"LOCAL"',
            "DEPLOYMENT_MODE=LOCAL gates the desktop-only code paths",
        )

    def test_user_data_dir(self):
        self._has(
            r"env\.AAA_USER_DATA_DIR\s*=",
            "AAA_USER_DATA_DIR must be set (fresh installs start clean)",
        )

    def test_senate_log_dir_anchored_in_user_data(self):
        # The desktop boots LocalJSONAuditLogger (runner.py:149), which reads
        # SENATE_LOG_DIR at instance time (senate_log.py:96) and would otherwise  # noqa: E501
        # write audit_log_<date>.jsonl to a CWD-relative `oss_audit_logs` under
        # ai_trading_bot/ — read-only in a Program Files install. The shell must  # noqa: E501
        # anchor it under the per-user data dir so (a) audit logs persist and
        # (b) the console's audit-chain reader (audit-chain.cjs::resolveAuditDir)  # noqa: E501
        # resolves the SAME path and the Audit Chain page is not empty.
        self._has(
            r'env\.SENATE_LOG_DIR\s*=\s*path\.join\(\s*userDataDir\s*,\s*"cloud_fallback_logs"\s*\)',  # noqa: E501
            "SENATE_LOG_DIR must be <userDataDir>/cloud_fallback_logs so writer "  # noqa: E501
            "(LocalJSONAuditLogger) and reader (audit-chain.cjs) agree",
        )

    def test_cors_both_vars_with_renderer_origin(self):
        # The engine reads ALLOWED_ORIGINS (api_routes.py:182), the public proxy  # noqa: E501
        # reads CORS_ALLOWED_ORIGINS (serve_public_api.py:347). Both must be the  # noqa: E501
        # renderer-origin list — value-pinned, so a silent regression to a
        # wildcard or literal cannot pass this gate.
        self._has(
            r"const origins\s*=\s*\[this\.rendererOrigin",
            "origins list must be built from this.rendererOrigin",
        )
        self._has(
            r"env\.ALLOWED_ORIGINS\s*=\s*origins\b",
            "ALLOWED_ORIGINS must be the renderer-origin list, not a literal",
        )
        self._has(
            r"env\.CORS_ALLOWED_ORIGINS\s*=\s*origins\b",
            "CORS_ALLOWED_ORIGINS must be the renderer-origin list (proxy parity)",  # noqa: E501
        )
        self.assertNotRegex(
            self.src,
            r'ALLOWED_ORIGINS\s*=\s*"\*"',
            "wildcard CORS must never be assigned on the desktop spawn",
        )

    def test_engine_api_key_injected(self):
        # require_engine_key is 503-fail-closed without ENGINE_API_KEY — the
        # shell must inject a key or every console call dies.
        self._has(
            r"env\.ENGINE_API_KEY\s*=",
            "ENGINE_API_KEY must be injected into the engine subprocess",
        )

    def test_xai_agent_core_enabled(self):
        # XAI-T9a (#1401): the desktop OSS build enables the glass-box agent-core  # noqa: E501
        # so /chat routes through the 4-domain Zero-Hallucination router instead  # noqa: E501
        # of the generic single-prompt chat. Cloud never routes through this
        # manager, so it stays OFF (byte-identical) there.
        self._has(
            r'env\.XAI_AGENT_CORE\s*=\s*"1"',
            "XAI_AGENT_CORE must default to '1' on the desktop spawn (glass-box chat)",  # noqa: E501
        )

    def test_app_version_stamped_for_telemetry(self):
        # #1940: the shell stamps the desktop app version so OTel/heartbeat report the
        # REAL running version — core.telemetry.get_service_version prefers AAA_APP_VERSION
        # over GIT_COMMIT (never set on the desktop). Grep-gated so a silent edit that drops
        # the stamp cannot pass Python CI.
        self._has(
            r"env\.AAA_APP_VERSION\s*=",
            "AAA_APP_VERSION must be stamped into the engine env (telemetry version, #1940)",
        )

    def test_no_secret_backend_env_override(self):
        # main is SEC-5 keychain-native; the bundle's SECRET_BACKEND=env
        # override must NOT be ported (G4 wires the wizard to keychain_cli).
        self.assertNotIn(
            'SECRET_BACKEND = "env"',
            self.src.replace("env.SECRET_BACKEND", "SECRET_BACKEND"),
            "SECRET_BACKEND=env override is bundle-only; main uses keychain",
        )

    def test_inv01_no_empty_string_secrets(self):
        # INV-01: empty-string secret env vars must be DELETED, not passed on.
        self._has(
            r"_SECRET_ENV_KEYS",
            "INV-01 guard: empty-string secret env vars must be stripped",
        )


class BootFailureContract(unittest.TestCase):
    """The engine exits 0 on 'Shadow Boot FAILED' (restart-loop prevention) —
    a plain exit-0 handler would report a clean stop. The shell must detect
    the marker and surface an error state instead."""

    @classmethod
    def setUpClass(cls):
        if not _MANAGER.is_file():
            raise unittest.SkipTest("manager missing — covered by existence test")
        cls.src = _read(_MANAGER)

    def test_shadow_boot_failed_marker_parsed(self):
        self.assertIn("Shadow Boot FAILED", self.src)

    def test_schema_rebuild_marker_surfaced(self):
        # INV-10: a schema-version bump backs up + recreates the local DB; the
        # GUI must surface that to the user (event), never hide it.
        self.assertIn("backing up and recreating database", self.src)


class StaticServerLoopback(unittest.TestCase):
    def test_serve_binds_loopback_only(self):
        serve = _REPO_ROOT / "desktop" / "electron" / "serve.cjs"
        if not serve.is_file():
            self.fail(f"{serve} missing — G2 must ship the static server")
        src = _read(serve)
        self.assertRegex(
            src,
            r'listen\(\s*0\s*,\s*"127\.0\.0\.1"',
            "renderer static server must bind 127.0.0.1 on an ephemeral port",
        )


class MainShellContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _MAIN.is_file():
            raise unittest.SkipTest("main.cjs missing — covered by existence test")
        cls.src = _read(_MAIN)

    def test_main_shell_exists(self):
        self.assertTrue(_MAIN.is_file())

    def test_renderer_origin_flows_into_manager(self):
        # The ephemeral static-server port must reach _childEnv (CORS contract).  # noqa: E501
        self.assertRegex(
            self.src,
            r"rendererOrigin",
            "main.cjs must hand the renderer origin to the engine manager",
        )

    def test_context_isolation_on(self):
        self.assertRegex(self.src, r"contextIsolation:\s*true")
        self.assertRegex(self.src, r"nodeIntegration:\s*false")

    def test_desktop_opens_the_operator_console(self):
        # G3-final: the desktop shell opens the ported operator console at
        # `/console` (not the legacy `/dashboard` placeholder it used until the
        # console pages shipped). The route swap (src/App.tsx) makes `/console`
        # render the console; the shell must point there.
        self.assertRegex(
            self.src,
            r"loadURL\([^)]*/console",
            "desktop shell must open /console (the operator console), not /dashboard",  # noqa: E501
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
