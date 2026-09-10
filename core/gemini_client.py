# core/gemini_client.py
# Epic 1.7 / PR-A — Extracted from ai_components.py
# Contains: GeminiModelWrapper, chat functions, model initialization.
#
# IMPORTANT: No network calls at module import time.
# The Gemini model singleton is initialized lazily on first use via
# get_gemini_instance(). This prevents Cloud Run startup probe failures
# caused by blocking HTTP calls before uvicorn opens port 8080.

import logging
import os
import threading
from time import perf_counter
from typing import Optional

from google import genai as new_genai
from google.genai import types as genai_types

from config import GEMINI_AVAILABLE, GEMINI_MODEL_NAME
from core.llm.telemetry import record_call

# Backward-compat flag: always True since we only support the new google.genai SDK
_using_new_genai_sdk: bool = True


class GeminiModelWrapper:
    """Wrapper to provide consistent API for the google.genai SDK."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._client = None
        self._model = None

        api_key = os.getenv("GEMINI_API_KEY")
        self._client = new_genai.Client(
            api_key=api_key,
            http_options={"headers": {"User-Agent": "aaagents-oss/1.0"}},
        )

    def generate_content(self, prompt: str, max_output_tokens: int = 512) -> str:
        """Generate content using the new SDK."""
        # ADR-OBS-01 / PR D: time the real generate call (PURE OBSERVATION). The
        # perf_counter + record_call are fail-safe and never alter the result or a
        # raised exception (only the exception CLASS name is retained, never text).
        _t0 = perf_counter()
        _obs_exc = None
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=max_output_tokens,  # Cost guard: default 512
                    safety_settings=[
                        genai_types.SafetySetting(
                            category="HARM_CATEGORY_HARASSMENT",
                            threshold="BLOCK_NONE",
                        ),
                        genai_types.SafetySetting(
                            category="HARM_CATEGORY_HATE_SPEECH",
                            threshold="BLOCK_NONE",
                        ),
                        genai_types.SafetySetting(
                            category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            threshold="BLOCK_NONE",
                        ),
                        genai_types.SafetySetting(
                            category="HARM_CATEGORY_DANGEROUS_CONTENT",
                            threshold="BLOCK_NONE",
                        ),
                    ],
                ),
            )
            return response.text
        except BaseException as exc:  # re-raised below; observation is fail-safe
            _obs_exc = exc
            raise
        finally:
            record_call(_t0, _obs_exc)

    async def generate_content_async(
        self, prompt: str, max_output_tokens: int = 512
    ) -> str:
        """Async generate content."""
        _t0 = perf_counter()
        _obs_exc = None
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=max_output_tokens,  # Cost guard: default 512
                    safety_settings=[
                        genai_types.SafetySetting(
                            category="HARM_CATEGORY_HARASSMENT",
                            threshold="BLOCK_NONE",
                        ),
                        genai_types.SafetySetting(
                            category="HARM_CATEGORY_HATE_SPEECH",
                            threshold="BLOCK_NONE",
                        ),
                        genai_types.SafetySetting(
                            category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            threshold="BLOCK_NONE",
                        ),
                        genai_types.SafetySetting(
                            category="HARM_CATEGORY_DANGEROUS_CONTENT",
                            threshold="BLOCK_NONE",
                        ),
                    ],
                ),
            )
            if not response.text:
                logging.warning(
                    "Gemini returned empty text! Raw response: %s", response
                )
            return response.text or ""
        except BaseException as exc:  # re-raised below; observation is fail-safe
            _obs_exc = exc
            raise
        finally:
            record_call(_t0, _obs_exc)


def _get_available_models():
    """Get list of available model IDs."""
    client = new_genai.Client(
        api_key=os.getenv("GEMINI_API_KEY"),
        http_options={"headers": {"User-Agent": "aaagents-oss/1.0"}},
    )
    return [
        m.name.split("/")[-1] if "/" in m.name else m.name for m in client.models.list()
    ]


# ---------------------------------------------------------------------------
# Lazy singleton — initialized on first use, NOT at import time.
# This is critical for Cloud Run: the HTTP call to _get_available_models()
# previously ran at module import time and delayed port 8080 by 4-5 minutes.
# ---------------------------------------------------------------------------
_gemini_instance: Optional[GeminiModelWrapper] = None
_gemini_lock = threading.Lock()

# Module-level name kept at None for backward compatibility.
# Code that does `from core.gemini_client import gemini_model_instance` and
# stores the value locally will get None — instead use get_gemini_instance().
gemini_model_instance = None


def _build_gemini_instance() -> Optional[GeminiModelWrapper]:
    """Internal: one-time creation of the GeminiModelWrapper. Never call directly."""
    if not GEMINI_AVAILABLE or not GEMINI_MODEL_NAME:
        return None

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logging.warning("GEMINI_API_KEY not found in environment variables")
        return None

    try:
        try:
            available_model_ids = _get_available_models()
            logging.info("Available Gemini models: %s", available_model_ids)

            if GEMINI_MODEL_NAME not in available_model_ids:
                logging.warning(
                    "Model %s not available in: %s",
                    GEMINI_MODEL_NAME,
                    available_model_ids,
                )
                fallback_options = [
                    "gemini-2.5-flash",
                    "gemini-flash-latest",
                    "gemini-pro-latest",
                ]
                fallback_model = next(
                    (opt for opt in fallback_options if opt in available_model_ids),
                    None,
                )
                if not fallback_model:
                    logging.error("No suitable Gemini model found. Disabling Gemini.")
                    return None
            else:
                fallback_model = GEMINI_MODEL_NAME
                logging.info("Using configured model: %s", fallback_model)

        except Exception as model_error:
            logging.warning(
                "Could not list models: %s. Using default fallback.", model_error
            )
            fallback_model = GEMINI_MODEL_NAME or "gemini-2.5-flash"

        instance = GeminiModelWrapper(fallback_model)
        logging.info("Gemini client: model '%s' initialized (lazy).", fallback_model)
        return instance

    except Exception as e:
        logging.error("Gemini client: Failed to initialize Gemini model: %s", e)
        return None


def get_gemini_instance() -> Optional[GeminiModelWrapper]:
    """Return the shared GeminiModelWrapper, initializing it on first call.

    Thread-safe. No-op if GEMINI_AVAILABLE is False or API key is missing.
    First call makes a network request to list available models — subsequent
    calls return the cached instance immediately.
    """
    global _gemini_instance, gemini_model_instance
    if _gemini_instance is not None:
        return _gemini_instance
    with _gemini_lock:
        # Double-checked locking
        if _gemini_instance is None:
            _gemini_instance = _build_gemini_instance()
            # Keep module-level alias in sync for code that reads it via module ref
            gemini_model_instance = _gemini_instance
    return _gemini_instance


# ---------------------------------------------------------------------------
# Chat functions — resolve the model through the LLM provider seam (ADR-014)
# so /chat follows LLM_PROVIDER (Gemini API or local Ollama on desktop). The
# import is lazy (in-function): core.llm.provider's default path imports this
# module, so a top-level import on both sides would be a cycle.
# ---------------------------------------------------------------------------
def answer_trading_chat(
    system_context: str, user_message: str, max_tokens: int = 800
) -> Optional[str]:
    """
    Answer user questions using primarily the provided bot context.
    Returns None if no LLM provider is available or on error.
    """
    from core.llm.provider import get_llm_provider

    model = get_llm_provider()
    if not model or not system_context.strip():
        return None
    prompt = (
        "You are the AAA Trading Bot assistant. Answer the user's question using the data "
        "below when it applies. Be concise but helpful (2-5 sentences). Do not invent portfolio "
        "numbers or symbols. Prefer data from the context for portfolio/strategy/trades; for "
        "news and trends use the recent news section. If the data genuinely doesn't contain "
        "enough to answer, reply briefly that the data doesn't contain that and you'd need more info.\n\n"
        f"DATA:\n{system_context.strip()}\n\nUSER: {user_message.strip()}\n\nASSISTANT:"
    )
    try:
        reply = model.generate_content(prompt, max_output_tokens=max_tokens)
        return (reply or "").strip() or None
    except Exception as e:
        err_str = str(e).upper()
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "RATE" in err_str:
            logging.info("Chat: Gemini rate limit (429) – using fallback reply.")
        else:
            logging.warning("Chat Gemini error: %s", e)
        return None


def _reply_indicates_insufficient(reply: Optional[str]) -> bool:
    """True if the model said it doesn't have enough data to answer."""
    if not reply or not reply.strip():
        return True
    r = reply.lower().strip()
    indicators = (
        "don't have",
        "doesn't contain",
        "data doesn't",
        "not in the data",
        "no information",
        "not available",
        "cannot answer",
        "need more",
        "insufficient",
        "not provided",
        "i'd need",
        "i would need",
    )
    return any(ind in r for ind in indicators)


