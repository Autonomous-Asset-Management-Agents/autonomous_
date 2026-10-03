"""#3976 — Der Broker-Stop deckt die ganze Position, auch am Folgetag und nach Mengenaenderung.

Plan: docs/3976-broker-stop-aufstockung/implementation_plan.md (plan-approved, PR #3985).

Gemessen am 02.10.2026 (installierte App 0.5.2, Paper, Markt offen): 11 Positionen, 0 Stops beim
Broker. Zwei Ursachen im Code:
  1. Der Planer nahm ``qty_available`` — ohne die Stuecke des EIGENEN liegenden Stops. Am Folgetag
     galt bei einer Bruchstueck-Position nur das Bruchstueck als frei, der GTC-Stop wurde storniert.
  2. Der Ersatz trug den Schluessel des stornierten Stops; Alpaca lehnt ihn ab (422/40010001).
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.vc4  # Stufen-Marker (#3396): wie test_exit_authority_3632

TAG1 = date(2026, 9, 25)
TAG2 = date(2026, 9, 26)
TAG3 = date(2026, 9, 29)


def _cid(
    symbol: str, versuch: int = 0, tif: str = "gtc", tag: date | None = None
) -> str:
    rumpf = f"stop-{symbol}" if tif == "gtc" else f"stop-{symbol}-{tag.isoformat()}"
    return f"stop-{versuch}-{rumpf}"


def _stop(oid, symbol, qty, preis, tif="gtc", cid=None):
    return SimpleNamespace(
        id=oid,
        symbol=symbol,
        qty=qty,
        stop_price=preis,
        time_in_force=tif,
        client_order_id=cid if cid is not None else _cid(symbol, 0, tif, TAG1),
    )


def _pos(symbol, qty, frei, avg=100.0):
    return SimpleNamespace(
        symbol=symbol, qty=qty, qty_available=frei, avg_entry_price=avg
    )


def _plan(positions, stops, tag=TAG1, vortag=None, pct=1.5):
    from core.broker_stops import plan_broker_stops

    return plan_broker_stops(
        positions,
        stop_loss_pct=pct,
        existing_stops=stops,
        session_date=tag,
        existing_session_date=vortag,
    )


# --------------------------------------------------------------------------- Planer
class TestSchutzmenge:
    def test_fraction_position_keeps_its_gtc_stop_on_the_next_day(self):
        """MRK 140,149: GTC 140 liegt, der Tages-Stop von gestern ist abgelaufen."""
        gtc = _stop("g1", "MRK", 140.0, 142.61)
        pos = _pos("MRK", 140.149, 0.149, avg=144.78)
        plan = _plan([pos], [gtc], tag=TAG2, vortag=TAG1)
        assert plan.to_cancel == ()
        assert [(s.qty, s.time_in_force) for s in plan.to_place] == [(0.149, "day")]

    def test_topup_plans_one_stop_over_the_whole_position(self):
        alt = _stop("g1", "X", 27.0, 98.50)
        pos = _pos("X", 50.0, 23.0, avg=104.6)
        plan = _plan([pos], [alt])
        assert "g1" in plan.to_cancel
        assert [(s.qty, s.time_in_force) for s in plan.to_place] == [(50.0, "gtc")]

    def test_foreign_bound_shares_stay_excluded(self):
        """Eine fremde offene Verkaufs-Order (kein eigener Stop) bindet 2 von 5 Stueck."""
        fremd = _stop("f1", "AAPL", 2.0, 90.0, cid="manual-order-1")
        pos = _pos("AAPL", 5.0, 3.0)
        plan = _plan([pos], [fremd])
        assert sum(s.qty for s in plan.to_place) == pytest.approx(3.0)

    def test_steady_state_changes_nothing(self):
        gtc = _stop("g1", "MRK", 140.0, 142.61)
        day = _stop(
            "d1", "MRK", 0.149, 142.61, tif="day", cid=_cid("MRK", 0, "day", TAG1)
        )
        pos = _pos("MRK", 140.149, 0.0, avg=144.78)
        plan = _plan([pos], [gtc, day], tag=TAG1, vortag=TAG1)
        assert plan.to_place == () and plan.to_cancel == ()

    def test_own_stop_is_recognised_by_its_key(self):
        from core.broker_stops import ist_eigener_stop

        assert ist_eigener_stop(_stop("g", "MRK", 1, 1))
        assert ist_eigener_stop(_stop("g", "MRK", 1, 1, cid="stop-3-stop-MRK"))
        assert not ist_eigener_stop(_stop("g", "MRK", 1, 1, cid="manual-order-1"))
        assert not ist_eigener_stop(_stop("g", "MRK", 1, 1, cid=""))


# --------------------------------------------------------------------------- Broker-Modell
def _duplikat_fehler():
    from alpaca.common.exceptions import APIError

    antwort = SimpleNamespace(status_code=422)
    return APIError(
        json.dumps({"code": 40010001, "message": "client_order_id must be unique"}),
        SimpleNamespace(response=antwort, request=None),
    )


def _nicht_gefunden_fehler():
    from alpaca.common.exceptions import APIError

    antwort = SimpleNamespace(status_code=404)
    return APIError(
        json.dumps({"code": 40410000, "message": "order not found"}),
        SimpleNamespace(response=antwort, request=None),
    )


class FakeBroker:
    """Alpaca-treu genug fuer den Schutzpfad: qty_available, Tagesablauf, Duplikat-Schluessel."""

    def __init__(self, bestaende, storno_bestaetigt=True):
        self.bestaende = dict(bestaende)  # symbol -> (qty, avg)
        self.orders: dict = {}  # id -> order
        self.benutzt: dict = {}  # client_order_id -> id
        self.n = 0
        self.storno_bestaetigt = storno_bestaetigt
        self.gesendet: list = []
        self.abfragen = 0

    # lesend
    def offene(self):
        return [o for o in self.orders.values() if o.status == "new"]

    def get_orders(self, *a, **k):
        return self.offene()

    def get_all_positions(self):
        aus = []
        for s, (q, avg) in self.bestaende.items():
            gebunden = sum(o.qty for o in self.offene() if o.symbol == s)
            aus.append(_pos(s, q, max(0.0, round(q - gebunden, 9)), avg=avg))
        return aus

    def get_order_by_id(self, oid):
        return self.orders[oid]

    def get_order_by_client_id(self, cid):
        self.abfragen += 1
        if cid not in self.benutzt:
            raise _nicht_gefunden_fehler()
        return self.orders[self.benutzt[cid]]

    # schreibend
    def cancel_order_by_id(self, oid):
        if self.storno_bestaetigt:
            self.orders[oid].status = "canceled"
        else:
            self.orders[oid].status = "pending_cancel"

    def submit_order(self, req=None, **kw):
        cid = req.client_order_id
        if cid in self.benutzt:
            raise _duplikat_fehler()
        frei = next(
            p.qty_available for p in self.get_all_positions() if p.symbol == req.symbol
        )
        if float(req.qty) > frei + 1e-9:
            raise RuntimeError(f"insufficient qty available ({frei} < {req.qty})")
        self.n += 1
        oid = f"o{self.n}"
        tif = getattr(req.time_in_force, "value", req.time_in_force)
        self.orders[oid] = SimpleNamespace(
            id=oid,
            symbol=req.symbol,
            qty=float(req.qty),
            stop_price=float(req.stop_price),
            time_in_force=str(tif).lower(),
            client_order_id=cid,
            status="new",
        )
        self.benutzt[cid] = oid
        self.gesendet.append(cid)
        return self.orders[oid]

    def neuer_tag(self):
        for o in self.orders.values():
            if o.status == "new" and o.time_in_force == "day":
                o.status = "expired"

    def gedeckt(self, symbol):
        return sum(o.qty for o in self.offene() if o.symbol == symbol)


def _zyklus(broker, tag, vortag, pct=1.5, frist=0.2):
    """Ein Pflege-Durchlauf: Planer + Ausfuehrung, wie ``_maintain_broker_stops``."""
    from core.broker_stops import plan_broker_stops
    from core.engine.broker_stop_pflege import fuehre_plan_aus

    liegende = broker.get_orders()
    plan = plan_broker_stops(
        broker.get_all_positions(),
        stop_loss_pct=pct,
        existing_stops=liegende,
        session_date=tag,
        existing_session_date=vortag,
    )
    gescheitert = asyncio.run(
        fuehre_plan_aus(
            broker,
            plan,
            liegende,
            storno=broker.cancel_order_by_id,
            halted=False,
            frist_s=frist,
        )
    )
    return plan, gescheitert


class TestMehrtagesSimulation:
    @pytest.mark.mutates_global_state
    def test_fraction_position_stays_covered_over_three_days(self):
        broker = FakeBroker({"MRK": (140.149, 144.78)})
        vortag = None
        for tag in (TAG1, TAG2, TAG3):
            if vortag is not None:
                broker.neuer_tag()
            for _ in range(3):
                _zyklus(broker, tag, vortag)
                vortag = tag
                assert broker.gedeckt("MRK") == pytest.approx(140.149)

    def test_topup_is_fully_covered_after_one_cycle(self):
        broker = FakeBroker({"X": (27.0, 100.0)})
        _zyklus(broker, TAG1, None)
        assert broker.gedeckt("X") == pytest.approx(27.0)
        broker.bestaende["X"] = (50.0, 104.6)  # Nachkauf 23 Stueck
        _zyklus(broker, TAG1, TAG1)
        assert broker.gedeckt("X") == pytest.approx(50.0)
        plan, _ = _zyklus(broker, TAG1, TAG1)
        assert plan.to_place == () and plan.to_cancel == ()


# --------------------------------------------------------------------------- Schluessel
class TestDuplikatSchluessel:
    def test_used_key_of_a_cancelled_stop_gets_the_next_attempt(self):
        broker = FakeBroker({"MRK": (140.0, 144.78)})
        _zyklus(broker, TAG1, None)
        alt = broker.offene()[0]
        broker.cancel_order_by_id(alt.id)  # z. B. vor einem Verkauf storniert
        _plan_, gescheitert = _zyklus(broker, TAG1, TAG1)
        assert gescheitert == []
        assert broker.offene()[0].client_order_id == "stop-1-stop-MRK"
        assert broker.gedeckt("MRK") == pytest.approx(140.0)

    def test_genuine_repeat_places_nothing_more(self):
        from core.broker_stops import plan_broker_stops
        from core.engine.broker_stop_pflege import fuehre_plan_aus

        broker = FakeBroker({"MRK": (140.0, 144.78)})
        _zyklus(broker, TAG1, None)
        # derselbe Plan noch einmal, als waere die Antwort verloren gegangen
        plan = plan_broker_stops(
            [_pos("MRK", 140.0, 140.0, avg=144.78)],
            stop_loss_pct=1.5,
            existing_stops=[],
            session_date=TAG1,
        )
        gescheitert = asyncio.run(
            fuehre_plan_aus(
                broker, plan, [], storno=broker.cancel_order_by_id, halted=False
            )
        )
        assert gescheitert == []
        assert len(broker.offene()) == 1

    def test_many_used_keys_are_skipped_with_few_reads(self):
        """Ein fester Versuchsdeckel liefe bei oft ersetzten Stops voll — gesucht wird."""
        broker = FakeBroker({"MRK": (140.0, 144.78)})
        for v in range(30):  # 30 fruehere Ersatz-Faelle, alle storniert
            broker.benutzt[f"stop-{v}-stop-MRK"] = f"x{v}"
            broker.orders[f"x{v}"] = SimpleNamespace(
                id=f"x{v}", symbol="MRK", qty=1, status="canceled"
            )
        _plan_, gescheitert = _zyklus(broker, TAG1, None)
        assert gescheitert == []
        assert broker.offene()[0].client_order_id == "stop-30-stop-MRK"
        assert broker.abfragen <= 12

    def test_unreadable_key_status_reports_unprotected(self):
        broker = FakeBroker({"MRK": (140.0, 144.78)})
        broker.benutzt["stop-0-stop-MRK"] = "x0"
        broker.orders["x0"] = SimpleNamespace(
            id="x0", symbol="MRK", qty=1, status="canceled"
        )

        def _kaputt(cid):
            raise RuntimeError("broker down")

        broker.get_order_by_client_id = _kaputt
        _plan_, gescheitert = _zyklus(broker, TAG1, None)
        assert [s for s, _ in gescheitert] == ["MRK"]
        assert broker.gedeckt("MRK") == 0


# --------------------------------------------------------------------------- Storno-Frist
class TestStornoBestaetigung:
    def test_unconfirmed_cancel_defers_the_replacement(self):
        broker = FakeBroker({"X": (27.0, 100.0)})
        _zyklus(broker, TAG1, None)
        broker.bestaende["X"] = (50.0, 104.6)
        broker.storno_bestaetigt = False
        _plan_, gescheitert = _zyklus(broker, TAG1, TAG1, frist=0.1)
        assert broker.gesendet == ["stop-0-stop-X"]  # kein Ersatz gesendet
        assert [s for s, _ in gescheitert] == ["X"]
        assert "Storno" in gescheitert[0][1]

    def test_one_failing_status_query_does_not_block_the_others(self):
        """Review #3990 POLICY-01: ein Abfragefehler fuer EINE Order bricht das Warten auf
        die anderen nicht ab."""
        broker = FakeBroker({"X": (27.0, 100.0), "Y": (10.0, 50.0)})
        _zyklus(broker, TAG1, None)
        alt_x = next(o.id for o in broker.offene() if o.symbol == "X")
        broker.bestaende["X"] = (50.0, 104.6)
        broker.bestaende["Y"] = (20.0, 52.0)
        echt = broker.get_order_by_id

        def _wackelig(oid):
            if oid == alt_x:
                raise RuntimeError("timeout")
            return echt(oid)

        broker.get_order_by_id = _wackelig
        _plan_, gescheitert = _zyklus(broker, TAG1, TAG1, frist=0.3)
        assert broker.gedeckt("Y") == pytest.approx(20.0)
        assert [s for s, _ in gescheitert] == ["X"]

    def test_the_wait_is_bounded(self):
        import time

        broker = FakeBroker({"X": (27.0, 100.0)})
        _zyklus(broker, TAG1, None)
        broker.bestaende["X"] = (50.0, 104.6)
        broker.storno_bestaetigt = False
        start = time.perf_counter()
        _zyklus(broker, TAG1, TAG1, frist=0.3)
        assert time.perf_counter() - start < 1.5


# --------------------------------------------------------------------------- Sichtbarkeit
class TestSichtbarkeit:
    def test_status_names_unprotected_symbols_with_reason(self):
        from core.engine import broker_stop_pflege as pflege

        pflege.merke_stand(["MRK", "X"], [("X", "Storno nicht bestaetigt")], tag=TAG1)
        stand = pflege.letzter_stand()
        assert stand["positionen"] == 2
        assert stand["geschuetzt"] == 1
        assert stand["ungeschuetzt"] == [
            {"symbol": "X", "grund": "Storno nicht bestaetigt"}
        ]
        assert stand["tag"] == TAG1.isoformat()

    def test_engine_diagnostics_carries_the_block(self):
        import inspect

        from core.engine import api_routes

        assert '"broker_stops"' in inspect.getsource(api_routes.engine_diagnostics)
        assert api_routes._collect_broker_stops()["positionen"] >= 0
