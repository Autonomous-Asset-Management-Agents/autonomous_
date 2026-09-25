"""Vertraege an den Uebergaben H2 und H3 (#3378, ARC-E1.2).

Der Plan (docs/3378-vertraege-orderintent-compliancedecision/implementation_plan.md §7)
setzt die Reihenfolge: zuerst das Schema, dann die geschlossene Grund-Code-Liste, dann
die Serialisierungs-Paritaet. Ein ImportError ist hier die *richtige* rote Ursache — der
Gegenstand dieser Tests ist die Existenz und Form des Vertrags. (Fuer CH-2 aus #3366
gilt das Gegenteil: dort zaehlt ein Importfehler nicht als Beleg.)

Die Vertraege werden in diesem Schritt von niemandem aufgerufen. Sie binden die Form,
bevor #3379 die Fassade einzieht.
"""

import pytest
from pydantic import ValidationError

from core.contracts import ComplianceDecision, OrderIntent, ReasonCode

pytestmark = pytest.mark.unit


def _intent(**overrides):
    base = dict(
        decision_id="dec-1",
        symbol="AAPL",
        side="sell",
        qty=1.0,
        intent_kind="entry",
        halted=False,
    )
    base.update(overrides)
    return OrderIntent(**base)


# ---------------------------------------------------------------------------
# Pflichtfelder
# ---------------------------------------------------------------------------


def test_intent_without_decision_id_is_rejected():
    with pytest.raises(ValidationError) as exc:
        OrderIntent(
            symbol="AAPL", side="sell", qty=1.0, intent_kind="entry", halted=False
        )
    assert "decision_id" in str(exc.value)


def test_empty_decision_id_is_rejected():
    """Ein leerer String ist kein Beleg. Ohne diese Pruefung wuerde `decision_id=""`
    die Pflichtfeldpruefung formal bestehen und die Rueckfuehrbarkeit still aushoehlen.
    """
    with pytest.raises(ValidationError):
        _intent(decision_id="")


def test_decision_without_reason_code_is_rejected():
    with pytest.raises(ValidationError) as exc:
        ComplianceDecision(decision_id="dec-1", approved=True, halted=False)
    assert "reason_code" in str(exc.value)


# ---------------------------------------------------------------------------
# Geschlossene Grund-Code-Liste
# ---------------------------------------------------------------------------


def test_unknown_reason_code_is_rejected():
    with pytest.raises(ValidationError):
        ComplianceDecision(
            decision_id="dec-1",
            approved=False,
            reason_code="voellig_erfunden",
            halted=False,
        )


def test_the_codes_the_guardian_already_uses_are_all_present():
    """Die Liste uebernimmt die sieben Codes aus core/compliance.py 1:1 — sonst waere die
    spaetere Umstellung eine stille Neuerfindung statt einer Uebernahme."""
    aus_dem_guardian = {
        "restricted_symbol",
        "non_spot_us_equity",
        "missing_mifid_fields",
        "wash_trade",
        "max_order_value",
        "hft_throttle",
        "system_error",
    }
    vorhanden = {c.value for c in ReasonCode}
    assert aus_dem_guardian <= vorhanden, aus_dem_guardian - vorhanden


def test_approval_carries_a_code_too():
    d = ComplianceDecision(
        decision_id="dec-1",
        approved=True,
        reason_code=ReasonCode.APPROVED,
        halted=False,
    )
    assert d.approved is True
    assert d.reason_code is ReasonCode.APPROVED


# ---------------------------------------------------------------------------
# Unbekannte Felder
# ---------------------------------------------------------------------------


def test_unknown_field_is_rejected_and_named():
    with pytest.raises(ValidationError) as exc:
        _intent(tyop="entry")
    assert "tyop" in str(exc.value)


# ---------------------------------------------------------------------------
# Schutz-Exit
# ---------------------------------------------------------------------------


def test_protective_exit_is_marked_and_not_rewritable():
    intent = _intent(intent_kind="stop", is_protective_exit=True)
    assert intent.is_protective_exit is True
    with pytest.raises(ValidationError):
        intent.is_protective_exit = False


def test_exit_kind_mirrors_the_existing_classification():
    """classify_exit_kind (order_executor.py:591) kennt risk / rotation / trim. Der Vertrag
    muss die Unterscheidung tragen, sonst geht die Freistellungs-Logik beim Umbau verloren.
    """
    for kind in ("risk", "rotation", "trim"):
        assert _intent(intent_kind="stop", exit_kind=kind).exit_kind == kind
    with pytest.raises(ValidationError):
        _intent(intent_kind="stop", exit_kind="irgendwas")


# ---------------------------------------------------------------------------
# Bruchstuecke
# ---------------------------------------------------------------------------


def test_fractional_quantity_survives_unchanged():
    """Fraktionale Positionen sind Owner-Entscheid — der Vertrag darf sie nicht wegrunden."""
    intent = _intent(qty=0.4429)
    assert intent.qty == 0.4429
    assert intent.model_dump(mode="json")["qty"] == 0.4429


def test_zero_or_negative_quantity_is_rejected():
    for bad in (0.0, -1.0):
        with pytest.raises(ValidationError):
            _intent(qty=bad)


# ---------------------------------------------------------------------------
# Editionsneutralitaet
# ---------------------------------------------------------------------------


def test_serialisation_does_not_depend_on_the_edition(monkeypatch):
    """BORA: derselbe Intent, beide Zusammenstellungen, identisches Ergebnis.

    Der Vertrag liest keine Konfiguration — dieser Test haelt genau das fest, damit
    niemand spaeter einen editionsabhaengigen Default einbaut.
    """
    intent = _intent(qty=2.5, intent_kind="displacement")

    monkeypatch.setenv("DEPLOYMENT_MODE", "CLOUD")
    enterprise = intent.model_dump(mode="json")
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    desktop = intent.model_dump(mode="json")

    assert enterprise == desktop


def test_dump_is_stable_across_calls():
    intent = _intent()
    assert intent.model_dump(mode="json") == intent.model_dump(mode="json")
