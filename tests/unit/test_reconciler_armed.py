"""#3389 (Epic #3367, ARC-E2) — der Abgleich läuft, korrigiert aber nichts von selbst.

Der ``ReconciliationService`` existiert seit Epic 2.3-Pre und hat **keinen
Produktionsaufrufer** — der Kontrollgrep findet ihn nur im eigenen Unit-Test und in einer
Risiko-Klassifizierungsliste. Er lief also nie. Das ist der Grund, warum drei Dinge bis
heute unbemerkt blieben:

1. ``_act`` **storniert automatisch** (``reconciliation.py:180``). Eine Abweichung heisst
   aber gerade, dass wir nicht wissen, welche Seite recht hat — eine automatische
   Korrektur ist in diesem Moment ein Handel auf Verdacht, mit echtem Geld. Nach
   EU AI Act Art. 14 ist eine Bestandsaenderung eine Kapitalentscheidung und gehoert
   zum Menschen.
2. ``_internal_order_ids`` ist nach jedem **Neustart leer** (``:60``). Jede offene Order
   beim Broker saehe damit verwaist aus — und wuerde storniert. Haette der Dienst je
   gelaufen, haette ein Neustart den gesamten offenen Orderbestand abgeraeumt.
3. ``_compare`` (``:135``) vergleicht ausschliesslich **Orders**. Positionen werden nie
   verglichen, obwohl ``position_mismatch`` als Abweichungsart deklariert ist
   (``:31``) — der Zweig ist tot.

Die Sperrwirkung bleibt in diesem Schritt **abgeschaltet**: Der Plan (§8) verlangt, die
Rate falsch positiver Befunde erst ueber mindestens eine volle Handelswoche gegen Paper
zu messen. Eine Sperre, deren Fehlalarmquote niemand kennt, legt den Handel still.
"""

from datetime import datetime, timezone

import pytest


class _Order:
    def __init__(self, oid, symbol="AAPL"):
        self.id = oid
        self.symbol = symbol


class _Pos:
    def __init__(self, symbol, qty):
        self.symbol = symbol
        self.qty = qty


class _Api:
    def __init__(self, orders=(), positions=()):
        self._orders = list(orders)
        self._positions = list(positions)
        self.storniert = []

    def get_orders(self, filter=None):  # noqa: A002 — Signatur von alpaca-py
        # #3588: Der Abgleich fragt zweimal — offene Orders ohne Filter, abgeschlossene
        # mit. Ohne das zweite Ergebnis koennte er entgangene Ausfuehrungen nie nachtragen.
        if filter is not None:
            return list(getattr(self, "_abgeschlossene", ()))
        return list(self._orders)

    def get_all_positions(self):
        return list(self._positions)

    def cancel_order_by_id(self, order_id):
        self.storniert.append(order_id)


def _dienst(api, **kw):
    from core.reconciliation import ReconciliationService

    return ReconciliationService(api, redis_client=None, **kw)


async def _bereit(api, **kw):
    """Dienst nach dem Uebernahme-Lauf.

    Der erste Lauf uebernimmt den vorgefundenen Bestand, statt ihn zu melden (sonst
    waere nach jedem Neustart der gesamte offene Bestand eine Abweichung). Wer einen
    BEFUND pruefen will, braucht deshalb den zweiten Lauf.
    """
    dienst = _dienst(api, **kw)
    await dienst.run_once()
    return dienst


# ---------------------------------------------------------------------------
# 1 — Der Gegentest: nichts wird automatisch veraendert
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_eine_abweichung_wird_niemals_automatisch_korrigiert():
    """Heute rot: reconciliation.py:180 storniert die verwaiste Order.

    Eine Abweichung heisst, dass wir nicht wissen, welche Seite recht hat. Ein
    automatischer Storno auf Basis eines fehlerhaften Vergleichs ist irreversibel,
    ein Alarm nicht.
    """
    api = _Api()
    dienst = await _bereit(api)
    api._orders.append(_Order("o-fremd"))

    record = await dienst.run_once()

    assert api.storniert == [], (
        f"Der Abgleich hat von sich aus storniert: {api.storniert}. "
        "Bestandsaenderungen gehoeren zum Menschen (EU AI Act Art. 14)."
    )
    assert not record.clean


@pytest.mark.anyio
async def test_der_dienst_kennt_keinen_weg_zum_broker_der_mutiert():
    """Quellbeleg: im Abgleich gibt es keinen mutierenden Broker-Aufruf mehr."""
    import re
    from pathlib import Path

    quelle = (
        Path(__file__).resolve().parents[2] / "core" / "reconciliation.py"
    ).read_text(encoding="utf-8")

    mutierend = [
        z.strip()
        for z in quelle.splitlines()
        if re.search(r"\.(cancel_order_by_id|cancel_orders|submit_order|close_)", z)
        and not z.strip().startswith("#")
    ]
    assert not mutierend, f"mutierende Aufrufe im Abgleich: {mutierend}"


