# core/engine/mandanten_zugang.py
# ARC-E6 H-1d (#4233) — Mandanten-Zugang, ausgezogen aus OrderExecutorMixin
# (core/engine/order_executor.py).
"""Broker-Client, Eigenkapital, Risk- und Portfolio-Manager je Mandant.

Hier wohnen die vier Methoden, die den Zugang eines Mandanten aufloesen:
``get_active_tenant_clients``, ``_get_tenant_risk_manager``,
``_get_tenant_portfolio_manager`` und ``_broker_zugang_fuer`` (#3391, Mandanten- und
Einzelkonto-Fall). Gerufen werden sie ueber ``self.`` aus dem Kern
(``_schritt_mandant_vorbereiten``, ``execute_approved_order``, ``_process_signal_event``)
und aus ``absendung_nachlauf.py``.

Die Ruempfe sind wortgleich mit dem Stand im Executor, mit derselben angemeldeten
Abbildung wie in G-1a und G-1b (Entscheidung #4183 §3, Weg b): Namen, die Tests am Modul
``order_executor`` neu binden, lesen die Ruempfe als ``order_executor.<name>`` — sonst
griffe der Patch ins Leere. Das sind ``config`` und ``logging``. Die funktionslokalen
Importe (darunter ``create_trading_client``) bleiben funktionslokal.

Die Importrichtung ist erzwungen: Dieses Modul importiert den Executor, nie umgekehrt.
Zusammengesetzt wird in ``AusfuehrungMixin`` (``ausfuehrung.py``).

Plan: ``docs/4233-h-1d-a-mandanten-zugang-a-mandanten-zugang-py/implementation_plan.md``.
"""

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from alpaca.common.exceptions import APIError

from core.protocols import BrokerClientProtocol

from . import order_executor


