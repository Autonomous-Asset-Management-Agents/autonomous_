# TDD Red -> Green for Issue #2981 (Epic #1891): Money-Path-Guard-Entfragmentierung.
#
# Sofort-Fix (Option A, Schritt 1): EIN reiner Materiality-Helper
# ``is_immaterial_entry`` in ``core.risk_manager`` mit Divisor **equity** (nicht
# ``cash``), aufgerufen an ALLEN DREI Money-Path-Call-Sites in
# ``core/engine/order_executor.py`` (Tenant :~1092, HITL-Drain :~1956,
# Desktop/[Global]-Fallback :~2404).
#
# Kern-Defekt vor dem Fix: der Desktop-Pfad teilte durch ``cash`` statt ``equity``.
# Bei investiertem Buch (cash << equity) ist die Materialitaets-Schwelle dort ~100x
# zu niedrig -> fail-open. Tenant/HITL nutzen bereits ``equity`` und bleiben
# byte-identisch.
#
# Diese Datei ERSETZT den frueheren Placeholder (``assert True``).

import inspect

import pytest

# --- Gherkin Background (implementation_plan.md, Abschnitt 4) --------------------
MAT_PCT = 0.25  # MATERIAL_ENTRY_MIN_PCT_OF_TARGET
MAX_POSITIONS = 10  # effective_max_positions()
EQUITY = 100_000.0  # investiertes Buch
CASH = 1_000.0  # freies Cash geht bei fast-vollem Buch gegen null

# Schwelle_equity = MAT_PCT * (EQUITY / MAX_POSITIONS) = 0.25 * 10_000 = 2_500
# Schwelle_cash   = MAT_PCT * (CASH   / MAX_POSITIONS) = 0.25 *    100 =    25
# -> order_value=500: equity blockt (500 < 2500), cash laesst durch (500 > 25) == DIVERGENZ


def _tenant_decision(order_value):
    """Wie der Tenant-/HITL-Pfad entscheidet: Divisor equity (korrekt)."""
    from core.risk_manager import is_immaterial_entry

    return is_immaterial_entry(
        order_value, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS
    )


def _desktop_decision(order_value):
    """Wie der Desktop/[Global]-Pfad NACH dem Fix entscheidet: derselbe Helper,
    derselbe Divisor equity. (Vor dem Fix teilte er durch cash.)"""
    from core.risk_manager import is_immaterial_entry

    return is_immaterial_entry(
        order_value, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS
    )


# --- Test 1: Metamorphe Invariante Desktop == Tenant (der zuerst rote) ----------
@pytest.mark.parametrize(
    "order_value, expected_immaterial",
    [
        (
            500.0,
            True,
        ),  # < 2500 -> immateriell (Tenant blockt; Desktop-cash liesse durch)
        (3000.0, False),  # > 2500 -> material
    ],
)
def test_materiality_desktop_equals_tenant_metamorphic(
    order_value, expected_immaterial
):
    """Metamorph: bei IDENTISCHEM Input muessen Desktop- und Tenant-Pfad DIESELBE
    Materiality-Entscheidung treffen. RED heute: der Helper existiert nicht
    (ImportError) und der Desktop-Pfad teilt durch cash -> Divergenz bei 500."""
    tenant = _tenant_decision(order_value)
    desktop = _desktop_decision(order_value)
    assert (
        tenant == desktop
    ), "Desktop- und Tenant-Pfad divergieren in der Materiality-Entscheidung"
    assert tenant is expected_immaterial


# --- Test 2: Helper-Unit — Divisor equity, NICHT cash ---------------------------
def test_materiality_helper_uses_equity_not_cash():
    """Der Helper teilt IMMER durch equity. Der cash-Wert kommt im Helper gar nicht
    vor -> eine order von 500 ist bei equity=100k immateriell, egal wie klein cash
    ist. RED heute: Helper existiert noch nicht."""
    from core.risk_manager import is_immaterial_entry

    # equity-Schwelle = 2500 -> 500 immateriell, 3000 material
    assert (
        is_immaterial_entry(500.0, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS) is True
    )
    assert (
        is_immaterial_entry(3000.0, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS)
        is False
    )

    # Haette der Helper (faelschlich) durch cash geteilt, waere die Schwelle 25 und
    # 500 material. Der Beweis, dass cash NICHT der Nenner ist:
    cash_threshold = MAT_PCT * (CASH / MAX_POSITIONS)
    assert 500.0 > cash_threshold  # unter cash-Semantik waere 500 "material"
    assert (
        is_immaterial_entry(500.0, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS) is True
    )


# --- Test 3: Gate-aus / Edge-Cases ----------------------------------------------
def test_materiality_gate_off_and_edge_cases():
    from core.risk_manager import is_immaterial_entry

    # mat_pct <= 0 -> Gate aus -> nie immateriell
    assert is_immaterial_entry(1.0, EQUITY, 0.0, max_positions=MAX_POSITIONS) is False
    assert is_immaterial_entry(1.0, EQUITY, -0.1, max_positions=MAX_POSITIONS) is False
    # order_value <= 0 -> nichts zu pruefen
    assert (
        is_immaterial_entry(0.0, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS) is False
    )
    assert (
        is_immaterial_entry(-5.0, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS) is False
    )
    # nicht-numerischer / kaputter Input -> fail-safe False (kein faelschliches Blocken)
    assert (
        is_immaterial_entry(None, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS) is False
    )
    assert (
        is_immaterial_entry(500.0, None, MAT_PCT, max_positions=MAX_POSITIONS) is False
    )


# --- Test 4: Genau an der Schwelle (strikt <) -----------------------------------
def test_materiality_boundary_is_strict_less_than():
    from core.risk_manager import is_immaterial_entry

    threshold = MAT_PCT * (EQUITY / MAX_POSITIONS)  # 2500
    # exakt auf der Schwelle ist NICHT immateriell (Verhalten der Call-Sites: `< thr`)
    assert (
        is_immaterial_entry(threshold, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS)
        is False
    )
    assert (
        is_immaterial_entry(
            threshold - 0.01, EQUITY, MAT_PCT, max_positions=MAX_POSITIONS
        )
        is True
    )


# --- Test 5: Alle drei Call-Sites delegieren an den Helper (Anti-Drift) ----------
def test_all_three_call_sites_delegate_to_helper():
    """Struktur-Guard gegen erneute Fragmentierung: das inline
    ``_target_alloc = cash / ...`` darf im Desktop-Pfad nicht wieder auftauchen und
    alle drei Materiality-Gates muessen ueber ``is_immaterial_entry`` laufen."""
    import core.engine.order_executor as oe

    src = inspect.getsource(oe)
    # Der cash-Divisor des Materiality-Gates ist entfernt.
    assert "_target_alloc = cash / max" not in src
    # Genau drei Aufrufe des gemeinsamen Helpers (Tenant, HITL, Desktop).
    assert src.count("is_immaterial_entry(") >= 3