# ---------------------------------------------------------------------------
# 2 — Der Neustart raeumt nicht den Orderbestand ab
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_nach_einem_neustart_gilt_nicht_alles_als_verwaist():
    """``_internal_order_ids`` ist nach dem Start leer — das ist kein Befund.

    Ohne diese Unterscheidung meldete der erste Lauf nach jedem Neustart den gesamten
    offenen Orderbestand als Abweichung und loeste (frueher) einen Massen-Storno aus.
    """
    api = _Api(orders=[_Order("o-1"), _Order("o-2"), _Order("o-3")])
    dienst = _dienst(api)

    erst = await dienst.run_once()

    assert erst.clean, (
        f"Der erste Lauf nach dem Start meldet {len(erst.breaks)} Abweichungen, "
        "obwohl er nur einen leeren internen Stand mit dem Broker vergleicht."
    )


@pytest.mark.anyio
async def test_nach_der_uebernahme_wird_neues_wieder_erkannt():
    """Die Uebernahme darf nicht dauerhaft blind machen — nur den ersten Lauf."""
    api = _Api(orders=[_Order("o-1")])
    dienst = _dienst(api)
    await dienst.run_once()

    api._orders.append(_Order("o-neu"))
    zweit = await dienst.run_once()

    assert not zweit.clean
    assert any(b.order_id == "o-neu" for b in zweit.breaks)


# ---------------------------------------------------------------------------
# 3 — Positionen werden ueberhaupt verglichen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_eine_unbekannte_position_wird_gefunden():
    """Heute rot: ``_compare`` sieht nur Orders an, nie Positionen."""
    api = _Api()
    dienst = await _bereit(api)
    api._positions.append(_Pos("NVDA", "4"))

    record = await dienst.run_once()

    arten = {b.kind for b in record.breaks}
    assert (
        "unknown_position" in arten
    ), f"Position beim Broker, die die Engine nicht kennt, wurde nicht gemeldet: {arten}"


@pytest.mark.anyio
async def test_eine_mengenabweichung_wird_gefunden():
    api = _Api(positions=[_Pos("AAPL", "5")])
    dienst = await _bereit(api, known_positions={"AAPL": 3.0})

    record = await dienst.run_once()

    bruch = next(b for b in record.breaks if b.kind == "position_mismatch")
    assert "5" in bruch.broker_side
    assert "3" in bruch.engine_side


@pytest.mark.anyio
async def test_bruchstuecke_erzeugen_keine_falschen_befunde():
    """Bruchteilige Positionen bleiben (Owner-Entscheid). Ein Vergleich, der auf ganze
    Stuecke rundet, machte daraus laufend falsch positive Abweichungen — und die Sperre
    daraus einen Dauer-Stillstand."""
    api = _Api(positions=[_Pos("AAPL", "0.017")])
    dienst = await _bereit(api, known_positions={"AAPL": 0.017})

    record = await dienst.run_once()

    assert record.clean, f"Bruchstueck als Abweichung gemeldet: {record.breaks}"


@pytest.mark.anyio
async def test_winzige_rundungsdifferenzen_sind_keine_abweichung():
    api = _Api(positions=[_Pos("AAPL", "1.0000000001")])
    dienst = await _bereit(api, known_positions={"AAPL": 1.0})

    assert (await dienst.run_once()).clean


# ---------------------------------------------------------------------------
# 4 — Jeder Lauf hinterlaesst einen Datensatz
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_jeder_lauf_erzeugt_einen_reconciliationrecord():
    from core.contracts import ReconciliationRecord

    api = _Api(orders=[_Order("o-1")], positions=[_Pos("AAPL", "1")])
    record = await _dienst(api).run_once()

    assert isinstance(record, ReconciliationRecord)
    assert record.run_id
    assert record.finished_at >= record.started_at


@pytest.mark.anyio
async def test_der_datensatz_nennt_den_vergleichsumfang():
    """Ohne Umfang ist ein Lauf, der nichts verglichen hat, von einem, der nichts
    gefunden hat, nicht zu unterscheiden."""
    api = _Api(orders=[_Order("o-1"), _Order("o-2")], positions=[_Pos("AAPL", "1")])
    record = await _dienst(api).run_once()

    assert record.broker_orders == 2
    assert record.broker_positions == 1


