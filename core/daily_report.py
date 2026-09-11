"""Automated daily report — a neutral, private self-notification (Senior feature).

Builds a sober text summary of the operator's OWN account (portfolio value,
today's P/L, # decisions, # fills) and delivers it to the operator's OWN PRIVATE
channels — a chat webhook (Slack / Discord / Mattermost / Teams), Telegram, and/or
their own email (SMTP). It is never posted publicly and never routed through an
AAAgents server (BaFin decentralization + the No-Phone-Home ToS). Every message
carries the legal notice. Delivery is fail-silent per channel; secrets and the
webhook URL are never logged.

Secrets (WEBHOOK_URL / TELEGRAM_BOT_TOKEN / SMTP_PASSWORD) reach the engine via the
OS keychain (MANAGED_KEYS → os.environ); the non-secret config (schedule, provider,
recipient, SMTP host/port/user, enable flags) reaches it via setup.json → the
electron INJECTED_ENV_KEYS promotion. Both converge on os.getenv here.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Mirrors the console LEGAL_LINE — on EVERY message. BaFin: a private report of the
# user's own data must never read as advice.
LEGAL_LINE = (
    "Not investment advice. Past performance is not indicative of future results."
)
SUBJECT = "autonomous_ — Daily Report"

# Incoming-webhook payload key differs by provider: Discord uses "content", the
# Slack-compatible platforms (Slack / Mattermost / Teams) use "text".
_CONTENT_PROVIDERS = {"discord"}


def _fmt_eur(v: Optional[float]) -> str:
    return "—" if v is None else f"€{v:,.2f}"


def build_report_text(
    *,
    equity: Optional[float],
    day_pl_abs: Optional[float],
    decisions: int,
    fills: int,
    when: datetime,
) -> str:
    """Pure formatter — a neutral data mirror of the user's own account. No forecasts,
    no recommendations, no price targets; carries the legal notice as a footer."""
    pl = (
        "—"
        if day_pl_abs is None
        else f"{'+' if day_pl_abs >= 0 else '-'}{_fmt_eur(abs(day_pl_abs))}"
    )
    return "\n".join(
        [
            "autonomous_ — Daily Report",
            when.strftime("%b %d, %Y"),
            "",
            f"Portfolio value   {_fmt_eur(equity)}",
            f"Today's P/L       {pl}",
            f"Decisions today   {decisions}",
            f"Fills today       {fills}",
            "",
            LEGAL_LINE,
            "Prepared locally on your device — sent only to the private channel you chose.",
        ]
    )


def _post_json(url: str, payload: dict) -> bool:
    """HTTPS-only JSON POST (mirrors scripts/aut9_notify.py). Never raises; never logs the URL."""
    if not url or not url.lower().startswith("https://"):
        logger.warning("daily-report: delivery URL missing or not https — skipping.")
        return False
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 — scheme checked above
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except (
        Exception
    ) as e:  # noqa: BLE001 — fail-silent; never echo the URL/response body
        logger.warning("daily-report: webhook delivery failed (%s).", type(e).__name__)
        return False


def send_webhook(provider: str, url: str, text: str) -> bool:
    """Slack/Discord/Mattermost/Teams incoming webhook — a plain text message."""
    key = "content" if provider in _CONTENT_PROVIDERS else "text"
    return _post_json(url, {key: text})


def send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
    if not bot_token or not chat_id:
        return False
    return _post_json(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        {"chat_id": chat_id, "text": text},
    )


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipient: str


def send_email(cfg: SmtpConfig, subject: str, body: str) -> bool:
    """Send via the user's OWN SMTP account (stdlib). Fail-silent; never logs the password."""
    if not (cfg.host and cfg.recipient):
        return False
    try:
        msg = EmailMessage()
        msg["From"] = cfg.sender or cfg.user or cfg.recipient
        msg["To"] = cfg.recipient
        msg["Subject"] = subject
        msg.set_content(body)
        if int(cfg.port) == 465:  # implicit TLS
            with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=10) as s:
                if cfg.user:
                    s.login(cfg.user, cfg.password)
                s.send_message(msg)
        else:  # STARTTLS (587 and friends)
            with smtplib.SMTP(cfg.host, cfg.port, timeout=10) as s:
                s.starttls()
                if cfg.user:
                    s.login(cfg.user, cfg.password)
                s.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001 — fail-silent; never log the password
        logger.warning("daily-report: email delivery failed (%s).", type(e).__name__)
        return False


