import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone

import httpx

from core.audit_paths import migrate_legacy_audit_log, resolve_audit_log_path
from core.notifier import send_slack_alert
from core.redis_client import RedisClient

# Durable audit sink for kill-switch trip/reset — mirrors compliance.py's pattern so a
# trip/reset is auditable even when Redis/Slack are down. A separate JSONL file
# (kill_switch_audit.log) keeps the halt trail readable in isolation. Dir-artifact →
# StreamHandler fallback handles the Docker case where the path is a leftover directory.
_ks_audit_logger = logging.getLogger("KillSwitchAudit")
_ks_audit_logger.setLevel(logging.INFO)
_ks_audit_logger.propagate = False
# ADR-C08 (see core/compliance.py): kill_switch_audit.log follows the SAME
# cwd-relative pattern as compliance_audit.log (this module's comment above says it
# mirrors compliance.py) — so it gets the identical #2586 treatment: durable under
# USER_DATA_DIR, one-time migration of a bundle-relative leftover, cloud/dev
# (env unset) byte-identical.
_ks_audit_path = resolve_audit_log_path("kill_switch_audit.log")
migrate_legacy_audit_log("kill_switch_audit.log", _ks_audit_path)
if os.path.isdir(_ks_audit_path):
    _ks_audit_logger.addHandler(logging.StreamHandler())
else:
    try:
        _ks_file_handler = logging.FileHandler(_ks_audit_path)
        _ks_file_handler.setFormatter(logging.Formatter("%(message)s"))
        _ks_audit_logger.addHandler(_ks_file_handler)
    except Exception:
        _ks_audit_logger.addHandler(logging.StreamHandler())


#: #3449 Schritt 5: der Trip-Satz liegt neben dem Halt in derselben Ablage.
TRIP_SATZ = "kill_switch_trip"


def _trip_schluessel(user_id: str = None) -> str:
    return TRIP_SATZ if not user_id else f"{TRIP_SATZ}:{user_id}"


def halt_beim_start_raeumen(ks) -> bool:
    """Owner-Tabelle vom 17.09.2026 (#3449): darf die Strategie starten?

    | Halt       | Trip-Satz          | beim Start                              |
    |------------|--------------------|-----------------------------------------|
    | nicht      | egal               | Start                                   |
    | gesetzt    | lesbar             | stehen bleiben, ausdrueckliche Freigabe |
    | gesetzt    | fehlt / unlesbar   | **stehen bleiben**                      |

    Geraeumt wird hier nie mehr: Unbekannt ist nicht dasselbe wie unschuldig. Frueher raeumte
    ``base.py`` einen Halt ohne Trip im Arbeitsspeicher — ein Prozessattribut, das genau den
    Neustart nicht ueberlebt, gegen den es unterscheiden sollte.
    """
    if ks is None or not ks.is_halted():
        return True
    satz = ks.last_trip()
    if satz:
        logging.warning(
            "Kill Switch HALTED (%s, seit %s) — bleibt nach dem Neustart stehen; "
            "ausdrueckliche Freigabe (/reset-kill-switch) noetig (#3449).",
            satz.get("reason"),
            satz.get("at"),
        )
    else:
        logging.warning(
            "Kill Switch HALTED ohne lesbaren Trip-Satz — bleibt stehen (fail-closed, "
            "Owner-Entscheid 17.09.). Ausdrueckliche Freigabe (/reset-kill-switch) noetig "
            "(#3449)."
        )
    return False


