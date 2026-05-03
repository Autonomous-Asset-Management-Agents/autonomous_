# cloud_logger.py
# SQLAlchemy Cloud Logger with async batching, fallback, and LLM-queryable decision logging
# Version: 2.0 (GCP Cloud SQL Native)

import os
import json
import logging
import os
import requests
import threading
import queue
import time
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Union
from dataclasses import dataclass, asdict, field
from enum import Enum

# SQLAlchemy + ORM imports — guarded for CI environments without asyncpg/psycopg2
try:
    from sqlalchemy import delete, select
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker
    from core.database.session import AsyncSessionLocal, _create_engine
    from core.database.models import (
        Decision,
        Trade,
        AIThought,
        RiskEvent,
        MifidDecisionLog,
        PortfolioSnapshot,
        RoundTableSession,
    )

    DB_AVAILABLE = True
except (ImportError, Exception) as e:
    logging.warning(f"Database ORM unavailable: {e}")
    DB_AVAILABLE = False
    AsyncSessionLocal = None  # type: ignore[assignment]
    Decision = Trade = AIThought = RiskEvent = MifidDecisionLog = PortfolioSnapshot = RoundTableSession = None  # type: ignore[assignment,misc]


# Try to import OpenTelemetry
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

    if not hasattr(trace.get_tracer_provider(), "add_span_processor"):
        provider = TracerProvider()
        cloud_trace_exporter = CloudTraceSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(cloud_trace_exporter))
        trace.set_tracer_provider(provider)
    OTEL_AVAILABLE = True
except Exception as e:
    OTEL_AVAILABLE = False
    logging.warning(f"Google Cloud Trace Exporter unavailable: {e}")


class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class DecisionContext:
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    decision_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    model_version_id: str = "unknown"
    action: str = "HOLD"
    action_executed: bool = False
    lstm_prediction: float = 0.0
    rl_raw_action: int = 0
    rl_stabilized_action: int = 0
    conviction_score: float = 0.0
    current_price: float = 0.0
    vix_level: float = 20.0
    market_regime: str = "normal"
    rsi_14: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    adx_14: float = 25.0
    bb_pct: float = 0.5
    volume_ratio: float = 1.0
    volatility_20d: float = 0.02
    atr_14d: float = 0.0
    in_position: bool = False
    position_qty: float = 0.0
    position_avg_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    risk_approved: bool = True
    risk_reason: str = ""
    risk_size_scaler: float = 1.0
    risk_sl_multiplier: float = 3.0
    portfolio_approved: bool = True
    portfolio_reason: str = ""
    portfolio_slot_used: int = 0
    portfolio_max_slots: int = 10
    symbol_to_close: str = ""
    intelligence_approved: bool = True
    intelligence_reason: str = ""
    reasoning_summary: str = ""
    reasoning_trace: Optional[str] = None
    trade_id: str = ""
    execution_price: float = 0.0
    execution_qty: float = 0.0
    inference_latency_ms: float = 0.0
    is_simulation: bool = False
    triggered_by_stop: bool = False
    stop_type: str = ""

    def build_reasoning_summary(self) -> str:
        parts = []
        if self.action == "BUY":
            parts.append(f"BOUGHT {self.symbol} at ${self.current_price:.2f}")
            parts.append(f"LSTM predicted {self.lstm_prediction:.2f} (bullish signal)")
            parts.append(f"Conviction={self.conviction_score:.2f}")
            if self.symbol_to_close:
                parts.append(f"Close: {self.symbol_to_close}")
        elif self.action == "SELL":
            if self.triggered_by_stop:
                parts.append(f"SOLD {self.symbol} due to {self.stop_type} stop")
            else:
                parts.append(f"SOLD {self.symbol} at ${self.current_price:.2f}")
                parts.append(f"Conviction={self.conviction_score:.2f}")
            if self.unrealized_pnl is not None:
                parts.append(f"P&L: {self.unrealized_pnl:.2f}")
        else:
            parts.append(f"HELD on {self.symbol}")
            reasons = []
            if abs(self.lstm_prediction) < 0.5:
                reasons.append(f"LSTM neutral ({self.lstm_prediction:.2f})")
            if self.adx_14 is not None and self.adx_14 < 20:
                reasons.append(f"ADX={self.adx_14:.1f} (weak trend)")
            if reasons:
                parts.append("Reasons: " + ", ".join(reasons))

        techs = [f"RSI={self.rsi_14:.1f}", f"MACD={self.macd:.3f}"]
        parts.append("Technicals: " + ", ".join(techs))

        if self.vix_level is not None:
            parts.append(f"VIX={self.vix_level:.1f}")

        if not self.risk_approved:
            parts.append(f"⚠️ Risk blocked: {self.risk_reason}")
        if not self.portfolio_approved:
            parts.append(f"⚠️ Portfolio blocked: {self.portfolio_reason}")

        self.reasoning_summary = " | ".join(parts)
        return self.reasoning_summary

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if isinstance(d["decision_time"], datetime):
            d["decision_time"] = d["decision_time"].isoformat()
        return d


