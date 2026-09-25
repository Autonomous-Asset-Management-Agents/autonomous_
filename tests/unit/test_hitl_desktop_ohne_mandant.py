"""#3391 (ARC-E2.8) — eine Freigabe wirkt auch ohne OAuth-Mandanten.

**Der Befund, der dieses Sub-Issue traegt:** Auf dem Desktop laeuft **jede**
HITL-Freigabe ins Leere. Der Mensch gibt frei, der Pfad findet keinen Mandanten und
verbucht `rejected / no_oauth_tenant` — und es passiert nichts. Die Aufsicht nach
EU AI Act Art. 14 ist dort damit **protokolliert, aber nicht wirksam**.

Belegt (gegen den Bestand geprueft, die Anker des Plans hatten sich verschoben):

* `order_executor.py:2423` holt `get_active_tenant_clients()`, `:2424` sucht den
  passenden `user_id`, `:2425-2434` bricht ab mit
  `_audit("rejected", reason="no_oauth_tenant")`.
* `get_active_tenant_clients` bezieht seine Eintraege aus dem Wallet-Store — ohne
  Mandanten-Aufstellung ist die Liste **leer**, und das ist der Desktop-Regelfall.
* Der Standardmodus verschaerft das: `HITL_MAX_VALUE_PER_TRADE` und
  `..._PER_DAY` haben den Vorgabewert `0.0` (`config.py`), also wandert **jede**
  Order in die Warteschlange.

Die Freigabepruefung selbst bleibt unangetastet streng. Entkoppelt wird nur die
**Aufloesung des Broker-Zugangs**.

Wichtig fuer die Unterscheidung — drei Faelle, nicht zwei:

1. **Mandanten vorhanden, passender dabei** → wie heute (Enterprise, unveraendert).
2. **Gar keine Mandanten** → Einzelkonto-Aufstellung, der Zugang der Engine gilt.
3. **Mandanten vorhanden, aber keiner passt** → echte Ablehnung. Das ist *nicht*
   der Desktop-Fall, sondern „nicht dein Konto", und muss abgelehnt bleiben.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))


def _run(coro):
    return asyncio.run(coro)


def _make_executor():
    from core.engine.order_executor import OrderExecutorMixin

    executor = OrderExecutorMixin.__new__(OrderExecutorMixin)
    executor.api = MagicMock()
    executor.compliance_guardian = None
    executor.live_universe = []
    return executor


def _payload(**kw):
    p = {
        "approval_id": "appr-1",
        "user_id": "u1",
        "symbol": "AAPL",
        "action": "BUY",
        "qty": 3.0,
        "price": 100.0,
        "conviction": 0.7,
        "target_weight": 0.05,
    }
    p.update(kw)
    return p


def _audit_patches():
    audit = AsyncMock()
    return (
        audit,
        patch("core.hitl_gate.log_execution_event", audit),
        patch("core.hitl_gate.policy_snapshot", return_value={}),
        patch("core.hitl_gate.policy_hash", return_value="pol-hash"),
    )


def _letzter_grund(audit):
    """Der Grund des letzten Pruefeintrags — oder None, wenn keiner gesetzt war."""
    return getattr(audit.await_args.args[0], "reason", None)


def _letzter_zweig(audit):
    return audit.await_args.args[0].branch


# ---------------------------------------------------------------------------
# Der fuehrende rote Fall
# ---------------------------------------------------------------------------


def test_freigabe_wirkt_ohne_mandanten() -> None:
    """Szenario: Freigabe wirkt ohne Mandanten.

    Heute rot: Die Liste ist leer, der Pfad lehnt mit `no_oauth_tenant` ab, und die
    Order erreicht den Broker nie — obwohl ein Mensch sie ausdruecklich freigegeben
    hat.
    """
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(return_value=[])  # Desktop
    executor._execute_tenant_order = AsyncMock(return_value=True)

    audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        ergebnis = _run(executor.execute_approved_order(_payload()))

    assert ergebnis is True, (
        "Die freigegebene Order wurde nicht ausgefuehrt. Auf dem Desktop gibt es keine "
        "OAuth-Mandanten — die Freigabe laeuft damit ins Leere, und die menschliche "
        "Aufsicht ist nur protokolliert, nicht wirksam (#3391)."
    )
    executor._execute_tenant_order.assert_awaited_once()
    assert _letzter_grund(audit) != "no_oauth_tenant"


def test_der_zugang_der_engine_wird_benutzt(monkeypatch) -> None:
    """Ohne Mandanten gilt der Broker-Zugang der Engine selbst.

    Das ist die Einzelkonto-Aufstellung: ein Konto, ein Zugang, kein Mandantenbegriff.
    """
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(return_value=[])
    executor._execute_tenant_order = AsyncMock(return_value=True)

    _audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        _run(executor.execute_approved_order(_payload()))

    args, _kwargs = executor._execute_tenant_order.await_args
    tenant = args[0]
    assert (
        tenant["client"] is executor.api
    ), "Der Einzelkonto-Zugang der Engine wurde nicht benutzt."
    assert tenant["user_id"] == "u1"


def test_der_kontoabruf_haelt_den_ereignisring_nicht_an() -> None:
    """Szenario: Der Kontostand wird geholt, ohne die Engine anzuhalten.

    **Nachgetragen nach dem Review zu diesem PR (POLICY-01).** Meine erste Fassung
    rief ``resolve_equity`` direkt in einer ``async def``. Das ist kein Schoenheits-
    fehler: ``resolve_equity`` ruft intern ``api.get_account()``, einen blockierenden
    REST-Aufruf. Waehrend er laeuft, steht der ganze Ereignisring — Herzschlag,
    Stall-Wache, jede andere Order. Der Hauspfad loest das seit #3241 richtig
    (``trading_loop.py:1068``), meine neue Stelle nicht.

    Der Test misst die Zusage, statt sie zu behaupten: Waehrend die Aufloesung laeuft,
    muss ein zweiter Task weiterticken. Ein Aufrufzaehler auf ``to_thread`` waere
    schwaecher — er wuerde gruen, sobald der Name faellt, und nichts darueber sagen,
    ob der Ring frei blieb.
    """
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(return_value=[])

    ABRUF_S = 0.30
    TAKT_S = 0.01

    def _langsamer_kontoabruf(*_a, **_kw):
        # Bewusst `time.sleep` und nicht `asyncio.sleep`: Nachgebildet wird ein
        # blockierender Netzaufruf, nicht ein kooperativer.
        import time as _t

        _t.sleep(ABRUF_S)
        return 12345.0

    async def _szenario():
        ticks = 0

        async def _ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(TAKT_S)
                ticks += 1

        laeuft = asyncio.ensure_future(_ticker())
        try:
            with patch(
                "core.engine.equity_fallback.resolve_equity", _langsamer_kontoabruf
            ):
                tenant, grund = await executor._broker_zugang_fuer("u1")
        finally:
            laeuft.cancel()
        return tenant, grund, ticks

    tenant, grund, ticks = _run(_szenario())

    assert (
        grund is None and tenant is not None
    ), "Die Aufloesung selbst ist gescheitert."
    assert tenant["equity"] == 12345.0, "Der Kontostand kam nicht durch."
    # Erwartbar waeren rund 30 Ticks. Die Haelfte als Schwelle laesst einem
    # ausgelasteten Runner Luft und trennt trotzdem sauber vom blockierten Fall,
    # der bei 0 bis 1 Ticks liegt.
    mindestens = int(ABRUF_S / TAKT_S) // 2
    assert ticks >= mindestens, (
        f"Waehrend des Kontoabrufs liefen nur {ticks} Ticks (erwartet >= {mindestens}). "
        "Der blockierende Aufruf laeuft im Ereignisring — genau der Befund POLICY-01."
    )


def test_doppelte_freigabe_erzeugt_keine_zweite_order() -> None:
    """Szenario: Doppelte Freigabe erzeugt keine zweite Order.

    **Nachgetragen nach dem Gate-Befund zu diesem PR.** Ich hatte das Kriterium in den
    „Grenzen der Aussage" auf #3449 verschoben mit der Begruendung, die Wiederaufnahme
    brauche die Outbox. Das war zu schnell: Die Outbox braucht es fuer die
    Wiederaufnahme **nach einem Absturz** — ein Doppel-Drain im selben Prozess ist
    davon unabhaengig und heute pruefbar.

    Die Zusage traegt der deterministische Schluessel (``order_executor.py:2505-2512``):
    Beide Durchlaeufe stempeln denselben ``client_order_id``, und der Broker lehnt das
    Duplikat ab. Genau diese Annahme begruendet auch ``core/idempotency.py``.

    Geprueft wird hier **unsere** Seite: dass derselbe Schluessel entsteht. Dass der
    Broker ihn dann abweist, ist die Annahme des Bestands und in dieser Sitzung nicht
    gegen die echte Alpaca-API nachgeprueft.
    """
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(return_value=[])  # Desktop
    executor._execute_tenant_order = AsyncMock(return_value=True)

    _audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        _run(executor.execute_approved_order(_payload(approval_id="appr-DOPPELT")))
        _run(executor.execute_approved_order(_payload(approval_id="appr-DOPPELT")))

    assert executor._execute_tenant_order.await_count == 2
    schluessel = [
        aufruf.args[1].decision_context.client_order_id
        for aufruf in executor._execute_tenant_order.await_args_list
    ]
    assert schluessel[0] == schluessel[1], (
        f"Zwei Abarbeitungen derselben Freigabe erzeugten verschiedene Schluessel: "
        f"{schluessel}. Der Broker koennte das Duplikat dann nicht erkennen, und eine "
        "einzelne menschliche Freigabe wuerde zweimal ausgefuehrt."
    )
    assert schluessel[0].startswith("hitl-appr-DOPPELT"), (
        f"Der Schluessel {schluessel[0]!r} ist nicht aus der Freigabe abgeleitet — dann "
        "ist er nicht reproduzierbar und die Idempotenz haengt am Zufall."
    )


# ---------------------------------------------------------------------------
# Die Faelle, die NICHT aufgeweicht werden duerfen
# ---------------------------------------------------------------------------


def test_enterprise_bleibt_unveraendert() -> None:
    """Szenario: Enterprise bleibt unveraendert.

    Mit Mandanten wird weiterhin der passende genommen — kein Rueckfall auf den
    Engine-Zugang, kein Ausfaechern auf alle.
    """
    executor = _make_executor()
    passend = {"user_id": "u1", "client": MagicMock(), "equity": 1000.0}
    fremd = {"user_id": "u2", "client": MagicMock(), "equity": 500.0}
    executor.get_active_tenant_clients = AsyncMock(return_value=[fremd, passend])
    executor._execute_tenant_order = AsyncMock(return_value=True)

    _audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        _run(executor.execute_approved_order(_payload()))

    args, _kwargs = executor._execute_tenant_order.await_args
    assert (
        args[0]["client"] is passend["client"]
    ), "Mit Mandanten muss der passende genommen werden, nicht der Engine-Zugang."


def test_mandanten_vorhanden_aber_keiner_passt_bleibt_abgelehnt() -> None:
    """Das ist NICHT der Desktop-Fall, sondern „nicht dein Konto".

    Der wichtigste Gegentest dieses PRs: Waere der Rueckfall auf den Engine-Zugang
    bedingungslos, wuerde eine Freigabe fuer einen **fremden** Mandanten auf dem
    Konto der Engine ausgefuehrt. Der Rueckfall gilt nur, wenn es gar keine
    Mandanten-Aufstellung gibt.
    """
    executor = _make_executor()
    executor.get_active_tenant_clients = AsyncMock(
        return_value=[{"user_id": "u2", "client": MagicMock(), "equity": 500.0}]
    )
    executor._execute_tenant_order = AsyncMock(return_value=True)

    audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        ergebnis = _run(executor.execute_approved_order(_payload()))

    assert ergebnis is False, (
        "Eine Freigabe fuer einen fremden Mandanten wurde ausgefuehrt. Der Rueckfall "
        "auf den Engine-Zugang darf NUR greifen, wenn es gar keine Mandanten gibt."
    )
    executor._execute_tenant_order.assert_not_awaited()
    assert _letzter_zweig(audit) == "rejected"


def test_ohne_jeden_broker_zugang_eigener_grund() -> None:
    """Szenario: Ohne Broker-Zugang wird sauber abgelehnt.

    Der Grund darf **nicht** `no_oauth_tenant` lauten: Das waere dieselbe Meldung fuer
    zwei verschiedene Sachverhalte, und wer sie im Pruefprotokoll liest, koennte den
    Desktop-Fall nicht vom fehlenden Zugang unterscheiden.
    """
    executor = _make_executor()
    executor.api = None  # kein Zugang hinterlegt
    executor.get_active_tenant_clients = AsyncMock(return_value=[])
    executor._execute_tenant_order = AsyncMock(return_value=True)

    audit, p_audit, p_snap, p_hash = _audit_patches()
    with p_audit, p_snap, p_hash:
        ergebnis = _run(executor.execute_approved_order(_payload()))

    assert ergebnis is False
    executor._execute_tenant_order.assert_not_awaited()
    grund = _letzter_grund(audit)
    assert grund and grund != "no_oauth_tenant", (
        f"Der fehlende Broker-Zugang wurde als {grund!r} verbucht. Zwei verschiedene "
        "Sachverhalte unter einem Grund sind im Pruefprotokoll nicht auseinanderzuhalten."
    )
