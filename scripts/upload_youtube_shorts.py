# ai_trading_bot/scripts/upload_youtube_shorts.py
# Handles YouTube Data API v3 integration, OAuth2 flow, and video uploading.
# Wires refresh-token caching with OAuthSecretManager (youtube namespace) and complies with BORA.

import argparse
import json
import logging
import os
import pathlib
import sys
import time
from typing import Optional

import google.oauth2.credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# Ensure project root on path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.secret_manager_utils import oauth_secrets  # noqa: E402
from scripts.shorts_upload_guard import already_uploaded, record_upload  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Scopes required for uploading YouTube videos
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# R6-2b: remember the exact reference save_tokens() returns so the next run loads with it
# regardless of the secret-manager edition (OSS "keychain:shorts" vs Enterprise
# "alpaca-oauth-shorts"). The ref is a key name, not a secret.
_DEFAULT_TOKEN_REF = "keychain:shorts"
_TOKEN_REF_FILE = pathlib.Path(PROJECT_ROOT) / ".youtube_token_ref"


def _persist_token_ref(ref: str) -> None:
    """Best-effort persist the save_tokens() reference for the next run's load."""
    try:
        _TOKEN_REF_FILE.write_text(ref, encoding="utf-8")
    except OSError as e:
        logger.warning("Could not persist YouTube token reference: %s", e)


def _load_token_ref() -> str:
    """Return the persisted token reference, or the OSS default if none is recorded."""
    try:
        return _TOKEN_REF_FILE.read_text(encoding="utf-8").strip() or _DEFAULT_TOKEN_REF
    except OSError:
        return _DEFAULT_TOKEN_REF


def load_client_secrets(path: str) -> Optional[dict]:
    """Loads client secrets from environment variable string or local JSON file."""
    env_json = os.getenv("YOUTUBE_CLIENT_SECRETS_JSON")
    if env_json:
        try:
            logger.info(
                "Loaded client secrets config from YOUTUBE_CLIENT_SECRETS_JSON env variable."
            )
            return json.loads(env_json)
        except Exception as e:
            logger.warning(
                "Failed to parse YOUTUBE_CLIENT_SECRETS_JSON env variable: %s", e
            )

    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                logger.info("Loaded client secrets config from file %s", path)
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to read client secrets file %s: %s", path, e)

    return None


def get_youtube_credentials(
    client_secrets_path: str,
) -> google.oauth2.credentials.Credentials:
    """Retrieves and refreshes YouTube OAuth2 credentials. Falls back to InstalledAppFlow."""
    tokens = None
    # R6-2b: load with the exact reference the last save_tokens() returned (persisted to
    # _TOKEN_REF_FILE), so the round-trip is correct regardless of which secret-manager
    # edition is active (OSS vs Enterprise use different key schemes).
    for ref in [_load_token_ref()]:
        try:
            tokens = oauth_secrets.get_tokens(ref)
            if tokens:
                logger.info("Retrieved cached YouTube tokens from reference: %s", ref)
                break
        except Exception as e:
            logger.warning("Failed to retrieve tokens for reference %s: %s", ref, e)

    client_config = load_client_secrets(client_secrets_path)
    if not client_config:
        raise FileNotFoundError(
            f"Client configuration not found. Please provide a valid {client_secrets_path} "
            "file or set the YOUTUBE_CLIENT_SECRETS_JSON environment variable."
        )

    web_or_installed = "installed" if "installed" in client_config else "web"
    creds_data = client_config[web_or_installed]

    if tokens and tokens.get("access_token") and tokens.get("refresh_token"):
        creds = google.oauth2.credentials.Credentials(
            token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds_data["client_id"],
            client_secret=creds_data["client_secret"],
        )

        # Verify/refresh token
        try:
            from google.auth.transport.requests import Request

            creds.refresh(Request())

            # Update cache if the token was refreshed
            if creds.token != tokens["access_token"]:
                logger.info("Access token updated, caching refreshed tokens.")
                ref = oauth_secrets.save_tokens(
                    "shorts", creds.token, creds.refresh_token
                )
                _persist_token_ref(ref)
            return creds
        except Exception as e:
            logger.warning(
                "Failed to refresh cached tokens: %s. Initializing fresh authentication flow.",
                e,
            )

    # If no cached tokens or refresh failed, trigger the browser authorization flow
    # This flow requires interactive terminal access
    if not os.path.exists(client_secrets_path) and not os.getenv(
        "YOUTUBE_CLIENT_SECRETS_JSON"
    ):
        raise FileNotFoundError(
            f"client_secrets.json not found at {client_secrets_path} and YOUTUBE_CLIENT_SECRETS_JSON is empty. "
            "Cannot start OAuth authentication flow."
        )

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    # Save newly generated tokens
    ref = oauth_secrets.save_tokens("shorts", creds.token, creds.refresh_token)
    _persist_token_ref(ref)
    logger.info("YouTube OAuth2 tokens successfully cached.")
    return creds


