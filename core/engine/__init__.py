# core/engine/__init__.py
# Epic 1.7 / PR-C — Package-Init-Shim
# Re-exportiert BotEngine, app und engine für Backward-Kompatibilität

from .api_routes import app, engine
from .base import BotEngine

__all__ = ["BotEngine", "app", "engine"]
