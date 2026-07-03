import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone

import httpx

from core.notifier import send_slack_alert
from core.redis_client import RedisClient

# Durable audit sink for kill-switch trip/reset — mirrors compliance.py's pattern so a
# trip/reset is auditable even when Redis/Slack are down. A separate JSONL file
# (kill_switch_audit.log) keeps the halt trail readable in isolation. Dir-artifact →
# StreamHandler fallback handles the Docker case where the path is a leftover directory.
_ks_audit_logger = logging.getLogger("KillSwitchAudit")
_ks_audit_logger.setLevel(logging.INFO)
_ks_audit_logger.propagate = False
_ks_audit_path = "kill_switch_audit.log"
if os.path.isdir(_ks_audit_path):
    _ks_audit_logger.addHandler(logging.StreamHandler())
else:
    try:
        _ks_file_handler = logging.FileHandler(_ks_audit_path)
        _ks_file_handler.setFormatter(logging.Formatter("%(message)s"))
        _ks_audit_logger.addHandler(_ks_file_handler)
    except Exception:
        _ks_audit_logger.addHandler(logging.StreamHandler())


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
            self.redis_client = RedisClient.get_sync_redis()
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
        self._initialized = True

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

    def trip(self, reason: str, user_id: str = None, access_token: str = None):
        """Trips the circuit breaker, halt the system globally/locally, and fire Mass-Cancel."""
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
            except Exception:
                pass
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
        """The most recent actual trip (dict) or None once reset. Read-only observability."""
        return self._last_trip

    def status(self, user_id: str = None):
        """Halt state + last trip, for the /health surface and diagnostics."""
        return {"halted": self.is_halted(user_id), "last_trip": self._last_trip}

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
        if access_token:
            headers = {"Authorization": f"Bearer {access_token}"}
        else:
            if not self.alpaca_api_key or not self.alpaca_secret_key:
                self.logger.warning("Cannot run mass-cancel: Alpaca keys missing.")
                return
            headers = {
                "APCA-API-KEY-ID": self.alpaca_api_key,
                "APCA-API-SECRET-KEY": self.alpaca_secret_key,
            }

        cancel_url = f"{self.alpaca_base_url}/v2/orders"

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


# Global singleton instance
kill_switch = KillSwitch()