@dataclass
class TradeRecord:
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    side: str = ""
    qty: float = 0.0
    price: float = 0.0
    total_value: float = 0.0
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    entry_price: float = 0.0
    position_pnl: float = 0.0
    position_pnl_pct: float = 0.0
    hold_duration_hours: float = 0.0
    order_type: str = "market"
    time_in_force: str = "day"
    order_status: str = "filled"
    account_id: str = ""
    strategy_name: str = "RLAgent"
    is_simulation: bool = False
    decision_id: str = ""  # Added to link back to the Decision

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if isinstance(d["executed_at"], datetime):
            d["executed_at"] = d["executed_at"].isoformat()
        return d


class GcpJsonFormatter(logging.Formatter):
    LEVEL_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record):
        log_entry = {
            "severity": self.LEVEL_MAP.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "logging.googleapis.com/sourceLocation": {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            },
        }
        if OTEL_AVAILABLE:
            span = trace.get_current_span()
            if span and span.get_span_context().is_valid:
                ctx = span.get_span_context()
                project_id = os.getenv("GCP_PROJECT_ID", "aaagents-oss")
                log_entry["logging.googleapis.com/trace"] = (
                    f"projects/{project_id}/traces/{format(ctx.trace_id, '032x')}"
                )
                log_entry["logging.googleapis.com/spanId"] = format(ctx.span_id, "016x")

        if hasattr(record, "details"):
            log_entry["details"] = record.details
        return json.dumps(log_entry)


class SlackWebhookHandler(logging.Handler):
    """
    Sends CRITICAL log messages to a Slack/Discord Webhook.
    Fails silently so that the main application thread never crashes.
    """

    def __init__(self, webhook_url: str):
        super().__init__(level=logging.CRITICAL)
        self.webhook_url = webhook_url

    def emit(self, record):
        try:
            msg = self.format(record)
            payload = {"content": f"🚨 **CRITICAL ALERT** 🚨\n```\n{msg}\n```"}
            # Fire and forget request with short timeout
            requests.post(self.webhook_url, json=payload, timeout=2.0)
        except Exception:
            # Must fail silently to prevent cascading failures
            pass


def setup_logging(level=logging.INFO):
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    if os.getenv("K_SERVICE") or os.getenv("FORCE_JSON_LOGGING") == "true":
        handler.setFormatter(GcpJsonFormatter())
    else:
        fmt = "%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
        handler.setFormatter(logging.Formatter(fmt))

    root_logger.addHandler(handler)

    # Attach Slack/Discord Webhook Handler if configured
    slack_url = os.getenv("SLACK_WEBHOOK_URL")
    if slack_url:
        slack_handler = SlackWebhookHandler(webhook_url=slack_url)
        # Use simple formatter for slack
        slack_handler.setFormatter(
            logging.Formatter("%(asctime)s - [%(filename)s:%(lineno)d] - %(message)s")
        )
        root_logger.addHandler(slack_handler)

    # NOTE: OTel TracerProvider is now managed exclusively by core.telemetry.
    # Do NOT re-initialise it here — cloud_logger merely reads the current
    # provider via trace.get_current_span() / trace.get_tracer().


