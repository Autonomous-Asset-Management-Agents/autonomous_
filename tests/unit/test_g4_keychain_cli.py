"""G4-1 (#1050): non-interactive `set` / `has` subcommands for keychain_cli.

The desktop wizard's Electron→Python bridge (secrets-bridge.cjs) needs a
non-interactive way to write one secret (value via stdin, never argv) and to
check first-launch state. These are NEW subcommands on the EXISTING CLI — the
interactive `setup`/`status`/`delete`/`migrate` commands must keep working
(regression). `core.keychain` is mocked so the test needs no real OS keyring.
"""

import io
import json
import sys
import types
import unittest
from unittest import mock


def _fake_keychain(has=True):
    m = types.ModuleType("core.keychain")
    m.MANAGED_KEYS = [
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "GEMINI_API_KEY",
        "POLYGON_API_KEY",
        "DATABENTO_API_KEY",
    ]
    m.SERVICE_NAME = "aaagents"
    m.save_secret = mock.Mock()
    m.has_secrets = mock.Mock(return_value=has)
    return m


def _run(argv, stdin="", fake=None):
    """Run keychain_cli.main() with patched argv/stdin/core.keychain; capture
    stdout + the exit code (0 when main returns without sys.exit)."""
    from core import keychain_cli

    out = io.StringIO()
    code = 0
    mods = {"core.keychain": fake} if fake else {}
    with mock.patch.dict(sys.modules, mods), mock.patch.object(
        sys, "argv", ["keychain_cli", *argv]
    ), mock.patch.object(sys, "stdin", io.StringIO(stdin)), mock.patch.object(
        sys, "stdout", out
    ):
        try:
            keychain_cli.main()
        except SystemExit as e:  # argparse / explicit exits
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return out.getvalue(), code


class KeychainCliSetHas(unittest.TestCase):
    def test_set_reads_value_from_stdin_and_saves(self):
        fake = _fake_keychain()
        _, code = _run(["set", "ALPACA_API_KEY"], stdin="pk-abc123\n", fake=fake)
        self.assertEqual(code, 0)
        fake.save_secret.assert_called_once_with("ALPACA_API_KEY", "pk-abc123")

    def test_set_rejects_unmanaged_key_without_saving(self):
        fake = _fake_keychain()
        _, code = _run(["set", "BOGUS_KEY"], stdin="x\n", fake=fake)
        self.assertNotEqual(code, 0)
        fake.save_secret.assert_not_called()

    def test_set_does_not_echo_the_value(self):
        fake = _fake_keychain()
        out, _ = _run(["set", "GEMINI_API_KEY"], stdin="super-secret\n", fake=fake)
        self.assertNotIn("super-secret", out)

    def test_set_save_failure_does_not_leak_value_or_detail(self):
        fake = _fake_keychain()
        fake.save_secret.side_effect = RuntimeError("backend-detail-leak")
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            out, code = _run(
                ["set", "ALPACA_API_KEY"], stdin="pk-secret-xyz\n", fake=fake
            )
        self.assertNotEqual(code, 0)
        combined = out + err.getvalue()
        self.assertNotIn("pk-secret-xyz", combined)  # never the value
        # never str(exc) / traceback
        self.assertNotIn("backend-detail-leak", combined)
        fake.save_secret.assert_called_once()

    def test_has_prints_json_true(self):
        fake = _fake_keychain(has=True)
        out, code = _run(["has"], fake=fake)
        self.assertEqual(code, 0)
        self.assertIs(json.loads(out)["has_secrets"], True)

    def test_has_prints_json_false(self):
        fake = _fake_keychain(has=False)
        out, _ = _run(["has"], fake=fake)
        self.assertIs(json.loads(out)["has_secrets"], False)


def _fake_keychain_with_keyring(present):
    """INC-5: fake core.keychain whose _get_keyring() returns a stub keyring.

    `present` maps a MANAGED_KEYS name → stored value (or None for "not set").
    """
    m = types.ModuleType("core.keychain")
    m.MANAGED_KEYS = list(present.keys())
    m.SERVICE_NAME = "aaagents"
    kr = mock.Mock()
    kr.get_password = mock.Mock(side_effect=lambda service, key: present.get(key))
    m._get_keyring = mock.Mock(return_value=kr)
    return m


class KeychainCliStatusJson(unittest.TestCase):
    """INC-5 (F5): `status --json` → machine-readable per-slot presence for the
    desktop broker-keys badge. Presence booleans only — never any secret value."""

    def test_status_json_reports_per_slot_presence(self):
        fake = _fake_keychain_with_keyring(
            {"ALPACA_API_KEY": "PK123456789", "ALPACA_LIVE_API_KEY": None}
        )
        out, code = _run(["status", "--json"], fake=fake)
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out)["present"],
            {"ALPACA_API_KEY": True, "ALPACA_LIVE_API_KEY": False},
        )

    def test_status_json_never_leaks_secret_values(self):
        fake = _fake_keychain_with_keyring({"ALPACA_API_KEY": "PK-super-secret-value"})
        out, _ = _run(["status", "--json"], fake=fake)
        self.assertNotIn("PK-super-secret-value", out)
        self.assertNotIn("super", out)

    def test_status_without_json_stays_human_readable(self):
        # Regression: the plain `status` output is unchanged (masked human report).
        fake = _fake_keychain_with_keyring({"ALPACA_API_KEY": "PK123456789"})
        out, code = _run(["status"], fake=fake)
        self.assertEqual(code, 0)
        self.assertNotIn('"present"', out)  # not the JSON shape
        self.assertIn("ALPACA_API_KEY", out)


class KeychainCliRegression(unittest.TestCase):
    def test_existing_commands_still_dispatch(self):
        from core import keychain_cli

        for cmd in ["setup", "status", "delete", "migrate"]:
            with mock.patch.object(
                keychain_cli, f"_{cmd}"
            ) as handler, mock.patch.object(sys, "argv", ["keychain_cli", cmd]):
                keychain_cli.main()
                handler.assert_called_once()


if __name__ == "__main__":
    unittest.main()
