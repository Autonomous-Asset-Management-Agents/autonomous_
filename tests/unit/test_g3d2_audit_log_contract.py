"""G3d-2 (#1050): pins the WRITER side of the desktop audit-chain contract.

The console's Electron reader (desktop/electron/audit-chain.cjs) reads
``audit_log_<date>.jsonl`` from the directory named by ``SENATE_LOG_DIR``. That
only ever finds data if the logger the desktop actually boots —
``LocalJSONAuditLogger`` (runner.boot_engine with no license key) — honors
``SENATE_LOG_DIR`` and writes that exact filename. Those facts are pinned here
by source inspection (no engine import → runs without the engine's heavy deps,
same grep-gate idiom as test_g2_desktop_env_contract.py), so a refactor that
silently breaks the cross-layer path contract fails CI loudly instead of leaving
the Audit Chain page mysteriously empty.

Closes the loop with:
  - env-contract gate: native-engine-manager._childEnv sets SENATE_LOG_DIR
  - node test: audit-chain.cjs::resolveAuditDir resolves the same SENATE_LOG_DIR
"""

import unittest
from pathlib import Path

_AITB = Path(__file__).resolve().parents[2]  # tests/unit -> ai_trading_bot
_SENATE_LOG = _AITB / "core" / "round_table" / "senate_log.py"
_RUNNER = _AITB / "core" / "round_table" / "runner.py"


class AuditLogWriterContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.senate = _SENATE_LOG.read_text(encoding="utf-8")
        cls.runner = _RUNNER.read_text(encoding="utf-8")

    def test_local_logger_anchors_dir_at_senate_log_dir(self):
        # LocalJSONAuditLogger.__init__ must read SENATE_LOG_DIR at instance time
        # — the desktop shell sets it so the audit logs land where the reader
        # looks (and out of a read-only Program Files CWD).
        self.assertRegex(
            self.senate,
            r'self\._log_dir\s*=\s*Path\(\s*os\.getenv\(\s*"SENATE_LOG_DIR"',
            "LocalJSONAuditLogger must anchor its dir at SENATE_LOG_DIR",
        )

    def test_writer_filename_is_audit_log_dated(self):
        # The reader's FILE_RE matches ^audit_log_<date>.jsonl$; the writer must
        # produce exactly that name.
        self.assertRegex(
            self.senate,
            r'f"audit_log_\{today\}\.jsonl"',
            "writer filename must be audit_log_<date>.jsonl (reader's FILE_RE)",
        )

    def test_desktop_boots_local_json_logger_without_license(self):
        # No license key → LocalJSONAuditLogger (the logger whose path the reader
        # mirrors). If this flips, the reader would mirror the wrong logger.
        self.assertIn("_senate = LocalJSONAuditLogger()", self.runner)
        self.assertRegex(self.runner, r"if\s+license_key\s*:")
        self.assertIn("_senate = SenateProtocol()", self.runner)


if __name__ == "__main__":
    unittest.main()