class MandantenZugangMixin:
    """Mandanten-Zugang; ueber ``AusfuehrungMixin`` in ``BotEngine`` zusammengesetzt."""

    async def get_active_tenant_clients(self) -> List[Dict[str, Any]]:
        """Fetches active wallets and creates TradingClients via OAuth."""
        try:
            from core.client_factory import create_trading_client
        except ImportError:
            from ai_trading_bot.core.client_factory import create_trading_client
        from core.secret_manager_utils import oauth_secrets
        from core.user_wallet_store import wallet_store

        active_wallets = await wallet_store.get_active_wallets()
        tenant_clients = []
        is_paper = getattr(order_executor.config, "PAPER_TRADING", True)

        for wallet in active_wallets:
            user_id = wallet["user_id"]

            # Check for local AAAgents keys first
            risk_limits = wallet.get("risk_limits", {})
            if "alpaca_keys" in risk_limits:
                keys = risk_limits["alpaca_keys"]
                try:
                    user_client = create_trading_client(
                        api_key=keys.get("api_key"),
                        secret_key=keys.get("secret_key"),
                        paper=is_paper,
                    )
                    acc = await asyncio.to_thread(user_client.get_account)
                    equity = float(acc.equity) if acc.equity else 0.0
                    tenant_clients.append(
                        {
                            "user_id": user_id,
                            "client": user_client,
                            "risk_limits": risk_limits,
                            "equity": equity,
                        }
                    )
                    continue
                except APIError as e:
                    order_executor.logging.error(
                        f"Alpaca API error for user {user_id} using local keys: {e}"
                    )
                    continue
                except Exception as e:
                    order_executor.logging.error(
                        "Failed to init TradingClient for user %s using local keys: %s",
                        user_id,
                        e,
                    )
                    continue

            # OAuth token logic
            secret_id = wallet.get("secret_manager_id")
            if not secret_id:
                continue

            tokens = oauth_secrets.get_tokens(secret_id)

            if not tokens or "access_token" not in tokens:
                order_executor.logging.warning(
                    "No valid tokens found for active user %s", user_id
                )
                continue

            try:
                user_client = create_trading_client(
                    oauth_token=tokens["access_token"], paper=is_paper
                )
                acc = await asyncio.to_thread(user_client.get_account)
                equity = float(acc.equity) if acc.equity else 0.0
                tenant_clients.append(
                    {
                        "user_id": user_id,
                        "client": user_client,
                        "risk_limits": risk_limits,
                        "equity": equity,
                    }
                )
            except APIError as e:
                order_executor.logging.error(
                    f"Alpaca API error for user {user_id}: {e}"
                )
            except Exception as e:
                order_executor.logging.error(
                    "Failed to init TradingClient for user %s: %s", user_id, e
                )

        return tenant_clients

    def _get_tenant_risk_manager(self, user_id: str, client: Any, equity: float):
        from core.composition.root import CompositionRoot
        from core.risk_manager import RiskManager

        if not hasattr(self, "tenant_risk_managers"):
            self.tenant_risk_managers = {}
        if user_id not in self.tenant_risk_managers:
            rm = RiskManager(
                client, equity, clock=CompositionRoot.get_instance().clock_port
            )
            rm.reset_daily_limit(equity)
            self.tenant_risk_managers[user_id] = rm
        else:
            self.tenant_risk_managers[user_id].client = client
        return self.tenant_risk_managers[user_id]

    def _get_tenant_portfolio_manager(
        self, user_id: str, client: BrokerClientProtocol, equity: float
    ):
        from core.portfolio_manager import PortfolioManager

        if not hasattr(self, "_pm_restored"):
            self._pm_restored: set = (
                set()
            )  # user_ids restored this session (NB-4: lives in executor, not PM)
        if not hasattr(self, "tenant_portfolio_managers"):
            self.tenant_portfolio_managers = {}
        if user_id not in self.tenant_portfolio_managers:
            max_positions = getattr(order_executor.config, "MAX_POSITIONS", 10)
            # Phase B (ADR-FU02): with the full universe the gremium judges ~500
            # names, so far more clear the bar than there is capital for. The slot
            # count IS the portfolio's scarcity — a better chance must DISPLACE the
            # weakest holding (debate_position_swap already arbitrates that) rather
            # than the book growing unbounded. Only the READ is overridden here;
            # config.MAX_POSITIONS itself stays 10, so the default path is unchanged.
            if order_executor.config.get_config().FULL_UNIVERSE_TRADING_ENABLED:
                # #4233: einzeilig, denn die Ratsche (regeln.RATSCHEN_MUSTER) zaehlt
                # je Zeile; umbrochen fiele diese Fundstelle aus der Zaehlung.
                # fmt: off
                max_positions = int(
                    getattr(order_executor.config.get_config(), "FULL_UNIVERSE_MAX_POSITIONS", 20)
                )
                # fmt: on
            pm = PortfolioManager(
                client,
                total_capital=equity,
                max_positions=max_positions,
                user_id=user_id,
            )
            self.tenant_portfolio_managers[user_id] = pm
        else:
            self.tenant_portfolio_managers[user_id].client = client
            self.tenant_portfolio_managers[user_id].update_total_capital(equity)
        return self.tenant_portfolio_managers[user_id]

    async def _broker_zugang_fuer(
        self, user_id: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Den Broker-Zugang fuer eine freigegebene Order aufloesen (#3391).

        Die eine Stelle, die zwei Quellen kennt — und sie **nicht** vermischt:

        1. **Mandanten-Aufstellung vorhanden** (Enterprise): Es gilt ausschliesslich
           der passende Mandant. Steht der Nutzer nicht darin, ist das eine echte
           Ablehnung („nicht dein Konto") und **kein** Einzelkonto-Fall.
        2. **Gar keine Mandanten** (Desktop, jede Einzelkonto-Aufstellung): Der
           Broker-Zugang der Engine gilt.
        3. **Weder noch**: eigener Ablehnungsgrund.

        Warum die Unterscheidung zwischen 1 und 2 traegt: Waere der Rueckfall auf den
        Engine-Zugang bedingungslos, wuerde eine Freigabe fuer einen **fremden**
        Mandanten auf dem Konto der Engine ausgefuehrt. Der Rueckfall greift darum nur,
        wenn es ueberhaupt keine Mandanten-Aufstellung gibt.

        Returns:
            ``(tenant, None)`` oder ``(None, grund)``.
        """
        active_tenants = await self.get_active_tenant_clients()
        if active_tenants:
            tenant = next(
                (t for t in active_tenants if t.get("user_id") == user_id), None
            )
            return (tenant, None) if tenant is not None else (None, "no_oauth_tenant")

        client = getattr(self, "api", None)
        if client is None:
            return None, "no_broker_access"

        from core.engine.equity_fallback import resolve_equity

        order_executor.logging.info(
            "[HITL] Einzelkonto-Aufstellung (keine OAuth-Mandanten) — die Freigabe "
            "laeuft ueber den Broker-Zugang der Engine (#3391)."
        )
        return (
            {
                "user_id": user_id,
                "client": client,
                # Der Hausweg fuer Eigenkapital: echter Kontostand, sonst
                # DEFAULT_EQUITY mit WARNING — nie eine stille Fantasiezahl
                # (equity_fallback.py, BUG-AI-S01).
                #
                # `to_thread`, weil `resolve_equity` intern `api.get_account()`
                # ruft — ein blockierender REST-Aufruf. Direkt im Ereignisring
                # steht waehrend dieses Netzabrufs die ganze Engine: Herzschlag,
                # Stall-Wache, jede andere Order. Genau so ist es im Hauspfad
                # schon geloest (`marktdaten.py:287`), und genau diesen
                # Unterschied hat der Review zu #3452 (POLICY-01) getroffen.
                "equity": await asyncio.to_thread(
                    resolve_equity,
                    client,
                    getattr(order_executor.config.get_config(), "DEFAULT_EQUITY", 0.0),
                ),
            },
            None,
        )