def _iso_to_dt(iso_str):
    """Helper to safely parse ISO strings to timezone-aware UTC datetime."""
    if isinstance(iso_str, datetime):
        return iso_str.replace(tzinfo=timezone.utc) if not iso_str.tzinfo else iso_str
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


class CloudLogger:
    """
    SQLAlchemy ORM Cloud Logger with async batching and local fallback.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, **kwargs):
        if self._initialized:
            return

        self._initialized = True
        self.is_connected = False

        self.batch_size = 50
        self.batch_interval = 5.0

        self._decision_queue = queue.Queue()
        self._trade_queue = queue.Queue()
        self._thought_queue = queue.Queue()
        self._event_queue = queue.Queue()
        self._compliance_queue = queue.Queue()
        self._senate_queue = queue.Queue()

        self._stop_event = threading.Event()
        self._worker_thread = None

        self.fallback_dir = "cloud_fallback_logs"
        os.makedirs(self.fallback_dir, exist_ok=True)

        self.stats = {
            "decisions_logged": 0,
            "trades_logged": 0,
            "thoughts_logged": 0,
            "events_logged": 0,
            "errors": 0,
            "fallback_writes": 0,
        }

        self._connect()
        self._start_worker()
        logging.info(f"ORM CloudLogger initialized. Connected: {self.is_connected}")

    def _connect(self) -> bool:
        if not DB_AVAILABLE:
            logging.error("Failed to connect to Cloud SQL: ORM unavailable.")
            return False

        self.is_connected = True
        return True

    def _start_worker(self):
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._run_async_worker, daemon=True, name="CloudLoggerWorker"
        )
        self._worker_thread.start()
        logging.debug("CloudLogger worker over async event loop started")

    def _run_async_worker(self):
        """Bridge between sync thread and async ORM inserts."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        if DB_AVAILABLE:
            self.thread_engine = _create_engine()
            self.thread_session_maker = sessionmaker(
                bind=self.thread_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

        try:
            loop.run_until_complete(self._async_worker_loop())
        except Exception as e:
            logging.error(f"Event loop crash in CloudLogger: {e}")
        finally:
            if DB_AVAILABLE and hasattr(self, "thread_engine"):
                loop.run_until_complete(self.thread_engine.dispose())
                from core.database.session import cleanup_engine_connector

                loop.run_until_complete(cleanup_engine_connector(self.thread_engine))
            loop.close()

    async def _async_worker_loop(self):
        last_thoughts_flush = time.time()

        while not self._stop_event.is_set():
            try:
                current_time = time.time()

                # Process Thoughts
                thoughts_buffer = []
                while not self._thought_queue.empty():
                    try:
                        thoughts_buffer.append(self._thought_queue.get_nowait())
                    except queue.Empty:
                        break

                if thoughts_buffer and (
                    current_time - last_thoughts_flush >= self.batch_interval
                    or len(thoughts_buffer) >= self.batch_size
                ):
                    await self._send_batch("ai_thoughts", thoughts_buffer)
                    last_thoughts_flush = current_time

                # Process Risk Events
                events = []
                while not self._event_queue.empty():
                    try:
                        events.append(self._event_queue.get_nowait())
                    except queue.Empty:
                        break
                if events:
                    await self._send_batch("risk_events", events)

                # Process Compliance
                c_events = []
                while not self._compliance_queue.empty():
                    try:
                        c_events.append(self._compliance_queue.get_nowait())
                    except queue.Empty:
                        break
                if c_events:
                    await self._send_batch("mifid_decision_log", c_events)

                # Process RoundTable Sessions
                senate_items = []
                while not self._senate_queue.empty():
                    try:
                        senate_items.append(self._senate_queue.get_nowait())
                    except queue.Empty:
                        break
                if senate_items:
                    await self._send_batch("round_table_sessions", senate_items)

                await asyncio.sleep(0.5)

            except Exception as e:
                try:
                    logging.error("CloudLogger worker error: %s", e)
                except ValueError:
                    pass
                self.stats["errors"] += 1
                await asyncio.sleep(1)

    async def _send_batch(self, table_name: str, items: List[Dict]):
        if not items:
            return

        if (
            not DB_AVAILABLE
            or not self.is_connected
            or not hasattr(self, "thread_session_maker")
        ):
            self._write_fallback(table_name, items)
            return

        try:
            async with self.thread_session_maker() as session:
                async with session.begin():
                    # Set up OpenTelemetry Trace — must use `with ... as span:` to get the actual Span
                    span_cm = None
                    if OTEL_AVAILABLE:
                        tracer = trace.get_tracer(__name__)
                        span_cm = tracer.start_as_current_span(
                            f"postgresql.insert.{table_name}"
                        )

                    # Enter span context (or a no-op if OTel not available)
                    from contextlib import nullcontext

                    active_span_ctx = span_cm if span_cm is not None else nullcontext()

                    with active_span_ctx as span:
                        if span is not None and OTEL_AVAILABLE:
                            try:
                                span.set_attribute("db.system", "postgresql")
                                span.set_attribute("db.operation", "insert")
                                span.set_attribute("db.name", table_name)
                                span.set_attribute(
                                    "db.statement",
                                    f"INSERT {len(items)} records into table '{table_name}'",
                                )
                                if table_name == "decisions" and items:
                                    summary = ",".join(
                                        [
                                            f"{i.get('symbol')}={i.get('action')}"
                                            for i in items[:3]
                                        ]
                                    )
                                    span.set_attribute("bot.decision", summary)
                            except Exception:
                                pass  # OTel attribute errors must never break inserts

                        try:
                            for item in items:
                                if table_name == "decisions":
                                    item["decision_time"] = _iso_to_dt(
                                        item.get("decision_time")
                                    )
                                    stmt = (
                                        insert(Decision)
                                        .values(**item)
                                        .on_conflict_do_nothing(
                                            index_elements=["decision_id"]
                                        )
                                    )
                                    await session.execute(stmt)
                                elif table_name == "trades":
                                    item["executed_at"] = _iso_to_dt(
                                        item.get("executed_at")
                                    )
                                    stmt = (
                                        insert(Trade)
                                        .values(**item)
                                        .on_conflict_do_nothing(
                                            index_elements=["trade_id"]
                                        )
                                    )
                                    await session.execute(stmt)
                                elif table_name == "ai_thoughts":
                                    item["thought_time"] = _iso_to_dt(
                                        item.get("thought_time")
                                    )
                                    stmt = (
                                        insert(AIThought)
                                        .values(**item)
                                        .on_conflict_do_nothing(index_elements=["id"])
                                    )
                                    await session.execute(stmt)
                                elif table_name == "risk_events":
                                    item["event_time"] = _iso_to_dt(
                                        item.get("event_time")
                                    )
                                    stmt = (
                                        insert(RiskEvent)
                                        .values(**item)
                                        .on_conflict_do_nothing(index_elements=["id"])
                                    )
                                    await session.execute(stmt)
                                elif table_name == "mifid_decision_log":
                                    item["event_time"] = _iso_to_dt(
                                        item.get("event_time")
                                    )
                                    stmt = (
                                        insert(MifidDecisionLog)
                                        .values(**item)
                                        .on_conflict_do_nothing(index_elements=["id"])
                                    )
                                    await session.execute(stmt)
                                elif table_name == "round_table_sessions":
                                    item["session_time"] = _iso_to_dt(
                                        item.get("session_time")
                                    )
                                    stmt = (
                                        insert(RoundTableSession)
                                        .values(**item)
                                        .on_conflict_do_nothing(
                                            index_elements=["session_id"]
                                        )
                                    )
                                    await session.execute(stmt)
                        except Exception as insert_err:
                            raise  # Re-raise so outer except block catches it

                # Update stats
                if table_name == "decisions":
                    self.stats["decisions_logged"] += len(items)
                elif table_name == "trades":
                    self.stats["trades_logged"] += len(items)
                elif table_name == "ai_thoughts":
                    self.stats["thoughts_logged"] += len(items)
                elif table_name == "risk_events":
                    self.stats["events_logged"] += len(items)
                elif table_name == "mifid_decision_log":
                    self.stats["compliance_logged"] = self.stats.get(
                        "compliance_logged", 0
                    ) + len(items)
                elif table_name == "round_table_sessions":
                    self.stats["senate_sessions_logged"] = self.stats.get(
                        "senate_sessions_logged", 0
                    ) + len(items)

                logging.info(
                    f"☁️ Successfully stored {len(items)} items to {table_name}"
                )

        except Exception as e:
            try:
                err_msg = str(e)
                if len(err_msg) > 300:
                    err_msg = (
                        err_msg[:300] + "... [TRUNCATED to prevent Cloud Logging Spam]"
                    )
                logging.error(
                    "❌ Failed ORM batch insert to %s: %s", table_name, err_msg
                )
            except ValueError:
                pass  # Ignore "I/O operation on closed file" during shutdown
            self.stats["errors"] += 1
            self._write_fallback(table_name, items)

    def _write_fallback(self, table_name: str, items: List[Dict]):
        try:
            filename = os.path.join(
                self.fallback_dir,
                f"{table_name}_{datetime.now().strftime('%Y%m%d')}.jsonl",
            )
            with open(filename, "a", encoding="utf-8") as f:
                for item in items:
                    f.write(json.dumps(item, default=str) + "\n")
            self.stats["fallback_writes"] += len(items)
        except Exception as e:
            try:
                logging.error("Failed to write fallback for %s: %s", table_name, e)
            except ValueError:
                pass

    # -------------------------------------------------------------------------
    # PUBLIC API
    # -------------------------------------------------------------------------
    def log_decision(self, context: DecisionContext):
        if not context.reasoning_summary:
            context.build_reasoning_summary()
        if not context.reasoning_trace:
            context.reasoning_trace = context.reasoning_summary

        self._decision_queue.put(context.to_dict())

    def log_trade(self, trade: TradeRecord):
        self._trade_queue.put(trade.to_dict())

    def log_senate_session(
        self,
        session_id: str,
        symbol: str,
        consensus_score: float,
        signal_action: Optional[str],
        gatekeeper_approved: bool,
        gatekeeper_reason: str,
        votes: list,
        vote_count: int,
        is_simulation: bool = False,
    ) -> None:
        """Persist one RoundTable evaluation to Cloud SQL.

        Called by SenateProtocol after every symbol evaluation — regardless
        of whether the bot buys, sells, holds, or does nothing. This is the
        primary source for ML training and accountability queries.

        votes: list of dicts with keys agent_name, score, weight, reasoning, vetoed
        """
        self._senate_queue.put(
            {
                "session_id": session_id,
                "session_time": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "consensus_score": consensus_score,
                "signal_action": signal_action or "NONE",
                "gatekeeper_approved": gatekeeper_approved,
                "gatekeeper_reason": gatekeeper_reason,
                "vote_count": vote_count,
                "votes_json": votes,
                "is_simulation": is_simulation,
            }
        )

    def log_thought(
        self,
        symbol: str,
        message: str,
        thought_type: str = "analysis",
        context: Dict = None,
        is_simulation: bool = False,
    ):
        self._thought_queue.put(
            {
                "id": str(uuid.uuid4()),
                "thought_time": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "thought_type": thought_type,
                "message": message,
                "context_json": context or {},
                "is_simulation": is_simulation,
            }
        )

    def log_risk_event(
        self,
        event_type: str,
        severity: str,
        message: str,
        trigger_value: float = None,
        threshold_value: float = None,
        equity: float = None,
        details: Dict = None,
        is_simulation: bool = False,
    ):
        self._event_queue.put(
            {
                "id": str(uuid.uuid4()),
                "event_time": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "severity": severity,
                "message": message,
                "trigger_value": trigger_value,
                "threshold_value": threshold_value,
                "equity_at_event": equity,
                "details_json": details or {},
                "is_simulation": is_simulation,
            }
        )

    def log_portfolio_snapshot(self, snapshot: Dict):
        # Fire-and-forget sync wrapper
        def _bg_insert():
            async def _do():
                await self._send_batch("portfolio_snapshots", [snapshot])

            try:
                asyncio.run(_do())
            except Exception:
                self._write_fallback("portfolio_snapshots", [snapshot])

        threading.Thread(target=_bg_insert, daemon=True).start()

    def log_compliance_event(
        self,
        order: Dict,
        approved: bool,
        reason: str,
        check_latency_ms: float = 0,
        is_simulation: bool = False,
    ):
        self._compliance_queue.put(
            {
                "id": str(uuid.uuid4()),
                "event_time": datetime.now(timezone.utc).isoformat(),
                "event_type": "compliance_check",
                "severity": "info" if approved else "warning",
                "message": f"{'APPROVED' if approved else 'BLOCKED'}: {order.get('symbol', '?')} {order.get('side', '?')} qty={order.get('quantity', 0)} — {reason}",
                "user_id": order.get("user_id"),
                "trigger_value": order.get("quantity", 0),
                "threshold_value": check_latency_ms,
                "equity_at_event": order.get("price", 0) * order.get("quantity", 0),
                "details_json": {
                    "order": order,
                    "approved": approved,
                    "reason": reason,
                    "check_latency_ms": round(check_latency_ms, 2),
                },
                "is_simulation": is_simulation,
            }
        )

    def log_latency_metric(
        self,
        total_ms: float,
        data_fetch_ms: float,
        strategy_exec_ms: float,
        symbol_count: int,
    ):
        self._event_queue.put(
            {
                "id": str(uuid.uuid4()),
                "event_time": datetime.now(timezone.utc).isoformat(),
                "event_type": "performance_metric",
                "severity": "info" if total_ms < 2000 else "warning",
                "message": f"Cycle latency: {total_ms:.1f}ms for {symbol_count} symbols",
                "trigger_value": total_ms,
                "threshold_value": 2000.0,
                "details_json": {
                    "total_ms": total_ms,
                    "data_fetch_ms": data_fetch_ms,
                    "strategy_exec_ms": strategy_exec_ms,
                    "symbol_count": symbol_count,
                },
            }
        )

    def log_swap_event(
        self, strategy_name: str, shadow_mode: bool = False, forced: bool = False
    ):
        self.log_risk_event(
            event_type="strategy_swap",
            severity="info",
            message=f"Hot-Swap zu '{strategy_name}' initiiert (shadow_mode={shadow_mode}, forced={forced})",
            details={
                "strategy_name": strategy_name,
                "shadow_mode": shadow_mode,
                "forced": forced,
                "mifid_note": "Strategy switch logged for RTS 6 audit trail.",
            },
        )

    def flush(self):
        """Force flush all pending sync queues into async ORM layer."""

        async def _flush_all():
            for q, table_name in [
                (self._decision_queue, "decisions"),  # Decisions MUST proceed trades!
                (self._trade_queue, "trades"),
                (self._thought_queue, "ai_thoughts"),
                (self._event_queue, "risk_events"),
                (self._compliance_queue, "mifid_decision_log"),
                (self._senate_queue, "round_table_sessions"),
            ]:
                items = []
                while not q.empty():
                    try:
                        items.append(q.get_nowait())
                    except queue.Empty:
                        break
                if items:
                    await self._send_batch(table_name, items)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop and loop.is_running():
                # We are inside an async context (like the integration test)
                loop.create_task(_flush_all())
            else:
                # We are in a synchronous thread (like the bot main thread)
                asyncio.run(_flush_all())
        except Exception as e:
            logging.error(f"Failed to flush: {e}")

    def shutdown(self):
        self.flush()
        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=5)

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "is_connected": self.is_connected,
            "pending_decisions": self._decision_queue.qsize(),
            "pending_trades": self._trade_queue.qsize(),
            "pending_thoughts": self._thought_queue.qsize(),
            "pending_events": self._event_queue.qsize(),
            "pending_compliance": self._compliance_queue.qsize(),
        }


# Legacy alias removed, no DB_AVAILABLE flag anymore.
logger_instance = CloudLogger()
_cloud_logger = logger_instance  # alias for test patching compatibility


def get_cloud_logger():
    """Returns the singleton instance of the CloudLogger."""
    global logger_instance, _cloud_logger
    return _cloud_logger if _cloud_logger is not None else logger_instance