def upload_video(
    creds: google.oauth2.credentials.Credentials,
    video_path: str,
    title: str,
    description: str,
    dedupe_key: Optional[str] = None,
) -> str:
    """Uploads vertical MP4 video to YouTube as an unlisted video. Returns YouTube Video ID.

    R6-2b: if ``dedupe_key`` is given and a video was already published for it, the upload
    is skipped and the recorded id returned — so a scheduler retry / double-run cannot
    publish a duplicate public video.
    """
    if dedupe_key:
        existing = already_uploaded(PROJECT_ROOT, dedupe_key)
        if existing:
            logger.warning(
                "Idempotency guard: '%s' was already uploaded (video_id=%s). "
                "Skipping to avoid a duplicate public video.",
                dedupe_key,
                existing,
            )
            return existing

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found at: {video_path}")

    # Enforce YouTube Shorts categorization requirement
    if "#Shorts" not in title:
        title = f"{title} #Shorts"

    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],  # YouTube API character limit
            "description": description[:5000],
            "tags": ["Shorts", "AI", "Trading", "Portfolio", "aaagents"],
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": "unlisted",  # Safe default to avoid posting immediately to public feed
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path, chunksize=1024 * 1024, resumable=True, mimetype="video/mp4"
    )
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    # R6-2b: retry the resumable upload on transient errors (5xx / 429 / socket) with
    # capped exponential backoff. next_chunk() resumes the SAME session, so retrying is
    # safe and does not create duplicate videos.
    response = None
    retries = 0
    max_retries = 5
    logger.info("Starting video upload for %s...", video_path)
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload progress: %d%%", int(status.progress() * 100))
        except HttpError as e:
            status_code = getattr(getattr(e, "resp", None), "status", None)
            if status_code in (500, 502, 503, 504, 429) and retries < max_retries:
                retries += 1
                backoff = min(2**retries, 60)
                logger.warning(
                    "Transient upload error (HTTP %s); retry %d/%d in %ds",
                    status_code,
                    retries,
                    max_retries,
                    backoff,
                )
                time.sleep(backoff)
            else:
                raise
        except OSError as e:
            if retries < max_retries:
                retries += 1
                backoff = min(2**retries, 60)
                logger.warning(
                    "Transient network error (%s); retry %d/%d in %ds",
                    e,
                    retries,
                    max_retries,
                    backoff,
                )
                time.sleep(backoff)
            else:
                raise

    video_id = response.get("id")
    logger.info("Successfully uploaded video to YouTube. Video ID: %s", video_id)
    if dedupe_key and video_id:
        record_upload(PROJECT_ROOT, dedupe_key, video_id)
    return video_id


def main() -> int:
    # R6-2b: resolve default relative paths against the project root only when run as a
    # script — not as an import-time side effect.
    os.chdir(PROJECT_ROOT)

    parser = argparse.ArgumentParser(description="Upload YouTube Shorts Video")
    parser.add_argument(
        "--video", required=True, help="Path to vertical MP4 video file"
    )
    parser.add_argument(
        "--title", default="Daily AI Portfolio Recap", help="Video Title"
    )
    parser.add_argument(
        "--description",
        default="Daily performance summary of the autonomous AI trading bots.",
        help="Video Description",
    )
    parser.add_argument(
        "--client-secrets",
        default=os.path.join(PROJECT_ROOT, "client_secrets.json"),
        help="Path to client_secrets.json",
    )
    parser.add_argument(
        "--dedupe-key",
        default=None,
        help="Idempotency key (e.g. the recap date). Skips re-upload if already published.",
    )

    args = parser.parse_args()

    try:
        creds = get_youtube_credentials(args.client_secrets)
        video_id = upload_video(
            creds,
            args.video,
            args.title,
            args.description,
            dedupe_key=args.dedupe_key,
        )
        print(f"SUCCESS: Uploaded video ID: {video_id}")
        return 0
    except Exception as e:
        logger.error("Upload failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
