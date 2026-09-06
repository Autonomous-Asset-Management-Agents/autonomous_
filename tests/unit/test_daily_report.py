"""Unit tests for the automated daily report (private self-notification, Senior feature).

Covers the pure report formatter, the per-provider webhook payload, the Telegram +
SMTP senders, the dispatch routing, the schedule time-math, and env config loading.
No real network / SMTP — the senders are patched.
"""

import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from core.daily_report import (
    LEGAL_LINE,
    DailyReportConfig,
    SmtpConfig,
    build_report_text,
    dispatch_report,
    gather_metrics,
    load_config,
    run_once,
    run_scheduler,
    seconds_until_next,
    send_email,
    send_telegram,
    send_webhook,
)


def _cfg(**over):
    base = dict(
        times=["18:00"],
        webhook_enabled=False,
        webhook_provider="slack",
        webhook_url="",
        telegram_chat_id="",
        telegram_token="",
        email_enabled=False,
        recipient="",
        smtp_host="",
        smtp_port=465,
        smtp_user="",
        smtp_password="",
    )
    base.update(over)
    return DailyReportConfig(**base)


class TestBuildReportText:
    def test_neutral_mirror_with_legal_line(self):
        txt = build_report_text(
            equity=10250.4,
            day_pl_abs=128.75,
            decisions=3,
            fills=2,
            when=datetime(2026, 7, 14),
        )
        assert "autonomous_ — Daily Report" in txt
        assert "€10,250.40" in txt
        assert "+€128.75" in txt
        assert "Decisions today   3" in txt
        assert "Fills today       2" in txt
        assert LEGAL_LINE in txt
        # Neutral data mirror only — no advice/recommendation/target language (BaFin).
        for banned in ("buy", "sell", "target", "forecast", "recommend"):
            assert banned not in txt.lower()

    def test_negative_pl_and_missing_equity(self):
        txt = build_report_text(
            equity=None,
            day_pl_abs=-50.0,
            decisions=0,
            fills=0,
            when=datetime(2026, 7, 14),
        )
        assert (
            "—" in txt
        )  # missing equity renders as an em dash, never a fabricated value
        assert "-€50.00" in txt


class TestSenders:
    def test_slack_uses_text_key(self):
        with patch("core.daily_report._post_json", return_value=True) as p:
            send_webhook("slack", "https://hooks.slack.com/x", "hi")
        p.assert_called_once_with("https://hooks.slack.com/x", {"text": "hi"})

    def test_discord_uses_content_key(self):
        with patch("core.daily_report._post_json", return_value=True) as p:
            send_webhook("discord", "https://discord.com/api/webhooks/x", "hi")
        p.assert_called_once_with(
            "https://discord.com/api/webhooks/x", {"content": "hi"}
        )

    def test_telegram_builds_api_url(self):
        with patch("core.daily_report._post_json", return_value=True) as p:
            send_telegram("TOKEN", "42", "hi")
        p.assert_called_once_with(
            "https://api.telegram.org/botTOKEN/sendMessage",
            {"chat_id": "42", "text": "hi"},
        )

    def test_telegram_noop_without_token_or_chat(self):
        assert send_telegram("", "42", "hi") is False
        assert send_telegram("T", "", "hi") is False

    def test_post_json_rejects_non_https(self):
        from core.daily_report import _post_json

        assert _post_json("http://insecure/x", {"text": "hi"}) is False
        assert _post_json("", {"text": "hi"}) is False


class TestEmail:
    def test_ssl_port_465_logs_in_and_sends(self):
        with patch("core.daily_report.smtplib.SMTP_SSL") as SSL:
            inst = SSL.return_value.__enter__.return_value
            ok = send_email(
                SmtpConfig("smtp.x", 465, "u@x", "pw", "u@x", "me@x"), "S", "B"
            )
        assert ok is True
        inst.login.assert_called_once_with("u@x", "pw")
        inst.send_message.assert_called_once()

    def test_starttls_port_587(self):
        with patch("core.daily_report.smtplib.SMTP") as SMTP:
            inst = SMTP.return_value.__enter__.return_value
            ok = send_email(
                SmtpConfig("smtp.x", 587, "u@x", "pw", "u@x", "me@x"), "S", "B"
            )
        assert ok is True
        inst.starttls.assert_called_once()

    def test_noop_without_host_or_recipient(self):
        assert send_email(SmtpConfig("", 465, "", "", "", "me@x"), "S", "B") is False
        assert send_email(SmtpConfig("smtp.x", 465, "", "", "", ""), "S", "B") is False


class TestDispatch:
    def test_routes_webhook_when_provider_not_telegram(self):
        cfg = _cfg(
            webhook_enabled=True,
            webhook_provider="slack",
            webhook_url="https://hooks.slack.com/x",
        )
        with patch("core.daily_report.send_webhook", return_value=True) as w, patch(
            "core.daily_report.send_telegram", return_value=True
        ) as t:
            res = dispatch_report("hi", cfg)
        w.assert_called_once()
        t.assert_not_called()
        assert res == {"webhook": True}

    def test_routes_telegram_when_provider_telegram(self):
        cfg = _cfg(
            webhook_enabled=True,
            webhook_provider="telegram",
            telegram_token="T",
            telegram_chat_id="42",
        )
        with patch("core.daily_report.send_webhook") as w, patch(
            "core.daily_report.send_telegram", return_value=True
        ) as t:
            res = dispatch_report("hi", cfg)
        t.assert_called_once_with("T", "42", "hi")
        w.assert_not_called()
        assert res == {"telegram": True}

    def test_email_when_enabled(self):
        cfg = _cfg(email_enabled=True, smtp_host="smtp.x", recipient="me@x")
        with patch("core.daily_report.send_email", return_value=True) as e:
            res = dispatch_report("hi", cfg)
        e.assert_called_once()
        assert res == {"email": True}

    def test_nothing_when_all_disabled(self):
        assert dispatch_report("hi", _cfg()) == {}