def _audit(event: str, **fields):
    """Write ONE structured JSON line for a kill-switch event. FAIL-SAFE: this is
    pure observability and MUST NEVER raise into trip()/reset() — a broken audit sink
    can never be allowed to break the halt itself (the core safety invariant)."""
    try:
        record = {
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        _ks_audit_logger.info(json.dumps(record, default=str))
    except Exception:
        pass


class KillSwitch:
    """
    Global Circuit Breaker logic that uses Redis to synchronize state.
    Provides async mass-cancel and disconnect primitives.
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(KillSwitch, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.logger = logging.getLogger("kill_switch")
        self.redis_client = None

        self.alpaca_api_key = os.getenv("ALPACA_API_KEY", "").strip()
        self.alpaca_secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
        self.alpaca_base_url = os.getenv(
            "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
        ).strip()

        try:
            # #3449 Schritt 5: die dauerhafte synchrone Ablage dieser Installation —
            # Enterprise der synchrone Redis-Client (wie bisher), Desktop die SQLite-Datei des
            # StatePort unter AAA_USER_DATA_DIR. Ohne dauerhafte Ablage wie bisher.
            from core.state import synchron

            self.redis_client = (
                synchron.synchrone_ablage() or RedisClient.get_sync_redis()
            )
            self.redis_client.ping()
            self.logger.info("KillSwitch initialized with RedisClient")
        except Exception as e:
            self.logger.warning(
                f"Could not connect to Redis: {e}. Falling back to local state."
            )
            self.redis_client = None

        self._local_halted = False
        self._user_halted = {}
        # Observability: the most recent actual trip (reason/scope/at/user_id), or None
        # once reset. Surfaced via last_trip()/status() and the /health halt_reason.
        self._last_trip = None
        # #3449 Schritt 5: ein Halt aus einem frueheren Prozess bringt seinen Trip-Satz mit.
        try:
            if self.redis_client and self.redis_client.get("system_halted") == "true":
                self._last_trip = self._gespeicherter_trip()
        except Exception:  # noqa: BLE001 — den Halt liest is_halted
            self.logger.exception(
                "KillSwitch: Trip-Satz beim Start nicht lesbar (#3449)."
            )
        self._initialized = True

    def _gespeicherter_trip(self, user_id: str = None):
        """Der dauerhaft abgelegte Trip-Satz — oder ``None``, wenn er fehlt oder unlesbar ist.

        ``None`` heisst NICHT „harmlos": Ein gesetzter Halt ohne lesbaren Trip-Satz bleibt
        stehen (Owner-Entscheid 17.09., ``halt_beim_start_raeumen``).
        """
        if not self.redis_client:
            return None
        try:
            roh = self.redis_client.get(_trip_schluessel(user_id))
            satz = json.loads(roh) if roh else None
        except Exception:  # noqa: BLE001 — unlesbar heisst: kein Satz
            self.logger.exception(
                "KillSwitch: Trip-Satz nicht lesbar — ein gesetzter Halt bleibt stehen (#3449)."
            )
            return None
        return satz if isinstance(satz, dict) and satz.get("reason") else None

    def is_halted(self, user_id: str = None) -> bool:
        """Check if the system is halted globally or for a specific user."""
        if self._local_halted:
            return True

        if user_id and self._user_halted.get(user_id):
            return True

        if self.redis_client:
            try:
                state = self.redis_client.get("system_halted")
                if state == "true":
                    self._local_halted = True
                    return True

                if user_id:
                    user_state = self.redis_client.get(f"system_halted:{user_id}")
                    if user_state == "true":
                        self._user_halted[user_id] = True
                        return True
            except Exception as e:
                self.logger.error("Error reading from Redis: %s", e)

        return False

    def check_halt(self, user_id: str = None):
        """Raises Exception if halted - to be used before routing orders."""
        if self.is_halted(user_id):
            scope = "Globally" if not user_id else f"for User {user_id}"
            raise Exception(
                f"System is HALTED by Kill Switch ({scope}). Orders blocked."
            )

    def trip(
        self,
        reason: str,
        user_id: str = None,
        access_token: str = None,
        fail_closed: bool = True,
    ):
        """Trips the circuit breaker, halt the system globally/locally, and fire Mass-Cancel.

        #2467 FIX 4 (fail-closed): a genuine safety trip (``fail_closed=True`` — panic-sell, the
        watchdogs, risk-manager) ALSO drops the active account to PAPER and records a ``disable`` on
        the tamper-evident WORM chain, so a restart boots fail-closed to paper and the trip is
        auditable (not just the flat ``kill_switch_audit.log``). Global trips only (a per-user OAuth
        halt must not flip the global account). Best-effort — it NEVER blocks or fails the halt.
        ``/api/live/disable`` passes ``fail_closed=False`` because it force-papers + WORM-writes itself.
        """
        if self.is_halted(user_id):
            return

        scope = "GLOBALLY" if not user_id else f"for USER {user_id}"
        self.logger.error("🚨 KILL SWITCH TRIPPED %s: %s", scope, reason)

        # Observability (fail-safe): capture the trip + durably audit it. Wrapped so a
        # broken audit sink can NEVER stop the halt below from landing.
        try:
            self._last_trip = {
                "reason": reason,
                "scope": scope,
                "at": datetime.now(timezone.utc).isoformat(),
                "user_id": user_id,
            }
            _audit("trip", reason=reason, scope=scope, user_id=user_id)
        except Exception:
            pass

        if not user_id:
            self._local_halted = True
        else:
            self._user_halted[user_id] = True

        if self.redis_client:
            # #3449 Schritt 5: erst der Trip-Satz, dann der Halt — beides dauerhaft, BEVOR
            # trip() zurueckkehrt. Stirbt der Prozess dazwischen, steht ein Halt ohne Satz
            # (bleibt stehen) oder ein Satz ohne Halt (kein Halt) — nie ein stiller Verlust.
            try:
                self.redis_client.set(
                    _trip_schluessel(user_id), json.dumps(self._last_trip, default=str)
                )
            except Exception as e:
                self.logger.error("Failed to persist the trip record: %s", e)
            try:
                key = "system_halted" if not user_id else f"system_halted:{user_id}"
                self.redis_client.set(key, "true")
            except Exception as e:
                self.logger.error("Failed to set halted state in Redis: %s", e)

        # Send alert
        send_slack_alert(f"🚨 *KILL SWITCH TRIPPED*\nReason: {reason}\nScope: {scope}")

        # Trigger async mass cancel (fire and forget)
        threading.Thread(
            target=self._run_async_mass_cancel, args=(access_token,), daemon=True
        ).start()

        # #2467 FIX 4: fail-closed for a genuine safety trip — drop to paper + WORM disable (global only).
        if fail_closed and not user_id:
            self._apply_fail_closed(reason)

    def _apply_fail_closed(self, reason: str):
        """Best-effort fail-closed side-effects of a safety trip: force the active account to PAPER
        (a restart then boots fail-closed) and record a ``disable`` on the WORM chain. Both are
        wrapped so a broken config/audit sink can NEVER affect the already-landed halt above.
        """
        try:
            import config

            config.force_paper_trading(reason=f"kill-switch trip: {reason}")
        except Exception as e:  # noqa: BLE001 — best-effort; the halt already landed
            self.logger.error(
                "Kill switch: force_paper_trading failed (non-fatal): %s", e
            )
        # WORM disable in its own event loop (mirrors _run_async_mass_cancel), strict=False.
        threading.Thread(
            target=self._run_async_worm_disable, args=(reason,), daemon=True
        ).start()

    def _run_async_worm_disable(self, reason: str):
        """Run the async WORM ``disable`` write in a dedicated event loop (best-effort, strict=False)."""
        try:
            import uuid

            from core.hitl_gate import log_live_enablement_event

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                log_live_enablement_event(
                    action="disable",
                    acknowledgment=f"kill-switch trip (fail-closed): {reason}",
                    nonce=str(uuid.uuid4()),
                    actor="kill_switch",
                    strict=False,
                )
            )
            loop.close()
        except Exception as e:  # noqa: BLE001 — best-effort audit; never surfaces
            self.logger.error(
                "Kill switch: WORM disable record failed (non-fatal): %s", e
            )

    def reset(self, user_id: str = None):
        """Manually reset the circuit breaker."""
        if not user_id:
            self._local_halted = False
            self._user_halted.clear()
        else:
            self._user_halted[user_id] = False

        if self.redis_client:
            try:
                key = "system_halted" if not user_id else f"system_halted:{user_id}"
                self.redis_client.delete(key)
                self.redis_client.delete(_trip_schluessel(user_id))
            except Exception:  # noqa: BLE001 — der lokale Reset ist schon wirksam
                self.logger.exception(
                    "KillSwitch: Reset nicht dauerhaft geschrieben — nach einem Neustart "
                    "stuende der Halt wieder (#3449)."
                )
        scope = "System" if not user_id else f"User {user_id}"
        self.logger.info("Kill Switch has been RESET. %s is ACTIVE.", scope)

        # Observability (fail-safe): audit the reset + the trip it cleared, then drop
        # last_trip. Wrapped so a broken audit sink can NEVER stop the reset above.
        try:
            _audit("reset", user_id=user_id, cleared_trip=self._last_trip)
            self._last_trip = None
        except Exception:
            self._last_trip = None

        # Reset CycleWatchdog to prevent immediate re-trip
        try:
            from core.cycle_watchdog import cycle_watchdog

            cycle_watchdog.reset()
        except ImportError:
            pass  # cycle_watchdog optional

    def last_trip(self):
        """The most recent actual trip (dict) or None once reset. Read-only observability.

        #3449 Schritt 5: nach einem Neustart der dauerhaft abgelegte Trip-Satz.
        """
        if self._last_trip is None and self.redis_client:
            try:
                if self.redis_client.get("system_halted") == "true":
                    self._last_trip = self._gespeicherter_trip()
            except Exception:  # noqa: BLE001 — Beobachtung, nie Steuerung
                self.logger.exception("KillSwitch: Halt-Zustand nicht lesbar (#3449).")
        return self._last_trip

    def status(self, user_id: str = None):
        """Halt state + last trip, for the /health surface and diagnostics."""
        return {"halted": self.is_halted(user_id), "last_trip": self._last_trip}

    def _resolve_broker_target(self):
        """Resolve the Alpaca REST base URL + credentials at TRIP time.

        H1 (launch-gate DD): __init__ captures os.getenv("ALPACA_BASE_URL" / "ALPACA_API_KEY" /
        "ALPACA_SECRET_KEY") at IMPORT, which on a desktop LIVE boot are the PAPER defaults —
        native-engine-manager sets PAPER_TRADING but never ALPACA_BASE_URL, and config.oss.py
        writes the live URL/keys only into config MODULE GLOBALS, not os.environ. Reading the
        selected values from `config` at trip time makes the emergency mass-cancel hit the account
        that actually holds the live orders. Edition-neutral: config.ALPACA_* and get_secret_str
        exist in both config.py (Pydantic) and config.oss.py. Falls back to the import-time values
        (paper) if config cannot be read, so paper behaviour is unchanged.
        """
        base_url = self.alpaca_base_url
        api_key = self.alpaca_api_key
        secret_key = self.alpaca_secret_key
        try:
            import config

            raw_url = getattr(config, "ALPACA_BASE_URL", None)
            raw_key = getattr(config, "ALPACA_API_KEY", None)
            raw_sec = getattr(config, "ALPACA_SECRET_KEY", None)
            if raw_url:
                base_url = str(raw_url).strip()
            if raw_key:
                api_key = config.get_secret_str(raw_key).strip()
            if raw_sec:
                secret_key = config.get_secret_str(raw_sec).strip()
        except Exception as e:
            self.logger.warning(
                "KillSwitch: could not resolve live broker target from config (%s); "
                "falling back to import-time env (may be paper).",
                e,
            )
        return base_url, api_key, secret_key

    def _run_async_mass_cancel(self, access_token: str = None):
        """Runs the async cancel in a dedicated event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.async_mass_cancel(access_token))
        loop.close()

    async def async_mass_cancel(self, access_token: str = None):
        """
        Asynchronous fire-and-forget mass-cancel using httpx with a strict timeout.
        If access_token is provided, cancels orders for that specific OAuth user.
        Otherwise, uses the global operator accounts (Prop Trading mode).
        """
        # H1: resolve the broker target at TRIP time from the live config, NOT from the
        # paper-defaulted env captured at import (see _resolve_broker_target).
        base_url, api_key, secret_key = self._resolve_broker_target()

        if access_token:
            headers = {"Authorization": f"Bearer {access_token}"}
        else:
            if not api_key or not secret_key:
                self.logger.warning("Cannot run mass-cancel: Alpaca keys missing.")
                return
            headers = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": secret_key,
            }

        cancel_url = f"{base_url}/v2/orders"

        # Hard timeout of 30s for cancel request
        timeout = httpx.Timeout(30.0)

        try:
            self.logger.info("Initiating async mass-cancel...")
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.delete(cancel_url, headers=headers)

            if response.status_code in (200, 204, 207):
                self.logger.info(
                    f"✅ Mass-cancel triggered successfully. Response: {response.status_code}"
                )
                # Log to Cloud SQL audit trail could be added here
            else:
                self.logger.error(
                    f"❌ Mass-cancel failed. Status: {response.status_code}, Body: {response.text}"
                )
                send_slack_alert(
                    f"❌ Mass-cancel failed! Status: {response.status_code}"
                )
        except Exception as e:
            self.logger.error("❌ Mass-cancel Exception (Timeout/Network): %s", e)
            send_slack_alert(f"❌ Mass-cancel Exception (Timeout/Network): {e}")


