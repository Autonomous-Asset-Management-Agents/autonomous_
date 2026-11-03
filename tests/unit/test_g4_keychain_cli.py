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

import pytest


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
    m.PLATZ_SCHLUESSEL = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
    m.save_secret = mock.Mock()
    m.delete_secret = mock.Mock()
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
    # #4631: _status liest den Dienst je Platz ueber dienst_fuer (Flag aus: immer aaagents).
    m.dienst_fuer = lambda key, platz=None: m.SERVICE_NAME
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


class KeychainCliDeleteKey(unittest.TestCase):
    """#4951: `delete KEY` removes one slot key without a prompt, never ALPACA_LIVE_*."""

    @pytest.mark.vc0
    def test_delete_key_removes_one_slot_key_without_prompt(self):
        fake = _fake_keychain()
        with mock.patch("builtins.input") as prompt:
            _, code = _run(["delete", "ALPACA_API_KEY"], fake=fake)
        self.assertEqual(code, 0)
        prompt.assert_not_called()
        fake.delete_secret.assert_called_once_with("ALPACA_API_KEY")

    @pytest.mark.vc0
    def test_delete_key_refuses_live_key_and_deletes_nothing(self):
        fake = _fake_keychain()
        with mock.patch("builtins.input") as prompt:
            _, code = _run(["delete", "ALPACA_LIVE_API_KEY"], fake=fake)
        self.assertEqual(code, 2)
        prompt.assert_not_called()
        fake.delete_secret.assert_not_called()

    @pytest.mark.vc0
    def test_delete_without_key_stays_interactive(self):
        fake = _fake_keychain()
        with mock.patch("builtins.input", return_value="n") as prompt:
            _, code = _run(["delete"], fake=fake)
        self.assertEqual(code, 0)
        prompt.assert_called_once()
        fake.delete_secret.assert_not_called()


def _fake_keychain_account(keys):
    """#5148: core.keychain mit Keyring-Double; `keys` = Dienst aaagents, KEY -> Wert.

    Jeder andere Dienst liefert einen fremden Wert: Liest `account` ihn, fällt der Test auf.
    """
    m = types.ModuleType("core.keychain")
    m.SERVICE_NAME = "aaagents"
    kr = mock.Mock()
    kr.get_password = mock.Mock(
        side_effect=lambda service, key: (
            keys.get(key) if service == "aaagents" else "fremder-dienst"
        )
    )
    m._get_keyring = mock.Mock(return_value=kr)
    m.dienst_fuer = mock.Mock(return_value="aaagents-paper2")
    return m


class _Antwort(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _account(keys, urlopen):
    """`account` mit Keyring- und HTTP-Double; liefert (stdout, stderr, code)."""
    err = io.StringIO()
    with mock.patch("urllib.request.urlopen", urlopen), mock.patch.object(
        sys, "stderr", err
    ):
        out, code = _run(["account"], fake=_fake_keychain_account(keys))
    return out, err.getvalue(), code


_KEYS = {"ALPACA_API_KEY": "PKID-geheim-1", "ALPACA_SECRET_KEY": "SECRET-geheim-2"}


class KeychainCliAccount(unittest.TestCase):
    """#5148: `account` liefert Status und Kontonummer des Setup-Kontos, nie die Keys."""

    @pytest.mark.vc0
    def test_account_druckt_status_und_nummer(self):
        body = json.dumps({"account_number": "PA0001234", "id": "uuid-x"}).encode()
        urlopen = mock.Mock(return_value=_Antwort(body))
        out, _, code = _account(_KEYS, urlopen)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"status": 200, "account_id": "PA0001234"})
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://paper-api.alpaca.markets/v2/account")
        self.assertEqual(urlopen.call_args.kwargs.get("timeout"), 10)

    @pytest.mark.vc0
    def test_account_ohne_account_number_nimmt_id(self):
        body = json.dumps({"id": "uuid-x"}).encode()
        out, _, _ = _account(_KEYS, mock.Mock(return_value=_Antwort(body)))
        self.assertEqual(json.loads(out), {"status": 200, "account_id": "uuid-x"})

    @pytest.mark.vc0
    def test_account_401_ohne_nummer(self):
        import urllib.error

        fehler = urllib.error.HTTPError(
            "https://paper-api.alpaca.markets/v2/account", 401, "x", {}, None
        )
        out, _, code = _account(_KEYS, mock.Mock(side_effect=fehler))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"status": 401, "account_id": None})

    @pytest.mark.vc0
    def test_account_netzfehler_status_0(self):
        import urllib.error

        urlopen = mock.Mock(side_effect=urllib.error.URLError("offline"))
        out, _, _ = _account(_KEYS, urlopen)
        self.assertEqual(json.loads(out), {"status": 0, "account_id": None})

    @pytest.mark.vc0
    def test_account_gibt_keine_keys_aus(self):
        import urllib.error

        for urlopen in (
            mock.Mock(return_value=_Antwort(b'{"account_number": "PA1"}')),
            mock.Mock(side_effect=urllib.error.URLError("PKID-geheim-1")),
            mock.Mock(side_effect=RuntimeError("SECRET-geheim-2")),
        ):
            out, err, _ = _account(_KEYS, urlopen)
            for wert in _KEYS.values():
                self.assertNotIn(wert, out)
                self.assertNotIn(wert, err)

    @pytest.mark.vc0
    def test_account_ohne_keys_status_0_ohne_netzaufruf(self):
        urlopen = mock.Mock()
        out, _, code = _account({"ALPACA_API_KEY": "PKID-geheim-1"}, urlopen)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"status": 0, "account_id": None})
        urlopen.assert_not_called()

    @pytest.mark.vc0
    def test_account_liest_dienst_aaagents_unabhaengig_von_slot(self):
        body = json.dumps({"account_number": "PA0001234"}).encode()
        urlopen = mock.Mock(return_value=_Antwort(body))
        env = {"STRATEGY_PROFILES_ENABLED": "true", "AAA_KEY_SLOT": "paper2"}
        with mock.patch.dict("os.environ", env):
            out, _, _ = _account(_KEYS, urlopen)
        self.assertEqual(json.loads(out)["account_id"], "PA0001234")
        headers = dict(urlopen.call_args.args[0].header_items())
        self.assertEqual(headers.get("Apca-api-key-id"), "PKID-geheim-1")
        self.assertEqual(headers.get("Apca-api-secret-key"), "SECRET-geheim-2")


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
