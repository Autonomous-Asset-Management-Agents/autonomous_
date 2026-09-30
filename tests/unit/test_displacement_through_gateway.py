"""Der Verdraengungs-SELL geht durch das Tor (#3379 + Owner-Entscheid zu #3279).

Heute setzt `submit_deferred_displacement_sell` die Order ab und meldet sie ERST DANACH
dem Iron Dome (`register_displacement_leg`, laut eigenem Docstring „observe-only … never
blocks", fail-open bei jedem Guardian-Fehler). Der Wash-Trade-Guard — 60 Sekunden,
Gegenseite, exakt dafuer gebaut — sieht die Order damit erst, wenn sie beim Broker liegt.

**Owner-Entscheid vom 16.09.2026 zu #3279: Option B.** Die Pruefung laeuft VOR dem
Absenden, das Ergebnis wird mit Grund-Code protokolliert, die Order geht in jedem Fall
raus. Das Verhalten am Konto bleibt identisch — kein Live-Risiko im Sinne einer
geaenderten Order —, aber der Pfad schreibt ab jetzt einen Datensatz. Genau den verlangt
CH-2 aus #3366.

Haeufigkeit zur Einordnung: 3 Verdraengungen in 10 erfassten Tagen (06.08.–15.09.2026),
rund 1 % der ausgefuehrten SELLs. Tragend ist nicht das Volumen, sondern dass dieser Pfad
sonst der eine bleibt, fuer den die Vollstaendigkeit nicht messbar ist.
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _call(guardian=None, halted=False, submit_raises=None):
    from core.engine.order_executor import submit_deferred_displacement_sell

    client = MagicMock()
    if submit_raises:
        client.submit_order.side_effect = submit_raises
    else:
        client.submit_order.return_value = MagicMock(id="order-displ-1")
    pm = MagicMock()
    guardian = guardian or MagicMock()

    with patch("core.engine.order_executor.kill_switch") as ks:
        ks.is_halted.return_value = halted
        ok = submit_deferred_displacement_sell(
            client=client,
            pm=pm,
            guardian=guardian,
            user_id="user-1",
            symbol="AAPL",
            qty=3.0,
        )
    return ok, client, guardian, pm


def test_the_guardian_sees_the_order_before_it_is_submitted():
    """Die Kernforderung aus #3279: Pruefung VOR dem Absenden, nicht danach."""
    reihenfolge = []
    guardian = MagicMock()
    guardian.check_order.side_effect = (
        lambda *a, **kw: reihenfolge.append("check") or True
    )

    from core.engine.order_executor import submit_deferred_displacement_sell

    client = MagicMock()
    client.submit_order.side_effect = lambda *a, **kw: reihenfolge.append("submit")

    with patch("core.engine.order_executor.kill_switch") as ks:
        ks.is_halted.return_value = False
        submit_deferred_displacement_sell(
            client=client,
            pm=MagicMock(),
            guardian=guardian,
            user_id="user-1",
            symbol="AAPL",
            qty=3.0,
        )

    assert reihenfolge[:2] == ["check", "submit"], (
        f"Reihenfolge war {reihenfolge}. Die Pruefung muss vor dem Absenden stehen — "
        "sonst sieht das Wash-Fenster die Gegenseite erst nach der Ausfuehrung."
    )


def test_a_rejected_check_does_not_stop_the_sell():
    """Option B, nicht C: Der Verkauf geht auch bei Ablehnung raus.

    Ein geblockter Verdraengungs-SELL liesse das Konto mit einer Position ueber dem
    Buch-Deckel und einer bereits ausgefuehrten BUY-Seite zurueck — genau der Zustand,
    den #2712 vermeiden wollte.
    """
    guardian = MagicMock()
    guardian.check_order.return_value = False

    ok, client, _, _ = _call(guardian=guardian)

    assert ok is True
    client.submit_order.assert_called_once()


def test_the_rejection_is_recorded_not_swallowed():
    guardian = MagicMock()
    guardian.check_order.return_value = False

    with patch("core.engine.order_executor.logging") as log:
        _call(guardian=guardian)

    gemeldet = " ".join(str(c) for c in log.warning.call_args_list)
    assert (
        "AAPL" in gemeldet and "displacement" in gemeldet.lower()
    ), "Eine uebergangene Ablehnung, die niemand sieht, ist kein Datensatz."


def test_a_guardian_error_never_stops_the_sell():
    """Fail-open bleibt fail-open: Der Guardian darf den Tausch nicht stranden lassen."""
    guardian = MagicMock()
    guardian.check_order.side_effect = RuntimeError("guardian kaputt")

    ok, client, _, _ = _call(guardian=guardian)

    assert ok is True
    client.submit_order.assert_called_once()


def test_a_failed_submit_still_reports_false():
    """Gegenprobe: Das bisherige Verhalten bei Broker-Fehlern bleibt unveraendert."""
    ok, _, _, _ = _call(submit_raises=RuntimeError("broker down"))
    assert ok is False