def answer_with_gemini_general(
    user_message: str, system_context: str = ""
) -> Optional[str]:
    """
    Use the configured LLM to answer open-ended questions (market trends, earnings, etc.).
    """
    from core.llm.provider import get_llm_provider

    model = get_llm_provider()
    if not model:
        return None
    prompt = (
        "You are the AAA Trading Bot assistant. The user is asking a question that may go beyond "
        "the bot's live data. Answer helpfully and concisely (3-6 sentences). Focus on markets, "
        "stocks, earnings, macro/political impact on stocks, and trends when relevant. If optional "
        "context is provided below, use it to tailor your answer; otherwise use your general "
        "knowledge. Be accurate and avoid giving specific investment advice or price targets.\n\n"
        f"Optional context from the bot (portfolio/news summary):\n"
        f"{system_context[:3000] if system_context else 'None provided.'}\n\n"
        f"USER: {user_message.strip()}\n\nASSISTANT:"
    )
    try:
        reply = model.generate_content(prompt)
        return (reply or "").strip() or None
    except Exception as e:
        logging.warning("Chat Gemini general fallback error: %s", e)
        return None


def answer_chat_with_fallback(system_context: str, user_message: str) -> str:
    """
    Answer chat: use knowledge-base context first; fallback to Gemini general.
    Always returns a non-empty string.
    """
    reply = answer_trading_chat(system_context, user_message)
    if reply and not _reply_indicates_insufficient(reply):
        return reply
    general_reply = answer_with_gemini_general(user_message, system_context)
    if general_reply:
        return general_reply
    if reply:
        return reply
    return (
        "I couldn't generate an answer. Make sure an LLM provider "
        "(Gemini API key or a local Ollama model) is configured."
    )
