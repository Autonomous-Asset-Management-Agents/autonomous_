"""#3396 (ARC-E3) — Stufen- und Uebergabe-Marker als Quelle der Wahrheit.

Plan: ``docs/3396-testmarker-je-stufe-und-uebergabe/implementation_plan.md``.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests import stufen

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/


def _registrierte_marker(pytestconfig) -> set[str]:
    return {zeile.split(":", 1)[0].strip() for zeile in pytestconfig.getini("markers")}


# ── 1. Marker sind registriert (strict-markers) ──────────────────────────────


def test_jede_stufe_und_jede_uebergabe_ist_registriert(pytestconfig):
    fehlend = (set(stufen.STUFEN) | set(stufen.UEBERGABEN)) - _registrierte_marker(
        pytestconfig
    )
    assert not fehlend, f"Nicht registriert (--strict-markers bricht ab): {fehlend}"


def test_die_stufen_folgen_der_value_chain():
    assert list(stufen.STUFEN) == ["vc0", "vc1", "vc2", "vc3", "vc4", "vc5", "vc6"]
    assert stufen.UEBERGABEN == {
        "h1": ("vc1", "vc2"),
        "h2": ("vc2", "vc4"),
        "h3": ("vc4", "vc3"),
        "h4": ("vc3", "vc5"),
        "h5": ("vc5", "vc6"),
    }


def test_ein_unbekannter_stufen_marker_bricht_den_lauf(tmp_path):
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    vc3: Stufe\n",
        encoding="utf-8",
    )
    (tmp_path / "test_probe.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.vc3\ndef test_bekannt():\n    pass\n\n"
        "@pytest.mark.vc9\ndef test_unbekannt():\n    pass\n",
        encoding="utf-8",
    )
    lauf = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--strict-markers",  # wie ai_trading_bot/pyproject.toml (addopts)
            "-p",
            "no:cacheprovider",
            "-p",
            "no:allure_pytest_bdd",
            "-q",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert lauf.returncode != 0
    assert "'vc9' not found in `markers`" in lauf.stdout + lauf.stderr


# ── 3. Das Allure-Label wird aus dem Marker abgeleitet ───────────────────────


def _allure_marke(stufe):
    import allure

    @allure.feature(stufen.STUFEN[stufe])
    def probe():
        pass

    marken = getattr(probe, "pytestmark", [])
    assert marken, "allure-pytest ist nicht aktiv — der Vergleich waere leer."
    return marken[0]


@pytest.mark.parametrize("stufe", list(stufen.STUFEN))
def test_die_ableitung_erzeugt_dieselbe_marke_wie_die_handarbeit(stufe):
    hand = _allure_marke(stufe)
    (abgeleitet,) = stufen.abgeleitete_allure_marken([stufe])
    assert (abgeleitet.name, abgeleitet.args, abgeleitet.kwargs) == (
        hand.name,
        hand.args,
        hand.kwargs,
    )


def test_ohne_stufe_wird_kein_label_abgeleitet():
    assert stufen.abgeleitete_allure_marken(["h2", "unit"]) == []


@pytest.mark.vc3
def test_der_hook_setzt_das_label_am_gesammelten_test(request):
    labels = [
        m.args
        for m in request.node.iter_markers("allure_label")
        if m.kwargs.get("label_type") == "feature"
    ]
    assert (stufen.STUFEN["vc3"],) in labels


# ── 2. Ratsche: Tests ohne Stufe und ohne Uebergabe ──────────────────────────


def _schreibe(wurzel, rel, quelle):
    pfad = wurzel / rel
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(textwrap.dedent(quelle), encoding="utf-8")


def test_die_zaehlung_kennt_alle_orte_eines_markers(tmp_path):
    _schreibe(
        tmp_path,
        "tests/unit/test_a.py",
        """\
        import pytest

        pytestmark = pytest.mark.vc1

        def test_modul():
            pass
        """,
    )
    _schreibe(
        tmp_path,
        "tests/unit/test_b.py",
        """\
        import pytest

        pytestmark = [pytest.mark.unit, pytest.mark.h2]

        def test_liste():
            pass
        """,
    )
    _schreibe(
        tmp_path,
        "tests/unit/test_c.py",
        """\
        import pytest

        @pytest.mark.vc3
        class TestKlasse:
            def test_methode(self):
                pass

        class TestOhne:
            @pytest.mark.vc4
            def test_mit(self):
                pass

            def test_ohne(self):
                pass

        @pytest.mark.vc5
        async def test_async():
            pass

        def test_nackt():
            pass

        def hilfe():
            pass
        """,
    )
    _schreibe(
        tmp_path, "tests/unit/helfer.py", "def test_kein_testmodul():\n    pass\n"
    )
    ohne = stufen.tests_ohne_zuordnung(tmp_path, ["tests/unit"])
    assert sorted(ohne) == [
        "tests/unit/test_c.py::TestOhne::test_ohne",
        "tests/unit/test_c.py::test_nackt",
    ]


def test_ratsche_ohne_zuordnung():
    ohne = stufen.tests_ohne_zuordnung(PAKET, stufen.TESTPFADE)
    grenze = stufen.OBERGRENZE_OHNE_ZUORDNUNG
    assert len(ohne) <= grenze, (
        f"{len(ohne)} Tests ohne Stufe und ohne Uebergabe, erlaubt {grenze}. Ein neuer "
        f"Test traegt seine Stufe (@pytest.mark.vc1 … vc6, vc0 fuer Plattform) oder "
        f"seine Uebergabe (@pytest.mark.h1 … h5)."
    )
    assert len(ohne) >= grenze, (
        f"Nur noch {len(ohne)} Tests ohne Zuordnung, eingecheckt sind {grenze}. Senke "
        f"OBERGRENZE_OHNE_ZUORDNUNG in tests/stufen.py auf {len(ohne)}."
    )


# ── 5. Auswertung je Stufe und Uebergabe ─────────────────────────────────────


def test_die_auswertung_zaehlt_je_stufe_und_weist_luecken_aus():
    bericht = stufen.auswertung(
        [["vc1"], ["vc1", "unit"], ["vc2"], ["vc4", "h3"], ["unit"]]
    )
    assert bericht.je_stufe["vc1"] == 2 and bericht.je_stufe["vc2"] == 1
    assert "vc5" in bericht.luecken_stufen and "vc1" not in bericht.luecken_stufen
    assert bericht.je_uebergabe["h3"] == 1
    assert bericht.ohne_zuordnung == 1


def test_eine_uebergabe_ist_nicht_durch_stufentests_ersetzbar():
    """Beide Stufen an H1 haben Tests, die Uebergabe selbst keinen: Luecke."""
    bericht = stufen.auswertung([["vc1"], ["vc2"]])
    assert "h1" in bericht.luecken_uebergaben


def test_der_bericht_nennt_zahlen_und_luecken():
    text = stufen.auswertung([["vc1"], ["h2"]]).als_text()
    assert "VC-1 Research & Analysis" in text and "1" in text
    assert "LUECKE" in text
