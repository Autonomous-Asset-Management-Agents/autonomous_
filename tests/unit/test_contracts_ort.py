"""#3825 (ARC-E6 G-3) — die Pydantic-Modelle der Konsole liegen in ``core/contracts/``.

Plan: ``docs/3825-*/implementation_plan.md`` §6.

* **Ort** — ``api_routes.py`` definiert auf oberster Ebene kein ``BaseModel`` mehr.
* **Vertrag** — das OpenAPI-Schema der 23 Modelle ist Feld für Feld dasselbe wie vor dem
  Verschieben, und jede Route verweist noch auf dieselben Modelle. Die Momentaufnahme
  wurde **vor** dem Verschieben erzeugt und eingecheckt; neu schreiben nur mit
  ``python -m tests.unit.test_contracts_ort`` aus ``ai_trading_bot/`` — und nur, wenn
  der Vertrag sich bewusst ändert.
* **Größe** — die Senkung von ``api_routes.py`` ist in ``vertrag.toml`` mitgesenkt. Direkt
  gegen ``regeln.pruefe_groessen`` geprüft, weil ``test_groessen_gegen_den_code`` im
  Modus ``warnen`` steht (wie #3823).
"""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

_PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
_ROUTEN = "core/engine/api_routes.py"
_MOMENTAUFNAHME = _PAKET / "tests" / "fixtures" / "contracts_openapi_3825.json"

#: Die 23 Modelle, die bis #3825 in ``api_routes.py`` standen (Plan §1), und ihr
#: Domaenen-Modul unter ``core/contracts/`` (Plan §2.1, Arbeitsschnitt).
_ORT = {
    "SwapRequest": "abrechnung",
    "CheckoutRequest": "abrechnung",
    "ActivateRequest": "abrechnung",
    "ReconciliationReleaseRequest": "abgleich",
    "HitlQueueItemDTO": "hitl",
    "HitlPendingResponse": "hitl",
    "HitlPolicyDTO": "hitl",
    "HitlPolicyUpdateDTO": "hitl",
    "HitlApproveRequest": "hitl",
    "HitlRejectRequest": "hitl",
    "HitlActionResponse": "hitl",
    "PortfolioConstraintsProposeRequest": "portfolio",
    "PortfolioConstraintsApproveRequest": "portfolio",
    "PortfolioShapeRequest": "portfolio",
    "PortfolioShapeResponse": "portfolio",
    "SettingsDeviationItem": "einstellungen",
    "SettingsDeviationAckRequest": "einstellungen",
    "SettingsDeviationAckResponse": "einstellungen",
    "TradingSettingsRequest": "einstellungen",
    "TradingSettingsResponse": "einstellungen",
    "LiveEnableRequest": "live",
    "LiveEnableResponse": "live",
    "SwitchReconcileRequest": "abgleich",
}
MODELLE = tuple(_ORT)
_DOMAENEN = sorted(set(_ORT.values()))


def _basismodelle_auf_oberster_ebene(quelle: str) -> list[str]:
    baum = ast.parse(quelle)
    return [
        k.name
        for k in baum.body
        if isinstance(k, ast.ClassDef)
        and any(
            (isinstance(b, ast.Name) and b.id == "BaseModel")
            or (isinstance(b, ast.Attribute) and b.attr == "BaseModel")
            for b in k.bases
        )
    ]


def _verweise(knoten) -> set[str]:
    """Alle ``#/components/schemas/<Name>``-Verweise unterhalb eines Schema-Knotens."""
    gefunden: set[str] = set()
    if isinstance(knoten, dict):
        ref = knoten.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            gefunden.add(ref.rsplit("/", 1)[1])
        for wert in knoten.values():
            gefunden |= _verweise(wert)
    elif isinstance(knoten, list):
        for wert in knoten:
            gefunden |= _verweise(wert)
    return gefunden


def momentaufnahme() -> dict:
    """Die Schemata der 23 Modelle und die Routen, die auf sie verweisen."""
    from core.engine.api_routes import app

    schema = app.openapi()
    komponenten = schema.get("components", {}).get("schemas", {})
    routen: dict[str, list[str]] = {}
    for pfad, operationen in schema.get("paths", {}).items():
        for methode, operation in _operationen(operationen):
            treffer = sorted(_verweise(operation) & set(MODELLE))
            if treffer:
                routen[f"{methode.upper()} {pfad}"] = treffer
    return {
        "schemas": {name: komponenten.get(name) for name in MODELLE},
        "routen": dict(sorted(routen.items())),
    }


def _operationen(operationen: dict):
    return sorted((m, o) for m, o in operationen.items() if isinstance(o, dict))


def test_api_routes_definiert_kein_basemodel_auf_oberster_ebene():
    quelle = (_PAKET / _ROUTEN).read_text(encoding="utf-8")
    assert _basismodelle_auf_oberster_ebene(quelle) == []


def test_die_modelle_liegen_in_core_contracts():
    """Jedes der 23 Modelle ist in genau einem Domaenen-Modul definiert (Plan §2.1)."""
    gefunden: dict[str, list[str]] = {}
    for modul in _DOMAENEN:
        m = importlib.import_module(f"core.contracts.{modul}")
        for name, obj in vars(m).items():
            if isinstance(obj, type) and obj.__module__ == m.__name__:
                gefunden.setdefault(name, []).append(modul)
    assert {n: gefunden.get(n, []) for n in MODELLE} == {
        n: [m] for n, m in _ORT.items()
    }


def test_das_openapi_schema_der_modelle_ist_unveraendert():
    assert (
        _MOMENTAUFNAHME.exists()
    ), f"Momentaufnahme fehlt: {_MOMENTAUFNAHME} — vor dem Verschieben erzeugen"
    erwartet = json.loads(_MOMENTAUFNAHME.read_text(encoding="utf-8"))
    assert momentaufnahme() == erwartet


def test_die_senkung_von_api_routes_ist_eingecheckt():
    meldungen = regeln.pruefe_groessen(_PAKET, regeln.lade_vertrag())
    assert [m for m in meldungen if f"{_ROUTEN} " in m] == []


if __name__ == "__main__":  # Momentaufnahme neu schreiben (siehe Modul-Docstring)
    _MOMENTAUFNAHME.write_text(
        json.dumps(momentaufnahme(), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
