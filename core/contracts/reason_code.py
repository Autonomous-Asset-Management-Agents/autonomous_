"""Geschlossene Grund-Code-Liste fuer ComplianceDecision (#3378, ARC-E1.2).

Warum geschlossen: Ein freies ``str``-Feld besteht jede Validierung und hoehlt die
Auswertbarkeit still aus — ein Tippfehler faellt erst dort auf, wo jemand den Wert
liest, im Zweifel Monate spaeter im Audit.

Die ersten sieben Werte sind **keine Neuerfindung**. Sie stehen so schon in
``core/compliance.py`` als maschinenlesbarer ``reason_code`` neben jedem Reject
(``restricted_symbol`` … ``system_error``) und werden hier 1:1 uebernommen, damit die
Umstellung in #3379 eine Uebernahme bleibt und keine stille Umbenennung wird.

Die uebrigen Werte benennen Faelle, die es im Verhalten bereits gibt, bisher aber nur
als Logzeile: die Tagesgrenze (``check_trade``), die Exit-Freistellung (#2065,
``_is_risk_reducing_exit``) und den Halt-Zustand.

Erweiterung: Ein neuer Code gehoert hierher UND in den Pfad, der ihn setzt — ein Code
ohne Setzer ist tote Dokumentation, ein Setzer ohne Code faellt bei der Validierung auf.
"""

from enum import Enum


class ReasonCode(str, Enum):
    """Warum eine Pruefung so entschieden hat."""

    # --- uebernommen aus core/compliance.py (dort als reason_code gesetzt) ---
    RESTRICTED_SYMBOL = "restricted_symbol"
    NON_SPOT_US_EQUITY = "non_spot_us_equity"
    MISSING_MIFID_FIELDS = "missing_mifid_fields"
    WASH_TRADE = "wash_trade"
    MAX_ORDER_VALUE = "max_order_value"
    HFT_THROTTLE = "hft_throttle"
    SYSTEM_ERROR = "system_error"

    # --- im Verhalten vorhanden, bisher ohne maschinenlesbaren Code ---
    MAX_DAILY_TRADES = "max_daily_trades"
    EXIT_EXEMPT = "exit_exempt"
    TRADING_HALTED = "trading_halted"

    # --- bevorrechtigte Ausnahmepfade (#3383) ---
    # Sie werden nie blockiert, aber immer protokolliert. Der eigene Code sagt im Audit,
    # WARUM eine Bewegung ohne Einstiegspruefung herausging — "exit_exempt" allein
    # koennte einen Notverkauf nicht von einem Schutz-Stop unterscheiden.
    EMERGENCY = "emergency"
    BREAKER = "breaker"
    STRATEGY_SWITCH = "strategy_switch"

    # --- der positive Fall ---
    APPROVED = "approved"

    def __str__(self) -> str:  # pragma: no cover - Diagnose
        return self.value
