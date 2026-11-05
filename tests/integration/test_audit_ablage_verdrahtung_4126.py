"""#4126 Teil 1b (Plan 9f, 9g Punkt 5): die Naht Root -> Tor -> Senke beim Engine-Start.

Nicht die Schichten einzeln, sondern der Weg, den der Desktop nimmt: ``api_routes`` startet
die Engine im Sim-Modus, dabei baut ``CompositionRoot`` die Ablage und meldet sie beim Tor an.
Eine Ablehnung am Tor steht danach in der Hash-Kette, und der Zaehler ``nicht_verdrahtet``
bleibt 0.
"""

import json
import threading
from unittest.mock import MagicMock

import pytest

from core.contracts import OrderIntent

pytestmark = [pytest.mark.integration, pytest.mark.vc3]


@pytest.fixture
def frische_root(tmp_path, monkeypatch):
    from core.composition.root import CompositionRoot
    from core.gateway import fabrik
    from core.ports import audit_ablage_port

    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    # Ein frueherer Test kann den Round-Table-Logger (anderes Verzeichnis) gesetzt haben;
    # die Senke nimmt ihn bevorzugt. Hier gilt: Engine-Start ohne Round Table.
    from core.round_table import runner

    monkeypatch.setattr(runner, "_senate", None)
    CompositionRoot.reset()
    audit_ablage_port.setze_ablage(None)
    fabrik.reset_ablage_zaehler()
    yield tmp_path
    CompositionRoot.reset()
    audit_ablage_port.setze_ablage(None)
    fabrik.reset_ablage_zaehler()


def _kettenzeilen(verzeichnis):
    return [
        json.loads(z)
        for d in sorted(verzeichnis.glob("audit_log_*.jsonl"))
        for z in d.read_text(encoding="utf-8").splitlines()
        if z
    ]


def test_engine_start_verdrahtet_die_ablage_und_die_ablehnung_steht_in_der_kette(
    frische_root, monkeypatch
):
    from core.engine import api_routes
    from core.gateway import fabrik
    from core.ports import audit_ablage_port

    monkeypatch.setattr(api_routes.config, "SIM_MODE", True, raising=False)
    monkeypatch.setattr(api_routes, "engine", None, raising=False)
    monkeypatch.setattr(
        api_routes, "create_trading_client", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(api_routes, "create_data_client", lambda *a, **k: MagicMock())
    monkeypatch.setattr(api_routes, "BotEngine", lambda **_kw: object())

    api_routes._init_trading_clients()

    from core.composition.root import CompositionRoot

    assert (
        audit_ablage_port.hole_ablage() is CompositionRoot.get_instance().audit_ablage
    )

    tor = fabrik.gateway_for(MagicMock(), is_halted=lambda user_id=None: True)
    entscheidung = tor.submit(
        OrderIntent(
            decision_id="dec-naht",
            symbol="AAPL",
            side="buy",
            qty=1.0,
            intent_kind="entry",
            halted=True,
        ),
        request={"symbol": "AAPL"},
    )
    assert entscheidung.approved is False

    zeilen = []
    for _ in range(100):
        zeilen = [
            z
            for z in _kettenzeilen(frische_root)
            if z.get("event_type") == "gateway_decision"
        ]
        if zeilen:
            break
        threading.Event().wait(0.05)

    assert zeilen, "gateway_decision fehlt in der Hash-Kette"
    daten = json.loads(zeilen[-1]["daten"])
    assert daten["decision_id"] == "dec-naht"
    assert daten["reason_code"] == "trading_halted"
    assert daten["symbol"] == "AAPL"
    assert fabrik.get_ablage_zaehler()["nicht_verdrahtet"] == 0
