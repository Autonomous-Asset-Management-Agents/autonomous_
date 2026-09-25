# R6-1 (#1671): the OAuth client secret for the YouTube Shorts pipeline must be ignored
# by the VERSIONED .gitignore (shared with every clone/CI), not a local-only exclude.
# These tests fail if the ignore rules are removed or narrowed.

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _is_ignored(rel_path: str) -> bool:
    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", "*"],
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "check-ignore", rel_path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"FAILED for {rel_path}: stdout={result.stdout}, stderr={result.stderr}")
    return result.returncode == 0


def test_client_secrets_json_is_gitignored_at_root():
    assert _is_ignored("client_secrets.json")


def test_client_secrets_json_is_gitignored_nested():
    assert _is_ignored("ai_trading_bot/client_secrets.json")


def test_arbitrary_client_secret_file_is_gitignored():
    assert _is_ignored("my_client_secret_prod.json")


def test_youtube_token_ref_cache_is_gitignored():
    assert _is_ignored(".youtube_token_ref")