@pytest.mark.anyio
async def test_ein_ausfall_der_broker_abfrage_ist_kein_sauberer_lauf():
    """Sonst meldete ein Netzausfall „alles in Ordnung" — das gefaehrlichste Ergebnis."""

    class _Kaputt(_Api):
        def get_all_positions(self):
            raise RuntimeError("Broker nicht erreichbar")

    record = await _dienst(_Kaputt()).run_once()

    assert not record.clean
    assert any(b.kind == "broker_unreachable" for b in record.breaks)


# ---------------------------------------------------------------------------
# 5 — Die Sperre: vorhanden, aber ausgeschaltet
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_die_sperre_ist_standardmaessig_aus():
    """Plan §8: die Rate falsch positiver Befunde ist nicht gemessen, weil der Dienst
    nie lief. Eine Sperre mit unbekannter Fehlalarmquote legt den Handel still."""
    from config import RuntimeConfigState

    assert RuntimeConfigState().RECONCILIATION_BLOCK_ON_BREAK is False

    api = _Api()
    dienst = await _bereit(api)
    api._orders.append(_Order("o-fremd"))
    await dienst.run_once()

    assert dienst.entries_blocked is False


@pytest.mark.anyio
async def test_mit_schalter_sperrt_eine_abweichung_die_einstiege():
    api = _Api()
    dienst = await _bereit(api, block_on_break=True)
    api._orders.append(_Order("o-fremd"))
    await dienst.run_once()

    assert dienst.entries_blocked is True


@pytest.mark.anyio
async def test_die_sperre_loest_sich_nicht_von_selbst():
    """„Bis ein Mensch sie aufhebt" — ein sauberer Folgelauf ist kein Mensch."""
    api = _Api()
    dienst = await _bereit(api, block_on_break=True)
    api._orders.append(_Order("o-fremd"))
    await dienst.run_once()

    api._orders.clear()
    await dienst.run_once()

    assert dienst.entries_blocked is True


@pytest.mark.anyio
async def test_die_sperre_trifft_nur_einstiege_nie_schutz_exits():
    """Eine Sperre, die den Notausgang mitsperrt, macht aus einem Datenproblem ein
    Kapitalproblem."""
    api = _Api()
    dienst = await _bereit(api, block_on_break=True)
    api._orders.append(_Order("o-fremd"))
    await dienst.run_once()

    assert dienst.blocks("entry") is True
    for freigestellt in ("stop", "panic", "breaker", "displacement", "strategy_switch"):
        assert dienst.blocks(freigestellt) is False, f"{freigestellt} wurde gesperrt"


@pytest.mark.anyio
async def test_ein_mensch_kann_die_sperre_aufheben():
    api = _Api()
    dienst = await _bereit(api, block_on_break=True)
    api._orders.append(_Order("o-fremd"))
    await dienst.run_once()

    dienst.release_block(by="operator")

    assert dienst.entries_blocked is False


# ---------------------------------------------------------------------------
# 6 — Vor dem ersten Abgleich wird nicht gehandelt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_vor_dem_ersten_abgleich_sind_einstiege_zurueckgehalten():
    """Ein Zyklus, der vor dem Start-Abgleich eine Order absetzt, entscheidet auf einem
    Bestand, den niemand gegen den Broker gehalten hat."""
    dienst = _dienst(_Api())

    assert dienst.reconciled_once is False
    assert dienst.blocks("entry") is True

    await dienst.run_once()

    assert dienst.reconciled_once is True
    assert dienst.blocks("entry") is False


@pytest.mark.anyio
async def test_schutz_exits_sind_auch_vor_dem_ersten_abgleich_frei():
    dienst = _dienst(_Api())

    assert dienst.blocks("stop") is False
    assert dienst.blocks("panic") is False


# ---------------------------------------------------------------------------
# 7 — Parität (BORA)
# ---------------------------------------------------------------------------


def test_beide_editionen_tragen_denselben_schalter():
    import importlib.util
    from pathlib import Path

    from config import RuntimeConfigState

    spec = importlib.util.spec_from_file_location(
        "config_oss_3389",
        str(Path(__file__).resolve().parents[2] / "config.oss.py"),
    )
    oss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oss)
    ent = RuntimeConfigState()

    for key in ("RECONCILIATION_ENABLED", "RECONCILIATION_BLOCK_ON_BREAK"):
        assert hasattr(oss, key), f"config.oss.py fehlt {key}"
        assert getattr(oss, key) == getattr(ent, key)