class TestScheduleMath:
    def test_next_time_today(self):
        secs = seconds_until_next(["18:00"], datetime(2026, 7, 14, 8, 0, 0))
        assert secs == 10 * 3600

    def test_past_time_rolls_to_tomorrow(self):
        secs = seconds_until_next(["18:00"], datetime(2026, 7, 14, 20, 0, 0))
        assert secs == 22 * 3600

    def test_picks_earliest_of_several(self):
        secs = seconds_until_next(
            ["18:00", "09:00", "12:00"], datetime(2026, 7, 14, 8, 0, 0)
        )
        assert secs == 3600

    def test_empty_or_invalid_returns_none(self):
        assert seconds_until_next([], datetime(2026, 7, 14, 8, 0, 0)) is None
        assert seconds_until_next(["bad"], datetime(2026, 7, 14, 8, 0, 0)) is None


class TestLoadConfig:
    def test_reads_env(self):
        env = {
            "daily_report_times": "09:00, 18:00",
            "daily_report_webhook_enabled": "true",
            "daily_report_webhook_provider": "Telegram",
            "TELEGRAM_BOT_TOKEN": "T",
            "daily_report_telegram_chat_id": "42",
            "daily_report_email_enabled": "false",
            "smtp_port": "587",
            "WEBHOOK_URL": "https://hooks.slack.com/x",
        }
        cfg = load_config(getenv=lambda k, d="": env.get(k, d))
        assert cfg.times == ["09:00", "18:00"]
        assert cfg.webhook_enabled is True
        assert cfg.webhook_provider == "telegram"
        assert cfg.telegram_token == "T"
        assert cfg.smtp_port == 587
        assert cfg.email_enabled is False
        assert cfg.any_channel is True


# ── In-process engine fakes (metrics read directly from engine.api, no loopback HTTP) ──
class _FakeOrder:
    def __init__(self, status, filled_at):
        self.status = status
        self.filled_at = filled_at


class _FakeApi:
    def __init__(self, equity="10250.40", last_equity="10121.65", orders=None):
        self._acc = SimpleNamespace(equity=equity, last_equity=last_equity)
        self._orders = orders or []

    def get_account(self):
        return self._acc

    def get_orders(self, _req):
        return self._orders


class _FakeEngine:
    def __init__(self, **kw):
        self.api = _FakeApi(**kw)


_DECISIONS = "core.round_table.recent_decisions.get_recent_round_table_decisions"


class TestGatherMetrics:
    def test_account_equity_and_day_pl_and_decisions(self):
        eng = _FakeEngine(equity="10250.40", last_equity="10121.65", orders=[])
        with patch(_DECISIONS, return_value=[{"symbol": "AAPL"}, {"symbol": "MSFT"}]):
            m = gather_metrics(eng, now=datetime(2026, 7, 14, 18, 0, 0))
        assert m["equity"] == 10250.40
        assert m["day_pl_abs"] == round(
            10250.40 - 10121.65, 2
        )  # 128.75, from Alpaca last_equity
        assert m["decisions"] == 2
        assert m["fills"] == 0

    def test_counts_only_today_filled_orders(self):
        now = datetime(2026, 7, 14, 18, 0, 0)
        orders = [
            _FakeOrder("filled", now),  # today + filled → counts
            _FakeOrder("filled", datetime(2026, 7, 13, 10, 0, 0)),  # yesterday
            _FakeOrder("new", now),  # not filled
        ]
        with patch(_DECISIONS, return_value=[]):
            m = gather_metrics(_FakeEngine(orders=orders), now=now)
        assert m["fills"] == 1

    def test_failsoft_when_broker_raises(self):
        class _Boom:
            @property
            def api(self):
                raise RuntimeError("broker offline")

        with patch(_DECISIONS, return_value=[]):
            m = gather_metrics(_Boom(), now=datetime(2026, 7, 14))
        assert m == {"equity": None, "day_pl_abs": None, "decisions": 0, "fills": 0}


class TestRunOnce:
    def test_builds_from_engine_and_dispatches(self):
        eng = _FakeEngine(equity="100.00", last_equity="100.00", orders=[])
        cfg = _cfg(
            webhook_enabled=True,
            webhook_provider="slack",
            webhook_url="https://hooks.slack.com/x",
        )
        with patch("core.daily_report.load_config", return_value=cfg), patch(
            "core.daily_report.send_webhook", return_value=True
        ) as w, patch(_DECISIONS, return_value=[]):
            res = run_once(eng, datetime(2026, 7, 14, 18, 0, 0))
        w.assert_called_once()
        assert res == {"webhook": True}


class TestRunScheduler:
    def test_exits_immediately_when_shutdown_already_set(self):
        ev = threading.Event()
        ev.set()
        # Loop condition is false from the start → returns without touching the engine.
        run_scheduler(engine=object(), shutdown_event=ev)
