import os
import pytest
import requests
import google.generativeai as genai
from alpaca.trading.client import TradingClient

# Only run if explicitly enabled via env var to prevent rate-limiting in normal CI runs
pytestmark = pytest.mark.skipif(
    os.getenv("E2E_SMOKE_TEST", "false").lower() != "true",
    reason="External E2E tests disabled. Set E2E_SMOKE_TEST=true to run.",
)


def test_alpaca_liveness():
    """Verify that Alpaca Trading API is reachable and credentials are valid."""
    api_key = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_SECRET_KEY")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    assert api_key, "ALPACA_API_KEY is not set"
    assert api_secret, "ALPACA_SECRET_KEY is not set"

    is_paper = "paper" in base_url.lower()
    client = TradingClient(api_key, api_secret, paper=is_paper)

    # Ping the API to fetch account details
    account = client.get_account()
    assert account is not None
    assert account.id is not None
    assert account.status in ["ACTIVE", "ACCOUNT_UPDATED"]


def test_gemini_liveness():
    """Verify that Google AI Studio returns 200 OK using current model versions."""
    api_key = os.getenv("GEMINI_API_KEY")
    assert api_key, "GEMINI_API_KEY is not set"

    genai.configure(api_key=api_key)

    # We test the model configured in production, fallback to gemini-2.5-flash
    model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
    model = genai.GenerativeModel(model_name)

    response = model.generate_content("Ping. Reply with 'Pong'.")
    assert response is not None
    assert "pong" in response.text.lower()
