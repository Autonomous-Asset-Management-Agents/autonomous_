"""#3418 — Sitzungsdeckel fuer die Verdraengung (Owner-Entscheid: Variante B).

Die Verdraengung verfolgt dieselbe Absicht wie die Rotation — „den schwaechsten Titel
loswerden" — hat aber bisher keinen Sitzungsdeckel. Die Rotation hat einen
(``ROTATION_MAX_EXITS_PER_SESSION``), und zwar aus einem gemessenen Grund:

    Der per-Zyklus-Deckel allein bindet KEINEN Buchdurchlauf. Verdraengungs-Verkaeufe
    sind risikomindernde SELLs und damit von der Tages-Handelsgrenze ausgenommen; der
    Zyklus laeuft rund 390-mal je Sitzung. Eine anhaltende Rangverschiebung leert das
    Buch dann Name fuer Name ueber den Tag — genau der Flush vom 28.07.

Was dieser Test NICHT prueft, weil es bereits existiert oder ausdruecklich gestrichen ist:

* die **Haltefrist** — seit #2696/ADR-R14 vorhanden (``_displacement_min_hold_days`` +
  Veto in ``debate_position_swap``);
* der **Ob-Schalter** — seit #3291 vorhanden (``DISPLACEMENT_ENABLED``);
* **fail-closed bei unbekanntem Alter** — Owner-Entscheid 17.09.: das heutige
  Fail-open-Verhalten aus #2935 bleibt. „Wir wissen nicht, wie lange das gehalten wird"
  ist kein Beleg dafuer, dass eine Position Schutz verdient.
"""

from datetime import date, datetime, timezone

import pytest


class _Kandidat:
    def __init__(self, symbol="NVDA", total_score=90.0):
        self.symbol = symbol
        self.total_score = total_score


class _Position:
    def __init__(self, symbol="AAPL", total_score=10.0, days_held=99.0):
        self.symbol = symbol
        self.total_score = total_score
        self.days_held = days_held
        self.age_known = True


def _pm():
    """PortfolioManager ohne Broker — nur die Entscheidungslogik."""
    from core.portfolio_manager import PortfolioManager

    return PortfolioManager.__new__(PortfolioManager)


# ---------------------------------------------------------------------------
# 1 — Der Deckel existiert und ist konfigurierbar
# ---------------------------------------------------------------------------


def test_der_deckel_steht_in_der_konfiguration():
    from config import RuntimeConfigState

    cfg = RuntimeConfigState()
    assert hasattr(cfg, "DISPLACEMENT_MAX_PER_SESSION")
    assert isinstance(cfg.DISPLACEMENT_MAX_PER_SESSION, int)


def test_beide_editionen_tragen_denselben_deckel():
    """BORA: der Desktop verdraengt nicht oefter als die Enterprise-Edition."""
    import importlib.util
    from pathlib import Path

    from config import RuntimeConfigState

    spec = importlib.util.spec_from_file_location(
        "config_oss_3418", str(Path(__file__).resolve().parents[2] / "config.oss.py")
    )
    oss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oss)

    assert (
        oss.DISPLACEMENT_MAX_PER_SESSION
        == RuntimeConfigState().DISPLACEMENT_MAX_PER_SESSION
    )


def test_der_deckel_steht_in_der_settings_registry():
    """Einstellbar wie die Rotation — nicht nur ueber Umgebungsvariablen."""
    from core.trading_settings import REGISTRY

    assert "DISPLACEMENT_MAX_PER_SESSION" in REGISTRY
    eintrag = REGISTRY["DISPLACEMENT_MAX_PER_SESSION"]
    assert eintrag.default == 2, "Der Default muss dem der Rotation folgen"


def test_der_deckel_steht_in_der_konsole_und_haengt_am_schalter():
    """Das Feld graut aus, wenn die Verdraengung abgeschaltet ist — Muster der Rotation."""
    from pathlib import Path

    quelle = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "console"
        / "desktop"
        / "advancedTradingFields.ts"
    ).read_text(encoding="utf-8")

    i = quelle.find("DISPLACEMENT_MAX_PER_SESSION")
    assert i > 0, "Das Deckel-Feld fehlt in der Konsole"
    umfeld = quelle[i : i + 400]
    assert 'dependsOn: "DISPLACEMENT_ENABLED"' in umfeld, (
        "Das Deckel-Feld haengt nicht am Ob-Schalter — es bliebe bedienbar, obwohl der "
        "Mechanismus aus ist."
    )


# ---------------------------------------------------------------------------
# 2 — Der Deckel bindet
# ---------------------------------------------------------------------------


def test_unter_dem_deckel_wird_verdraengt():
    pm = _pm()
    pm._displacement_session_date = date(2026, 9, 17)
    pm._displacements_session = 0

    erlaubt, grund = pm._displacement_session_budget_ok(cap=2, heute=date(2026, 9, 17))

    assert erlaubt is True
    assert grund == ""


def test_am_deckel_wird_nicht_mehr_verdraengt():
    pm = _pm()
    pm._displacement_session_date = date(2026, 9, 17)
    pm._displacements_session = 2

    erlaubt, grund = pm._displacement_session_budget_ok(cap=2, heute=date(2026, 9, 17))

    assert erlaubt is False
    assert (
        "2" in grund
    ), "Der Grund muss die Zahl nennen, sonst ist er nicht nachpruefbar"


