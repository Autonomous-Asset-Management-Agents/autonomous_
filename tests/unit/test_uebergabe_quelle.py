"""#3945 — Eigentests des Quelltext-Helfers ``_uebergabe_quelle``.

Plan: ``docs/3945-vorarbeit-testkonstruktion-und-waechter/implementation_plan.md`` §4/§5.

Die elf quelltextlesenden Wächter über ``_process_signal_event`` lesen künftig diesen
Helfer statt ``inspect.getsource``. Damit sie nicht stillschweigend wahr werden, muss der
Helfer zweierlei beweisen:

1. Solange es keine extrahierten Schritte gibt, liefert er **zeichengleich** dasselbe wie
   ``inspect.getsource(_process_signal_event)``.
2. Greift er ins Leere — Dirigent oder Schritt nicht auffindbar —, **erhebt** er, statt
   leeren Text zu liefern.
"""

from __future__ import annotations

import inspect
import sys
import textwrap
from pathlib import Path

import pytest

_HIER = Path(__file__).resolve().parent
if str(_HIER) not in sys.path:
    sys.path.insert(0, str(_HIER))

import _uebergabe_quelle as uq  # noqa: E402

from core.engine.order_executor import OrderExecutorMixin  # noqa: E402

pytestmark = pytest.mark.vc4


class _OhneSchritte:
    async def _dirigent(self, event):
        return None


def test_ohne_schritte_zeichengleich_mit_getsource():
    assert uq.uebergabe_quelle(
        klasse=_OhneSchritte, dirigent="_dirigent"
    ) == inspect.getsource(_OhneSchritte._dirigent)


def test_uebergabe_quelle_enthaelt_dirigent_und_alle_schritte():
    dirigent_src = inspect.getsource(OrderExecutorMixin._process_signal_event)
    schritte = uq._schritte(dirigent_src)
    assert schritte == [
        "_schritt_secrets",
        "_schritt_verkaufsmenge",
        "_schritt_bemessung",
        "_schritt_compliance",
        "_schritt_absenden",
        "_schritt_nachbuchen",
    ]
    erwartet = dirigent_src + "".join(
        uq._quelle_von(uq._komponiert(), name, "Schritt") for name in schritte
    )
    assert uq.uebergabe_quelle() == erwartet


def test_quelle_liefert_fuer_den_dirigenten_die_uebergabe():
    assert uq.quelle(OrderExecutorMixin._process_signal_event) == uq.uebergabe_quelle()


def test_quelle_liefert_fuer_jede_andere_funktion_getsource():
    fn = OrderExecutorMixin._execute_tenant_order
    assert uq.quelle(fn) == inspect.getsource(fn)


# ---------------------------------------------------------------------------
# Gegen Leerlauf
# ---------------------------------------------------------------------------


def test_fehlender_dirigent_erhebt_statt_leer_zu_liefern():
    with pytest.raises(LookupError, match="_gibt_es_nicht"):
        uq.uebergabe_quelle(dirigent="_gibt_es_nicht")


class _MitSchritten:
    """Eine Attrappe mit zwei Schritten, absichtlich in umgekehrter Reihenfolge definiert."""

    async def _dirigent(self, event):
        self._vorher(event)
        await self._schritt_zwei(event)
        self._schritt_eins(event)
        await self._schritt_zwei(event)

    def _schritt_eins(self, event):
        return "eins"

    async def _schritt_zwei(self, event):
        return "zwei"

    def _vorher(self, event):
        return None


class _MitFehlendemSchritt:
    async def _dirigent(self, event):
        await self._schritt_fehlt(event)


def test_schritte_folgen_in_der_reihenfolge_des_dirigenten():
    erwartet = (
        inspect.getsource(_MitSchritten._dirigent)
        + inspect.getsource(_MitSchritten._schritt_zwei)
        + inspect.getsource(_MitSchritten._schritt_eins)
    )
    assert uq.uebergabe_quelle(klasse=_MitSchritten, dirigent="_dirigent") == erwartet


def test_zusammengesetzte_quelle_bleibt_parsebar():
    """Die Wächter parsen die Quelle mit ``ast`` — Dirigent + Schritte muss parsen."""
    import ast

    src = uq.uebergabe_quelle(klasse=_MitSchritten, dirigent="_dirigent")
    namen = [
        n.name
        for n in ast.parse(textwrap.dedent(src)).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert namen == ["_dirigent", "_schritt_zwei", "_schritt_eins"]


def test_fehlender_schritt_erhebt():
    with pytest.raises(LookupError, match="_schritt_fehlt"):
        uq.uebergabe_quelle(klasse=_MitFehlendemSchritt, dirigent="_dirigent")