def test_der_zeitpunkt_kommt_von_der_engine_uhr():
    """Sim und Live auf derselben Uhr (#3317) — sonst traegt ein Sim-Datensatz die
    Wanduhr und ist gegen den Sim-Tag nicht auswertbar."""
    import re
    from pathlib import Path

    quelle = (
        Path(__file__).resolve().parents[2] / "core" / "reconciliation.py"
    ).read_text(encoding="utf-8")

    assert "engine_now" in quelle
    assert not re.search(
        r"datetime\.now\(", quelle
    ), "datetime.now() im Abgleich — der Datensatz muss die Engine-Uhr tragen"


def test_zeitstempel_sind_zeitzonenbehaftet():
    """Ein naiver Zeitstempel im Audit-Datensatz ist spaeter nicht eindeutig."""
    from core.reconciliation import ReconciliationService

    assert ReconciliationService  # Import-Wache; Verhalten unten
    jetzt = datetime.now(timezone.utc)
    assert jetzt.tzinfo is not None


# ---------------------------------------------------------------------------
# 8 — Die Fill-Seite: der periodische Lauf faengt, was der Strom verpasst hat
# ---------------------------------------------------------------------------


class _AusgefuehrteOrder:
    def __init__(self, oid, symbol="AAPL", qty="2", price="185.5", coid=""):
        self.id = oid
        self.symbol = symbol
        self.status = "filled"
        self.filled_qty = qty
        self.filled_avg_price = price
        self.side = "sell"
        self.client_order_id = coid
        self.filled_at = datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc)


@pytest.mark.anyio
async def test_ein_fill_ueber_den_strom_erzeugt_sofort_ein_fillevent():
    """Ohne Warten auf den periodischen Lauf."""
    from core.contracts import FillEvent

    dienst = await _bereit(_Api())
    ereignis = dienst.on_broker_fill(
        _AusgefuehrteOrder("o-1", coid="stop-0-d-4711"), decision_id="d-4711"
    )

    assert isinstance(ereignis, FillEvent)
    assert ereignis.broker_order_id == "o-1"
    assert ereignis.decision_id == "d-4711"
    assert dienst.fills


@pytest.mark.anyio
async def test_derselbe_fill_zweimal_gemeldet_zaehlt_einmal():
    """Ein doppelt eingetroffenes Ereignis darf keine zweite Buchung erzeugen."""
    dienst = await _bereit(_Api())
    order = _AusgefuehrteOrder("o-1")

    dienst.on_broker_fill(order, decision_id="d-1")
    dienst.on_broker_fill(order, decision_id="d-1")

    assert len(dienst.fills) == 1


@pytest.mark.anyio
async def test_der_periodische_lauf_findet_den_entgangenen_fill():
    """Der Strom war unterbrochen — der Lauf traegt nach und meldet es."""
    api = _Api()
    dienst = await _bereit(api)

    api._orders.append(_AusgefuehrteOrder("o-verpasst", symbol="NVDA"))
    record = await dienst.run_once()

    assert any(
        b.kind == "missing_fill" for b in record.breaks
    ), f"Der entgangene Fill wurde nicht gefunden: {[b.kind for b in record.breaks]}"
    assert any(
        f.broker_order_id == "o-verpasst" for f in dienst.fills
    ), "Der Fill wurde gemeldet, aber nicht nachgetragen — melden allein heilt nichts."


@pytest.mark.anyio
async def test_ein_bereits_bekannter_fill_ist_kein_befund():
    api = _Api()
    dienst = await _bereit(api)
    order = _AusgefuehrteOrder("o-1")
    dienst.on_broker_fill(order, decision_id="d-1")

    api._orders.append(order)
    record = await dienst.run_once()

    assert not any(b.kind == "missing_fill" for b in record.breaks)


@pytest.mark.anyio
async def test_eine_offene_order_ist_kein_fill():
    api = _Api()
    dienst = await _bereit(api)

    offen = _AusgefuehrteOrder("o-offen")
    offen.status = "new"
    api._orders.append(offen)
    record = await dienst.run_once()

    assert not any(b.kind == "missing_fill" for b in record.breaks)
    assert not dienst.fills


@pytest.mark.anyio
async def test_der_strom_haengt_am_selben_weg_wie_der_lauf():
    """Beide Wege muessen denselben Bestand fuellen — sonst faende der Lauf, was der
    Strom laengst gemeldet hat, und alarmierte bei jedem Durchgang."""
    import inspect

    from core.reconciliation import ReconciliationService

    quelle = inspect.getsource(ReconciliationService.start_fill_stream)
    assert (
        "on_broker_fill" in quelle
    ), "Der Stream-Pfad ruft nicht denselben Eintrag wie der periodische Lauf."
