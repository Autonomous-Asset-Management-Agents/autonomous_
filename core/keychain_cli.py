"""SEC-5 (#1084): Interactive CLI Setup-Wizard for OS Keychain credentials.

Usage::

    python -m core.keychain_cli setup    # Interactive wizard
    python -m core.keychain_cli status   # Show which keys are set
    python -m core.keychain_cli delete   # Remove all keys from keychain
    python -m core.keychain_cli migrate  # Migrate .env.oss → OS Keychain
    python -m core.keychain_cli set KEY  # Non-interactive: write one secret (value via stdin)
    python -m core.keychain_cli has      # Non-interactive: print {"has_secrets": bool} as JSON

The interactive ``setup`` uses ``getpass`` for masked input so that API keys
are never visible in the terminal scrollback or shell history. The
non-interactive ``set``/``has`` commands (G4-1) back the desktop wizard's
Electron→Python bridge: ``set`` reads the value from **stdin** (never argv, so
it cannot leak in process listings); ``has`` reports first-launch state.
"""

import argparse
import getpass
import json
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Where .env.oss lives — repo root (3 levels up from core/keychain_cli.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_OSS_PATH = _PROJECT_ROOT / ".env.oss"


def _setup() -> None:
    """Interactive Setup-Wizard: prompt user for API keys."""
    from core.keychain import has_secrets, save_secret

    print()
    print("=" * 60)
    print("  AAAgents — Secure Credential Setup (SEC-5)")
    print("=" * 60)
    print()
    print("This wizard stores your API keys in the OS credential store")
    print("(Windows: Credential Manager, macOS: Keychain).")
    print("Keys are encrypted at rest — never stored as plaintext.")
    print()

    if has_secrets():
        print("⚠️  Keys already exist in keychain.")
        answer = input("Overwrite existing keys? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    # --- Required keys ---
    print("─" * 60)
    print("Required: Alpaca Paper-Trading API Keys")
    print("  Get yours at: https://app.alpaca.markets")
    print()

    alpaca_key = getpass.getpass("  Alpaca API Key: ").strip()
    if not alpaca_key:
        print("❌ Alpaca API Key is required. Aborting.")
        sys.exit(1)

    alpaca_secret = getpass.getpass("  Alpaca Secret Key: ").strip()
    if not alpaca_secret:
        print("❌ Alpaca Secret Key is required. Aborting.")
        sys.exit(1)

    # --- Optional keys ---
    print()
    print("─" * 60)
    print("Optional: AI & Data Provider Keys")
    print("  (Press Enter to skip)")
    print()

    gemini_key = getpass.getpass("  Gemini API Key (optional): ").strip()
    polygon_key = getpass.getpass("  Polygon API Key (optional): ").strip()
    databento_key = getpass.getpass("  Databento API Key (optional): ").strip()

    # --- Save ---
    saved = 0

    def _try_save(key_name: str, val: str) -> bool:
        try:
            save_secret(key_name, val)
            return True
        except Exception as exc:
            print(f"❌ Error saving {key_name}: {exc}")
            return False

    if _try_save("ALPACA_API_KEY", alpaca_key):
        saved += 1
    if _try_save("ALPACA_SECRET_KEY", alpaca_secret):
        saved += 1

    if gemini_key and _try_save("GEMINI_API_KEY", gemini_key):
        saved += 1
    if polygon_key and _try_save("POLYGON_API_KEY", polygon_key):
        saved += 1
    if databento_key and _try_save("DATABENTO_API_KEY", databento_key):
        saved += 1

    print()
    print("─" * 60)
    print(f"✅ {saved} key(s) verschlüsselt auf deinem System gespeichert.")
    if sys.platform == "win32":
        print("   Speicherort: Windows Credential Manager (DPAPI)")
    elif sys.platform == "darwin":
        print("   Speicherort: macOS Keychain")
    else:
        print("   Speicherort: Secret Service (D-Bus/GNOME Keyring)")
    print()
    print("Du kannst die Engine jetzt starten mit:")
    print("  python -m core.engine")
    print()


def _status() -> None:
    """Show which managed keys are present in the keychain."""
    from core.keychain import MANAGED_KEYS, SERVICE_NAME, _get_keyring

    kr = _get_keyring()
    if kr is None:
        print("❌ keyring library not installed.")
        sys.exit(1)

    print()
    print("AAAgents Keychain Status")
    print("─" * 40)

    found = 0
    for key in MANAGED_KEYS:
        try:
            value = kr.get_password(SERVICE_NAME, key)
            if value:
                masked = (
                    value[:4] + "****" + value[-4:]
                    if len(value) > 8
                    else "****"  # noqa: E501
                )
                print(f"  {key}: {masked}")
                found += 1
            else:
                print(f"  ❌ {key}: not set")
        except Exception as exc:
            print(f"  ⚠️  {key}: error ({exc})")

    print()
    print(f"Total: {found}/{len(MANAGED_KEYS)} keys configured")
    print()


def _delete() -> None:
    """Remove all managed keys from the keychain."""
    from core.keychain import MANAGED_KEYS, delete_secret

    prompt = "Delete ALL API keys from OS keychain? [y/N] "
    answer = input(prompt).strip().lower()
    if answer != "y":
        print("Aborted.")
        return

    for key in MANAGED_KEYS:
        delete_secret(key)

    print(f"✅ {len(MANAGED_KEYS)} key(s) removed from OS keychain.")


def _migrate() -> None:
    """Migrate secrets from .env.oss to OS keychain."""
    from core.keychain import MANAGED_KEYS, save_secret

    if not _ENV_OSS_PATH.exists():
        print(f"❌ {_ENV_OSS_PATH} not found. Nothing to migrate.")
        sys.exit(1)

    # Use dotenv to parse .env.oss without modifying os.environ
    try:
        from dotenv import dotenv_values
    except ImportError:
        print("❌ python-dotenv not installed. Cannot parse .env.oss.")
        sys.exit(1)

    env_values = dotenv_values(_ENV_OSS_PATH)

    migrated = []
    for key in MANAGED_KEYS:
        value = env_values.get(key)
        if value and value.strip():
            try:
                save_secret(key, value.strip())
                migrated.append(key)
            except Exception as exc:
                print(f"❌ Error migrating {key}: {exc}")

    if not migrated:
        print("No credential keys found in .env.oss. Nothing to migrate.")
        return

    # Backup .env.oss
    backup_path = _ENV_OSS_PATH.with_suffix(".oss.bak")
    shutil.copy2(_ENV_OSS_PATH, backup_path)

    # Comment out migrated keys in .env.oss
    lines = _ENV_OSS_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = []
    for line in lines:
        stripped = line.strip()
        commented = False
        for key in migrated:
            parts = [p.strip() for p in stripped.split("=", 1)]
            is_match = parts[0] == key or parts[0] == f"export {key}"
            if len(parts) > 1 and is_match:
                new_lines.append(f"# SEC-5: Migrated to OS keychain\n# {line}")
                commented = True
                break
        if not commented:
            new_lines.append(line)

    _ENV_OSS_PATH.write_text("".join(new_lines), encoding="utf-8")

    print()
    print(f"✅ {len(migrated)} key(s) migrated to OS keychain:")
    for key in migrated:
        print(f"   • {key}")
    print(f"   Backup: {backup_path}")
    print("   Migrated lines in .env.oss are now commented out.")
    print()


def _set(key: str | None) -> None:
    """Non-interactive single-secret write for the desktop bridge (G4-1).

    KEY is a MANAGED_KEYS name; the VALUE is read from **stdin** (never argv, so
    it cannot leak in process listings). Never echoes the value. Exits non-zero
    on an unknown key or empty value so the bridge can surface the error.
    """
    from core.keychain import MANAGED_KEYS, save_secret

    if not key or key not in MANAGED_KEYS:
        print(f"error: key must be one of {list(MANAGED_KEYS)}", file=sys.stderr)
        raise SystemExit(2)
    value = sys.stdin.readline().rstrip("\r\n")
    if not value:
        print("error: empty value on stdin", file=sys.stderr)
        raise SystemExit(2)
    try:
        save_secret(key, value)
    except Exception as exc:
        # Backend locked/unavailable. Emit the exception CLASS only — never the
        # value or str(exc) (which a backend might embed) and never a traceback,
        # since stderr is relayed to the renderer.
        print(f"error: failed to save {key}: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
    print(f"saved {key}")  # name only — never the value


def _has() -> None:
    """Print first-launch state as JSON for the bridge (G4-1): {"has_secrets": bool}."""
    from core.keychain import has_secrets

    print(json.dumps({"has_secrets": bool(has_secrets())}))


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="keychain_cli",
        description="AAAgents OS Keychain Manager (SEC-5)",
    )
    parser.add_argument(
        "command",
        choices=["setup", "status", "delete", "migrate", "set", "has"],
        help="Action to perform",
    )
    parser.add_argument(
        "key",
        nargs="?",
        help="for 'set': the MANAGED_KEYS name to write (value read from stdin)",
    )
    args = parser.parse_args()

    # Basic logging for CLI output (stderr — keeps `has` stdout clean JSON).
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "setup":
        _setup()
    elif args.command == "status":
        _status()
    elif args.command == "delete":
        _delete()
    elif args.command == "migrate":
        _migrate()
    elif args.command == "set":
        _set(args.key)
    elif args.command == "has":
        _has()


if __name__ == "__main__":
    main()
