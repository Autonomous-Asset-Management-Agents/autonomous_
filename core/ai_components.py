# core/ai_components.py
# Epic 1.7 / PR-D — Backward-Compatibility Shim (vollständig)
#
# Alle Klassen sind in fokussierte Module extrahiert worden:
#   - core/gemini_client.py    → GeminiModelWrapper, chat functions  (PR-A)
#   - core/news_processor.py   → NewsProcessor                       (PR-A)
#   - core/market_regime.py    → MarketRegimeModel                   (PR-A)
#   - core/market_scanner.py   → AIMarketScanner                     (PR-A)
#   - core/learning/engine.py  → AILearningEngine                    (PR-D)
#
# Alle bestehenden `from core.ai_components import X` Statements
# funktionieren weiterhin ohne Änderungen im restlichen Codebase.

from core.gemini_client import (  # noqa: F401
    GeminiModelWrapper,
    _get_available_models,
    _reply_indicates_insufficient,
    _using_new_genai_sdk,
    answer_chat_with_fallback,
    answer_trading_chat,
    answer_with_gemini_general,
    gemini_model_instance,
)
from core.learning.engine import AILearningEngine  # noqa: F401
from core.market_regime import MarketRegimeModel  # noqa: F401
from core.market_scanner import SCAN_CONCURRENCY, AIMarketScanner  # noqa: F401
from core.news_processor import NewsProcessor  # noqa: F401