def test_der_zaehler_setzt_sich_zum_naechsten_handelstag_zurueck():
    """Ein Deckel, der nicht zurueckgesetzt wird, ist ein Einmal-Verbot."""
    pm = _pm()
    pm._displacement_session_date = date(2026, 9, 17)
    pm._displacements_session = 2

    erlaubt, _ = pm._displacement_session_budget_ok(cap=2, heute=date(2026, 9, 18))

    assert erlaubt is True
    assert pm._displacements_session == 0


def test_deckel_null_heisst_unbegrenzt():
    """Byte-identischer Rueckweg — wie <=0 bei der Rotation."""
    pm = _pm()
    pm._displacement_session_date = date(2026, 9, 17)
    pm._displacements_session = 999

    erlaubt, _ = pm._displacement_session_budget_ok(cap=0, heute=date(2026, 9, 17))

    assert erlaubt is True


def test_ein_frischer_manager_ohne_zaehler_faellt_nicht_um():
    """Der Mixin kann ueber __new__ gebaut werden — nie annehmen, dass __init__ lief."""
    pm = _pm()

    erlaubt, _ = pm._displacement_session_budget_ok(cap=2, heute=date(2026, 9, 17))

    assert erlaubt is True


# ---------------------------------------------------------------------------
# 3 — Gezaehlt wird, was beschlossen wird
# ---------------------------------------------------------------------------


def test_eine_beschlossene_verdraengung_wird_gezaehlt():
    pm = _pm()
    pm._displacement_session_date = date(2026, 9, 17)
    pm._displacements_session = 0

    pm._note_displacement(heute=date(2026, 9, 17))

    assert pm._displacements_session == 1


def test_der_zaehler_faengt_am_neuen_tag_bei_eins_an():
    pm = _pm()
    pm._displacement_session_date = date(2026, 9, 17)
    pm._displacements_session = 5

    pm._note_displacement(heute=date(2026, 9, 18))

    assert pm._displacements_session == 1
    assert pm._displacement_session_date == date(2026, 9, 18)


# ---------------------------------------------------------------------------
# 4 — Die Naht zum Entscheidungspfad
# ---------------------------------------------------------------------------


def test_der_deckel_wird_im_entscheidungspfad_geprueft():
    """Quellbeleg: die Pruefung sitzt neben dem Ob-Schalter aus #3291.

    Sie muss VOR ``debate_position_swap`` stehen — dort oeffnet der Zweig
    ``if score_diff > 15: should_swap = True`` und ignoriert jedes Gegenargument. Ein
    Deckel, der als Argument in die Debatte ginge, waere in genau diesem Zweig
    wirkungslos; dieselbe Lehre wie bei ADR-R14.
    """
    import ast
    from pathlib import Path

    quelle = (
        Path(__file__).resolve().parents[2] / "core" / "portfolio_manager.py"
    ).read_text(encoding="utf-8")

    baum = ast.parse(quelle)
    ziel = next(
        n
        for n in ast.walk(baum)
        if isinstance(n, ast.FunctionDef) and n.name == "should_open_new_position"
    )
    # Ueber AST, nicht ueber Textsuche: ein Kommentar, der die Debatte ERWAEHNT, steht
    # sonst vor dem Aufruf und verfaelscht die Reihenfolge.
    aufrufe = {}
    for knoten in ast.walk(ziel):
        if isinstance(knoten, ast.Call):
            name = getattr(knoten.func, "attr", None)
            if name in ("_displacement_session_budget_ok", "debate_position_swap"):
                aufrufe.setdefault(name, knoten.lineno)

    assert (
        "_displacement_session_budget_ok" in aufrufe
    ), "Der Sitzungsdeckel wird im Entscheidungspfad nicht geprueft"
    assert "debate_position_swap" in aufrufe

    assert (
        aufrufe["_displacement_session_budget_ok"] < aufrufe["debate_position_swap"]
    ), (
        "Der Deckel steht NACH der Debatte — dort ist er wirkungslos, weil der "
        "score_diff-Zweig jedes Gegenargument ignoriert."
    )


def test_der_deckel_liest_die_konfiguration_zur_aufrufzeit():
    """Sonst geht ein vom Bediener gesetzter Wert inert — das ist hier schon passiert."""
    import ast
    from pathlib import Path

    quelle = (
        Path(__file__).resolve().parents[2] / "core" / "portfolio_manager.py"
    ).read_text(encoding="utf-8")

    baum = ast.parse(quelle)
    ziel = next(
        n
        for n in ast.walk(baum)
        if isinstance(n, ast.FunctionDef) and n.name == "_displacement_session_cap"
    )
    koerper = ast.get_source_segment(quelle, ziel) or ""

    assert "get_config" in koerper, "Der Deckel wird nicht zur Aufrufzeit gelesen"


def test_ein_lesefehler_bindet_nicht():
    """Fail-open, wie beim Haltefrist-Veto: der Deckel blockiert nur Handel."""
    pm = _pm()
    cap = pm._displacement_session_cap()

    assert isinstance(cap, int)
    assert cap >= 0
