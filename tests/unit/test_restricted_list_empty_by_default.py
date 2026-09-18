# tests/unit/test_restricted_list_empty_by_default.py
"""#3222 — die Restricted List ist bewusst LEER, das Gate bleibt scharf.

Eine Restricted List sperrt Papiere aus RECHTLICHEN Gruenden — Insiderwissen,
Sanktionen, laufende Verfahren (MAR Art. 5). Sie hat nichts mit Qualitaet,
Performance oder Index-Zugehoerigkeit zu tun und nichts mit dem
Handelsuniversum (das kommt aus der S&P-500-Mitgliedschaft im data_provider).

Owner-Entscheidung 05.09.2026: **aktuell nicht benoetigt** — es gibt keine
institutionellen Anleger und damit keine Sperrpflicht.

Bis dahin standen dort ``SCAM_TOKEN`` und ``EVIL_CORP`` — Platzhalter aus dem
allerersten Scaffold-Commit vom 16.02.2026, dort woertlich als
``# Example restricted instruments`` markiert und nie ersetzt. Das ist der
schlechtestmoegliche Zustand fuer eine Compliance-Kontrolle: Im Audit sieht
sie aktiv aus, sperrt aber nichts Reales. ``AUDIT_VORGEHENSVORSCHLAG.md``
fuehrt sie deshalb als 🔴-High-Befund ("kein Pflegeprozess").

Eine leere Liste ist die ehrliche Aussage: es ist bewusst nichts gesperrt.
Das Gate bleibt bestehen und sofort nutzbar, falls sich das aendert — genau
das pinnt der zweite Test.
"""

import time

import allure

from core.compliance import ComplianceGuardian


def _order(symbol: str) -> dict:
    """Sonst valide Order (Form wie in test_compliance.py) — nur das Symbol variiert."""
    return {
        "symbol": symbol,
        "side": "buy",
        "quantity": 10,
        "price": 150.0,
        "strategy_id": "test_strat",
        "timestamp": time.time(),
    }


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestRestrictedListEmptyByDefault:
    def test_default_list_is_empty(self):
        """Keine Platzhalter mehr — die Liste sagt die Wahrheit."""
        guardian = ComplianceGuardian()

        assert guardian.restricted_list == [], (
            "Die Restricted List traegt wieder Eintraege. Platzhalter wie "
            "SCAM_TOKEN/EVIL_CORP lassen eine Kontrolle im Audit aktiv "
            "aussehen, die nichts sperrt. Echte Sperren gehoeren hier hinein "
            "— aber nur echte."
        )

    def test_gate_still_blocks_a_listed_symbol(self):
        """Das Gate bleibt scharf: was auf der Liste steht, wird abgelehnt.

        Der Nachweis wird bewusst mit einer im Test gesetzten Liste gefuehrt
        statt mit einem Produktions-Default — ein Test des Gates darf nicht
        davon abhaengen, was zufaellig ausgeliefert wird.
        """
        guardian = ComplianceGuardian()
        guardian.restricted_list = ["BLOCKED_SYM"]

        assert guardian.check_order(_order("BLOCKED_SYM")) is False
        assert (
            guardian.check_order(_order("AAPL")) is not False
        ), "ein nicht gelistetes Symbol darf nicht am Blocklist-Gate scheitern"