class LokalerHalt:
    """#3485: ein Halt, der nur seinem Besitzer gilt — fuer die In-App-Simulation.

    Dieselbe Form wie ``KillSwitch`` (``trip``, ``reset``, ``is_halted``; ein Vertragstest haelt
    beide beisammen), aber nur Arbeitsspeicher: kein Massen-Storno, keine dauerhafte Ablage,
    kein Wechsel auf Paper, kein Eintrag im echten Audit-Pfad. Die Simulation haelt sich damit
    bei Verlust weiter selbst an — nie die Engine, und sie hebt nie deren Halt auf.
    """

    def __init__(self):
        self._global = False
        self._je_nutzer: dict = {}

    def trip(
        self,
        reason: str,
        user_id: str = None,
        access_token: str = None,
        fail_closed: bool = True,
    ):
        logging.getLogger(__name__).warning(
            "Lokaler Halt (Simulation) ausgeloest%s: %s",
            f" fuer {user_id}" if user_id else "",
            reason,
        )
        if user_id:
            self._je_nutzer[user_id] = True
        else:
            self._global = True

    def reset(self, user_id: str = None):
        if user_id:
            self._je_nutzer[user_id] = False
        else:
            self._global = False
            self._je_nutzer.clear()

    def is_halted(self, user_id: str = None) -> bool:
        return self._global or bool(user_id and self._je_nutzer.get(user_id))


# Global singleton instance
kill_switch = KillSwitch()