@dataclass
class DailyReportConfig:
    times: list
    webhook_enabled: bool
    webhook_provider: str
    webhook_url: str
    telegram_chat_id: str
    telegram_token: str
    email_enabled: bool
    recipient: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str

    @property
    def any_channel(self) -> bool:
        return self.webhook_enabled or self.email_enabled


def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def load_config(
    getenv: Callable[[str, str], Optional[str]] = os.getenv,
) -> DailyReportConfig:
    """Assemble the config from env (secrets via keychain, non-secrets via setup.json→env)."""
    times = [
        t.strip()
        for t in (getenv("daily_report_times", "") or "").split(",")
        if t.strip()
    ]
    try:
        port = int(getenv("smtp_port", "465") or "465")
    except ValueError:
        port = 465
    return DailyReportConfig(
        times=times,
        webhook_enabled=_truthy(getenv("daily_report_webhook_enabled", "")),
        webhook_provider=(
            getenv("daily_report_webhook_provider", "slack") or "slack"
        ).lower(),
        webhook_url=getenv("WEBHOOK_URL", "") or "",
        telegram_chat_id=getenv("daily_report_telegram_chat_id", "") or "",
        telegram_token=getenv("TELEGRAM_BOT_TOKEN", "") or "",
        email_enabled=_truthy(getenv("daily_report_email_enabled", "")),
        recipient=getenv("report_recipient_email", "") or "",
        smtp_host=getenv("smtp_host", "") or "",
        smtp_port=port,
        smtp_user=getenv("smtp_user", "") or "",
        smtp_password=getenv("SMTP_PASSWORD", "") or "",
    )


def dispatch_report(text: str, cfg: DailyReportConfig) -> dict:
    """Fan the report out to every enabled PRIVATE channel. Returns per-channel success."""
    results: dict = {}
    if cfg.webhook_enabled:
        if cfg.webhook_provider == "telegram":
            results["telegram"] = send_telegram(
                cfg.telegram_token, cfg.telegram_chat_id, text
            )
        else:
            results["webhook"] = send_webhook(
                cfg.webhook_provider, cfg.webhook_url, text
            )
    if cfg.email_enabled:
        results["email"] = send_email(
            SmtpConfig(
                host=cfg.smtp_host,
                port=cfg.smtp_port,
                user=cfg.smtp_user,
                password=cfg.smtp_password,
                sender=cfg.smtp_user,
                recipient=cfg.recipient,
            ),
            SUBJECT,
            text,
        )
    return results


def seconds_until_next(times: list, now: datetime) -> Optional[float]:
    """Seconds from `now` until the next daily HH:MM in `times`; None if none are valid."""
    best: Optional[float] = None
    for t in times:
        try:
            hh, mm = str(t).split(":")
            target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except (ValueError, AttributeError):
            continue
        if target <= now:
            target += timedelta(days=1)
        secs = (target - now).total_seconds()
        best = secs if best is None else min(best, secs)
    return best


