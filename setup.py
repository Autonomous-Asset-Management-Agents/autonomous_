#!/usr/bin/env python3
import argparse
import getpass
import os
import re
import sys
import secrets
from pathlib import Path

# Match `KEY=`, `# KEY=`, `#  KEY=`, `#\tKEY=` etc. The earlier prefix-tuple
# match silently dropped two-space and tab variants, which would mean the
# captured Alpaca secret was thrown away while the success message lied.
_ALPACA_LINE_RE = re.compile(
    r'^\s*#?\s*(ALPACA_API_KEY|ALPACA_SECRET_KEY)\s*=',
    re.IGNORECASE,
)

# Guard against CP1252 / Latin-1 terminal encoding on Windows (PowerShell 5.1,
# cmd.exe) which cannot encode Unicode emoji. Reconfigure stdout/stderr to
# replace unencodable characters with '?' so the script never crashes on
# print(). This is a no-op on UTF-8 terminals.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

ENV_EXAMPLE = Path(".env.oss.example")
ENV_FILE = Path(".env.oss")

def generate_secrets() -> dict:
    """Generates cryptographically secure hex strings with exact entropy."""
    return {
        "POSTGRES_PASSWORD": secrets.token_hex(24),
        "PROXY_ENGINE_SHARED_SECRET": secrets.token_hex(32),
        "ENGINE_API_KEY": secrets.token_hex(32),
        "REDIS_PASSWORD": secrets.token_hex(24),
    }


def _prompt_alpaca_keys(prompt_enabled: bool) -> tuple:
    """Prompt the user for Alpaca paper-trading credentials.

    Returns a (api_key, secret_key) tuple. Empty strings are valid and result
    in empty-but-uncommented lines being written to .env.oss so the user can
    paste later without having to remove a leading '#'. Whitespace around
    pasted values is stripped.

    Prompts are skipped entirely when ``prompt_enabled`` is False
    (--non-interactive flag). Otherwise we read from stdin even if it isn't a
    TTY, so piped input (CI fixtures, automation) is honoured. Closed stdin
    (`< /dev/null`) raises EOFError on the first read and we return empty
    strings without hanging.

    On a real TTY, the secret prompt uses ``getpass.getpass`` so the secret is
    not echoed. On non-TTY stdin, getpass falls back to plain ``input()`` and
    emits a single ``GetPassWarning`` to stderr — harmless for piped runs.
    """
    if not prompt_enabled:
        return "", ""

    on_tty = sys.stdin.isatty()
    if on_tty:
        print("\n--- Alpaca Paper-Trading credentials ----------------------------")
        print("Optional. Press Enter to skip and add them to .env.oss later.")
        print("Get a free account at https://app.alpaca.markets")
    try:
        api_key = input(
            "Alpaca API Key (or Enter to skip): " if on_tty else ""
        ).strip()
        # On a real TTY, use getpass to mask the secret echo. On non-TTY
        # stdin (CI, pipes), getpass.getpass on Windows reads from the
        # console via msvcrt and ignores the pipe → hangs. Falling back to
        # input() consumes the next stdin line and keeps piped runs
        # deterministic.
        if on_tty:
            secret_key = getpass.getpass(
                "Alpaca Secret Key (input hidden, or Enter to skip): "
            ).strip()
        else:
            # Git Bash / MinTTY on Windows reports isatty() == False even
            # though there is a real interactive terminal attached. README
            # used to claim "the secret prompt hides your input" — that is
            # only true on PowerShell, cmd.exe and POSIX terminals. Warn
            # the user before we echo their secret in plain text.
            print(
                "[WARN] Stdin is not a TTY (Git Bash MinTTY?) — your secret will be echoed in plain text.\n"
                "       For masked input, run from PowerShell or cmd.exe, or pipe the value via stdin:\n"
                '         printf "$KEY\\n$SECRET\\n" | python setup.py',
                file=sys.stderr,
            )
            secret_key = input().strip()
    except (EOFError, KeyboardInterrupt):
        if on_tty:
            print("\n[!] Prompt aborted - leaving Alpaca keys empty.")
        return "", ""
    return api_key, secret_key


def _rewrite_alpaca_line(line: str, key: str, value: str) -> str:
    """Convert a possibly-commented Alpaca `# KEY=` line into `KEY=value`.

    Uses a regex (``_ALPACA_LINE_RE``) so any amount of whitespace, tabs or
    leading ``#`` is tolerated. Preserves trailing newline. Leaves unrelated
    lines untouched.
    """
    match = _ALPACA_LINE_RE.match(line)
    if match is None:
        return line
    if match.group(1).upper() != key.upper():
        return line
    newline = "\n" if line.endswith("\n") else ""
    return f"{key}={value}{newline}"


