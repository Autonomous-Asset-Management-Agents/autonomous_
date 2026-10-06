"""#3636 (ARC-E3) — Ein Verweis zeigt auf das, was er meint, oder er bricht laut.

Plan: ``docs/3636-doku-wahrheit/implementation_plan.md`` (plan-approved 25.09.2026).

Drei Luecken, die #2785 offen gelassen hat:

1. **Die Symbolpruefung war weich.** Stand vor dem Anker ein Symbol in Backticks, pruefte
   ``check_doc_anchors.py``, ob es in der Naehe der genannten Zeile vorkommt — und meldete
   sonst ``soft``. Weiche Befunde zaehlen weder in die Ratsche noch brechen sie den Lauf.
2. **Die Obergrenze wanderte mit dem Schaden.** Sie wurde viermal in demselben PR
   angehoben, der sie brach; aus 40 wurden in fuenf Tagen 575.
3. **Der Gesamtbestand lief in keiner Pflicht-Pruefung.** Der Workflow bildet die Dateiliste
   aus dem PR-Diff, ein reiner Code-PR konnte also beliebig viele Anker in unberuehrten
   Dokumenten brechen, ohne rot zu werden.

Diese Tests laufen unter ``tests/unit`` und damit im Pflicht-Job ``Backend`` — das ist
Punkt 3, ohne den Workflow anzufassen (der braeuchte ``governance-bypass``, Plan §8).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

import check_doc_anchors as pruefer  # noqa: E402

pytestmark = pytest.mark.vc0


def _quelle(tmp_path: Path) -> Path:
    """Ein Ziel mit einem benannten Feld und viel Text darum herum."""
    ziel = tmp_path / "ziel.py"
    zeilen = ["# Kopf"] * 40
    zeilen[20] = "ECHTES_FELD: bool = True"
    ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return ziel


# ── 1. Der Namensanker ────────────────────────────────────────────────────────


def test_ein_namensanker_loest_ueber_den_namen_auf(tmp_path):
    """``ziel.py::ECHTES_FELD`` ist gueltig, egal in welcher Zeile das Feld steht."""
    _quelle(tmp_path)
    idx = {"ziel.py": [str(tmp_path / "ziel.py")]}
    hart, _weich, gesehen = pruefer.check_text(
        "doku.md", "Der Schalter `ziel.py::ECHTES_FELD` steht auf True.", idx
    )
    assert gesehen == 1, "der Namensanker wurde gar nicht erkannt"
    assert hart == []


def test_ein_namensanker_auf_einen_verschwundenen_namen_bricht(tmp_path):
    _quelle(tmp_path)
    idx = {"ziel.py": [str(tmp_path / "ziel.py")]}
    hart, _weich, _ = pruefer.check_text(
        "doku.md", "Der Schalter `ziel.py::GAB_ES_NIE` steht auf True.", idx
    )
    assert len(hart) == 1 and "GAB_ES_NIE" in hart[0], hart


# ── 2. Die Symbolpruefung ist hart ────────────────────────────────────────────


def test_ein_symbol_das_nicht_am_ziel_steht_ist_ein_harter_fehler(tmp_path):
    """Heute rot: Der Befund ist ``soft`` und zaehlt nirgends."""
    _quelle(tmp_path)
    idx = {"ziel.py": [str(tmp_path / "ziel.py")]}
    hart, _weich, _ = pruefer.check_text(
        "doku.md", "`ANDERES_FELD` (`ziel.py#L21`)", idx
    )
    assert len(hart) == 1 and "ANDERES_FELD" in hart[0], hart


def test_ein_wort_aus_einem_anderen_halbsatz_zaehlt_nicht(tmp_path):
    """Der Abstand ist eng: sonst meldet die Regel Prosa als Drift."""
    _quelle(tmp_path)
    idx = {"ziel.py": [str(tmp_path / "ziel.py")]}
    hart, _weich, _ = pruefer.check_text(
        "doku.md",
        "`ANDERES_FELD` wird an einer ganz anderen Stelle erklaert, siehe `ziel.py#L21`",
        idx,
    )
    assert hart == []


def test_ein_symbol_das_am_ziel_steht_ist_kein_fehler(tmp_path):
    _quelle(tmp_path)
    idx = {"ziel.py": [str(tmp_path / "ziel.py")]}
    hart, _weich, _ = pruefer.check_text(
        "doku.md", "`ECHTES_FELD` steht in `ziel.py#L21`.", idx
    )
    assert hart == []


# ── 3. Die Obergrenze wandert nicht mit dem Schaden ───────────────────────────


def _grenze(tmp_path: Path, text: str) -> Path:
    pfad = tmp_path / "obergrenze.txt"
    pfad.write_text(text, encoding="utf-8")
    return pfad


def test_die_obergrenze_ist_der_letzte_eintrag(tmp_path):
    pfad = _grenze(
        tmp_path,
        "# datum  wert  grund\n2026-09-19  538  #2785 Einfuehrung\n"
        "2026-09-25  211  #3636 Ankerumzug\n",
    )
    assert pruefer.lade_obergrenze(pfad) == 211


def test_eine_anhebung_ohne_freigabe_schlaegt_fehl(tmp_path):
    pfad = _grenze(
        tmp_path,
        "2026-09-19  40  #2785 Einfuehrung\n2026-09-22  538  #3553 hat config.py gekuerzt\n",
    )
    with pytest.raises(ValueError, match="FREIGABE"):
        pruefer.lade_obergrenze(pfad)


def test_eine_anhebung_mit_freigabe_ist_zulaessig(tmp_path):
    pfad = _grenze(
        tmp_path,
        "2026-09-19  40  #2785 Einfuehrung\n"
        "2026-09-22  538  FREIGABE Owner 22.09.: #3553 kuerzt config.py auf 55 Zeilen\n",
    )
    assert pruefer.lade_obergrenze(pfad) == 538


def test_eine_senkung_braucht_keine_zeremonie(tmp_path):
    pfad = _grenze(tmp_path, "2026-09-19  538  x\n2026-09-25  211  #3636\n")
    assert pruefer.lade_obergrenze(pfad) == 211


# ── 4. Der Gesamtbestand laeuft im Pflicht-Job ────────────────────────────────


def test_der_gesamtbestand_haelt_seine_obergrenze(capsys):
    """Punkt 3: Diese Zeile ist die Pflicht-Pruefung. Sie sieht ALLE Dokumente,
    nicht nur die im Diff — ein reiner Code-PR kann Anker anderswo nicht mehr still
    brechen."""
    code = pruefer._gesamt(pruefer.build_index())
    ausgabe = capsys.readouterr().out
    assert code == 0, ausgabe[-4000:]
