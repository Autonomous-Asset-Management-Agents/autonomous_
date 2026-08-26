# core/engine/equity_fallback.py
# BUG-AI-S01 (#1232): the engine must never size positions off a hardcoded
# fictional equity. resolve_equity() returns the live account equity, or the
# configured DEFAULT_EQUITY with a WARNING when the broker is unavailable / the
# fetch fails / returns a non-positive value — never a silent made-up number
# (CLAUDE.md §5.6 — fallbacks are always logged at WARNING).
import logging


def resolve_equity(api, default_equity) -> float:
    """Live account equity, or ``default_equity`` (config ``DEFAULT_EQUITY``) with
    a WARNING when it cannot be read. Never returns a silent fictional value."""
    default = float(default_equity)
    if api is None:
        logging.warning(
            "equity: no broker client — sizing on DEFAULT_EQUITY (%.2f). "
            "Set config DEFAULT_EQUITY to your real account size.",
            default,
        )
        return default
    try:
        acc = api.get_account()
        eq = float(getattr(acc, "equity", 0) or 0)
        if eq > 0:
            return eq
        logging.warning(
            "equity: broker returned non-positive equity — using DEFAULT_EQUITY (%.2f).",
            default,
        )
        return default
    except Exception as e:
        logging.warning(
            "equity: account fetch failed — using DEFAULT_EQUITY (%.2f): %s",
            default,
            e,
        )
        return default