def main():
    # 1. Robust native version check
    if sys.version_info < (3, 8):
        sys.exit("[FATAL] Python 3.8+ required.")

    parser = argparse.ArgumentParser(
        description="Generate .env.oss with secure secrets for AAAgents OSS.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip Alpaca credential prompt (for CI/scripted runs). The "
             "ALPACA_API_KEY / ALPACA_SECRET_KEY lines are written empty "
             "but uncommented.",
    )
    args = parser.parse_args()

    # 2. Guard against accidental override
    if ENV_FILE.exists():
        print("[!] .env.oss already exists - skipping to prevent overwriting secrets.")
        sys.exit(0)

    if not ENV_EXAMPLE.exists():
        sys.exit("[FATAL] .env.oss.example not found in current directory.")

    # 3. Decide whether to prompt for Alpaca keys.
    # --non-interactive disables the prompt outright (for CI / scripted runs).
    # Otherwise the prompt runs and reads stdin — works on a real TTY (with
    # masked secret echo via getpass) and with piped input (printf | setup.py).
    # Closed stdin (`< /dev/null`) yields EOFError → empty values, no hang.
    alpaca_key, alpaca_secret = _prompt_alpaca_keys(prompt_enabled=not args.non_interactive)

    print("Generating cryptographically secure secrets...")
    sec = generate_secrets()

    try:
        lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(keepends=True)

        # Create the env file with mode 0600 BEFORE any byte is written on
        # POSIX. The previous "open then chmod" sequence left a window where
        # a partial file with default umask permissions (typically 0644)
        # could exist on disk if the write crashed mid-stream — exposing the
        # captured Alpaca secret to other local users. Windows has no POSIX
        # mode bits; the parent ACL handles confinement there.
        if os.name == "posix":
            fd = os.open(
                str(ENV_FILE),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            f = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        else:
            f = ENV_FILE.open("w", encoding="utf-8", newline="\n")

        try:
            for line in lines:
                # Robust matching without hardcoding string lengths
                if line.startswith("POSTGRES_PASSWORD="):
                    f.write(f"POSTGRES_PASSWORD={sec['POSTGRES_PASSWORD']}\n")
                    continue
                # Always uncomment the Alpaca lines so a user pasting their
                # key into .env.oss later cannot accidentally leave a leading
                # '#' in place. Empty value is valid (offline mode).
                rewritten = _rewrite_alpaca_line(line, "ALPACA_API_KEY", alpaca_key)
                if rewritten is not line:
                    f.write(rewritten)
                    continue
                rewritten = _rewrite_alpaca_line(line, "ALPACA_SECRET_KEY", alpaca_secret)
                if rewritten is not line:
                    f.write(rewritten)
                    continue
                f.write(line)

            f.write("\n# --- Internal Service Authentication -----------------------------------------\n")
            f.write("# Auto-generated. Do NOT commit this file or share these values.\n")
            f.write("# HMAC-SHA256 key signing requests from the Public API to the Backend Engine.\n")
            f.write(f"PROXY_ENGINE_SHARED_SECRET={sec['PROXY_ENGINE_SHARED_SECRET']}\n")
            f.write("\n# Auto-generated API Key for the Engine REST API.\n")
            f.write(f"ENGINE_API_KEY={sec['ENGINE_API_KEY']}\n")

            f.write("\n# --- Redis Auth --------------------------------------------------------------\n")
            f.write("# Auto-generated. Required by docker-compose.oss.yml (redis-server --requirepass)\n")
            f.write("# and embedded in REDIS_URL by both the backend and the public-api services.\n")
            f.write(f"REDIS_PASSWORD={sec['REDIS_PASSWORD']}\n")
        finally:
            f.close()

        # Initialize required directories
        Path("data").mkdir(exist_ok=True)
        Path("plugins").mkdir(exist_ok=True)

    except IOError as e:
        sys.exit(f"[FATAL] File I/O Error: {e}")

    print("[OK] .env.oss created successfully with secure permissions.")
    
    print("\n+----------------------------------------------------------+")
    print("|  Auto-generated secrets (stored only in .env.oss):       |")
    print("|                                                          |")
    print("|  POSTGRES_PASSWORD            [v]  generated             |")
    print("|  PROXY_ENGINE_SHARED_SECRET   [v]  generated             |")
    print("|  ENGINE_API_KEY               [v]  generated             |")
    print("|  REDIS_PASSWORD               [v]  generated             |")
    print("|                                                          |")
    print("|  These values are NOT printed here intentionally.        |")
    print("+----------------------------------------------------------+\n")
    
    if alpaca_key and alpaca_secret:
        print("[OK] Alpaca paper-trading keys captured and written to .env.oss.\n")
        print("    Start the system:")
        print("    -> docker compose --env-file .env.oss -f docker-compose.oss.yml up -d\n")
    else:
        print("[!] Alpaca keys are empty - bot will boot in Offline Mode (no orders).\n")
        print("    To enable order execution later, paste your keys directly into")
        print("    the existing (uncommented) lines in .env.oss:\n")
        print("      ALPACA_API_KEY=<paste your key>")
        print("      ALPACA_SECRET_KEY=<paste your secret>\n")
        print("    Free Alpaca Paper-Trading account (no real money required):")
        print("    -> https://app.alpaca.markets\n")
        print("    Start the system:")
        print("    -> docker compose --env-file .env.oss -f docker-compose.oss.yml up -d\n")

if __name__ == "__main__":
    main()