def _account_metrics(engine) -> tuple:
    """(equity, day_pl_abs) from the engine's OWN broker client — in-process, no HTTP/auth.
    Alpaca's account carries `last_equity` (prior close), so day P/L is a direct subtraction
    (mirrors what /portfolio-summary reads internally, api_routes.py:1678)."""
    try:
        acc = engine.api.get_account()
    except (
        Exception
    ):  # noqa: BLE001 — broker unavailable → honest None, never fabricated
        return None, None
    try:
        equity = float(acc.equity) if getattr(acc, "equity", None) is not None else None
    except (TypeError, ValueError):
        equity = None
    day_pl_abs = None
    last_eq = getattr(acc, "last_equity", None)
    if equity is not None and last_eq is not None:
        try:
            day_pl_abs = round(equity - float(last_eq), 2)
        except (TypeError, ValueError):
            day_pl_abs = None
    return equity, day_pl_abs


def _fills_today(engine, today) -> int:
    """Count today's FILLED orders via the engine's OWN broker client (in-process,
    mirrors /recent-trades, api_routes.py:1497-1501)."""
    try:
        from alpaca.trading.enums import (  # lazy — keep the module import-safe
            QueryOrderStatus,
        )
        from alpaca.trading.requests import GetOrdersRequest

        orders = engine.api.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
        )
    except Exception:  # noqa: BLE001
        return 0
    count = 0
    for o in orders or []:
        if str(getattr(o, "status", "")).lower() != "filled":
            continue
        fa = getattr(o, "filled_at", None)
        if fa is None:
            continue
        try:
            d = fa.date() if hasattr(fa, "date") else None
            if (d == today) if d is not None else (str(fa)[:10] == today.isoformat()):
                count += 1
        except Exception:  # noqa: BLE001
            continue
    return count


def _decisions_count() -> int:
    """Count recent Round-Table decisions (latest per symbol), in-process — the same store
    /round-table-decisions serves (api_routes.py:2443-2445)."""
    try:
        from core.round_table.recent_decisions import get_recent_round_table_decisions

        return len(get_recent_round_table_decisions(limit=200) or [])
    except Exception:  # noqa: BLE001
        return 0


def gather_metrics(engine, now: Optional[datetime] = None) -> dict:
    """Read the neutral own-account metrics DIRECTLY from the engine object (same process) —
    NO loopback HTTP, NO auth signature (the report endpoints require verify_user_id_sig).
    Fail-soft: missing data → None/0, never a fabricated value (ADR-017 fail-closed)."""
    today = (now or datetime.now()).date()
    equity, day_pl_abs = _account_metrics(engine)
    return {
        "equity": equity,
        "day_pl_abs": day_pl_abs,
        "decisions": _decisions_count(),
        "fills": _fills_today(engine, today),
    }


def run_once(engine, now: datetime) -> dict:
    """Build the report from live own-account data and deliver it. Returns per-channel results."""
    text = build_report_text(when=now, **gather_metrics(engine, now))
    return dispatch_report(text, load_config())


def run_scheduler(
    engine,
    shutdown_event,
    now_fn: Callable[[], datetime] = datetime.now,
    idle_wait: float = 300.0,
) -> None:
    """Daemon loop: sleep until the next configured HH:MM, then build + deliver the report
    to the user's own private channels. Reads metrics IN-PROCESS from `engine` (no loopback
    HTTP). Interruptible via `shutdown_event` (mirrors core/engine/news_poller.py). Never
    raises out of the loop."""
    logger.info("daily-report: scheduler started.")
    while not shutdown_event.is_set():
        cfg = load_config()
        if not cfg.times or not cfg.any_channel:
            if shutdown_event.wait(idle_wait):  # not configured (yet) — re-check later
                break
            continue
        delay = seconds_until_next(cfg.times, now_fn())
        if delay is None:
            if shutdown_event.wait(idle_wait):
                break
            continue
        if shutdown_event.wait(delay):
            break  # woke because we are shutting down, not because the time arrived
        try:
            run_once(engine, now_fn())
        except Exception as e:  # noqa: BLE001 — never let a bad run kill the thread
            logger.warning("daily-report: run failed (%s).", type(e).__name__)
        if shutdown_event.wait(61):  # guard against re-firing within the same minute
            break
    logger.info("daily-report: scheduler stopped.")
