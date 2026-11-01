"""Tests for scripts/check_doc_anchors.py — the doc anchor linter.

Gates against code reference anchor drift.
"""

import os
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
# Path to repo root: tests/unit -> repo root
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SCRIPT = os.path.join(REPO, "scripts", "check_doc_anchors.py")


def _run(md_text):
    fd, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(md_text)
        return subprocess.run(
            [sys.executable, SCRIPT, path],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
    finally:
        os.unlink(path)


def test_out_of_range_anchor_fails():
    r = _run("- `core/compliance.py#L99999`\n")
    assert r.returncode == 1, r.stdout
    assert "out of range" in r.stdout


def test_missing_file_anchor_fails():
    r = _run("- ghost `core/does_not_exist_xyz.py#L10`\n")
    assert r.returncode == 1, r.stdout
    assert "missing file" in r.stdout


def test_valid_in_range_anchor_passes():
    r = _run("- valid `core/compliance.py#L69`\n")
    assert r.returncode == 0, r.stdout


# ── #2785: repo-weit, zweite Schreibweise, Code-Kommentare, Ratsche ─────────


def _modul():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_doc_anchors", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.vc0
def test_die_schreibweise_ohne_L_wird_geprueft():
    r = _run("- `core/compliance.py:99999`\n")
    assert r.returncode == 1 and "out of range" in r.stdout, r.stdout


@pytest.mark.vc0
def test_die_schreibweise_ohne_L_gilt_auch_fuer_gueltige_anker():
    """Ein Filter ohne Treffer ist kein Beleg (WoW §13.4): derselbe Anker muss gefunden
    und bestanden werden."""
    mod = _modul()
    hard, _soft, gesehen = mod.check_text(
        "x.md", "- `core/compliance.py:69`\n", mod.build_index()
    )
    assert hard == [] and gesehen == 1


@pytest.mark.vc0
def test_anker_unter_github_werden_gefunden():
    """#2785: `.git` als Teilstring schloss `.github/` aus dem Index aus."""
    mod = _modul()
    assert "docs_lint.py" in mod.build_index()


@pytest.mark.vc0
def test_ein_anker_im_code_kommentar_wird_geprueft(tmp_path):
    mod = _modul()
    quelle = tmp_path / "modul.py"
    quelle.write_text(
        "x = 1  # siehe core/compliance.py:99999\ny = 'core/compliance.py:99999'\n",
        encoding="utf-8",
    )
    hard, _soft, _n = mod.check_code_comments(str(quelle), mod.build_index())
    assert len(hard) == 1 and ":1:" in hard[0], hard


@pytest.mark.vc0
def test_der_order_deckel_kommentar_traegt_keinen_widerrufenen_anker():
    """#2785 / #3219: Der Kommentar zum Order-Deckel nennt die interne Risikopolicy, nicht
    MiFID II Art. 57, und verweist auf eine Zeile, die den Wert tatsaechlich liest."""
    import re

    for datei in ("settings.py",):
        text = open(
            os.path.join(REPO, "ai_trading_bot", datei), encoding="utf-8"
        ).read()
        # die Definition, nicht eine fruehere Erwaehnung im Kommentar
        i = re.search(
            r"^\s*COMPLIANCE_MAX_ORDER_VALUE(: float)? = ", text, re.M
        ).start()
        block = text[max(0, i - 600) : i]
        kommentar = block[block.rfind("ADR-C01") :]
        assert "Art. 57" not in kommentar, f"{datei}: {kommentar}"
        for pfad, zeile in re.findall(r"(core/[\w/]+\.py):L?(\d+)", kommentar):
            ziel = (
                open(os.path.join(REPO, "ai_trading_bot", pfad), encoding="utf-8")
                .read()
                .splitlines()[int(zeile) - 1]
            )
            assert (
                "COMPLIANCE_MAX_ORDER_VALUE" in ziel or "max_order_value" in ziel
            ), f"{datei}: {pfad}:{zeile} liest den Deckel nicht: {ziel!r}"


@pytest.mark.vc0
def test_die_ratsche_des_gesamtbestands():
    """Repo-weiter Lauf (Doku + Code-Kommentare): die Zahl der harten Fehler darf nur
    sinken. Die Obergrenze steht eingecheckt in scripts/doc_anchor_obergrenze.txt."""
    r = subprocess.run(
        [sys.executable, SCRIPT, "--gesamt"], capture_output=True, text=True, cwd=REPO
    )
    assert r.returncode == 0, r.stdout[-3000:]


@pytest.mark.vc0
def test_autoritaetsansprueche_sind_belegt_oder_zurueckgenommen():
    """#2785: ein Dokument, das Autoritaet behauptet, belegt sie oder nimmt sie zurueck."""

    def lies(rel):
        return open(os.path.join(REPO, rel), encoding="utf-8").read()

    llms = lies("docs/llms.txt")
    assert (
        "This file is the Single Source of Truth" not in llms
    ), "docs/llms.txt behauptet SSoT, ohne dass ein Waechter seine Pfade prueft."
    baseline = lies("docs/0_strategy_and_roadmap/RELEASE_BASELINE.md")
    assert "authoritative snapshot of the system state" not in baseline
    assert "Historical baseline" in baseline
    ziel = lies("docs/1_architecture_and_adr/TARGET_ARCHITECTURE.md")
    abschnitt = ziel[ziel.index("## 8. Current State vs. Target State") :]
    assert "Last verified:" in abschnitt.split("\n## ")[0]


# ── #4435: Folge-Anker, Symbol neben dem Zeilenanker, Modulkopf ─────────────


@pytest.mark.vc0
def test_folge_anker_ausserhalb_der_datei_faellt():
    """`#L99999` ohne Dateinamen gehoert zum vollstaendigen Anker davor im Absatz.
    Vorher war er unsichtbar (ADR-021: `#L981` und `#L1845` bei 642 Zeilen)."""
    r = _run("- `core/compliance.py#L69` und weiter\n  bei `#L99999`\n")
    assert r.returncode == 1, r.stdout
    assert "core/compliance.py#L99999` out of range" in r.stdout, r.stdout


@pytest.mark.vc0
def test_folge_anker_im_bereich_besteht():
    mod = _modul()
    hard, _soft, gesehen = mod.check_text(
        "x.md", "- `core/compliance.py#L69` bis `#L70`\n", mod.build_index()
    )
    assert hard == [] and gesehen == 2, hard


@pytest.mark.vc0
def test_folge_anker_nach_leerzeile_wird_nicht_zugeordnet():
    r = _run("- `core/compliance.py#L69`\n\nbei `#L99999`\n")
    assert r.returncode == 0, r.stdout


@pytest.mark.vc0
def test_folge_anker_in_tabelle_nimmt_den_anker_derselben_zeile():
    """Eine Tabelle ist ein Absatz. Steht in der Zeile selbst ein vollstaendiger Anker,
    gilt dieser, nicht der aus einer Zeile darueber."""
    mod = _modul()
    idx = mod.build_index()
    kurz = mod.linecount(mod.resolve("core/engine/order_executor.py", idx))
    lang = mod.linecount(mod.resolve("settings.py", idx))
    assert kurz + 1 < lang
    text = (
        "| a | `core/engine/order_executor.py#L1` |\n"
        f"| b | `settings.py#L1` und `#L{kurz + 1}` |\n"
    )
    hard, _soft, gesehen = mod.check_text("x.md", text, idx)
    assert hard == [] and gesehen == 3, hard
    # Der Anker der eigenen Zeile gilt auch, wenn er hinter dem Folge-Anker steht.
    text = (
        "| a | `core/engine/order_executor.py#L1` |\n"
        f"| b | `#L{kurz + 1}` | `settings.py#L1` |\n"
    )
    hard, _soft, _ = mod.check_text("x.md", text, idx)
    assert hard == [], hard
    # Gegenprobe: ohne Anker in der eigenen Zeile gilt der des Absatzes.
    text = f"| a | `core/engine/order_executor.py#L1` |\n| b | `#L{kurz + 1}` |\n"
    hard, _soft, _ = mod.check_text("x.md", text, idx)
    assert len(hard) == 1 and "order_executor.py" in hard[0], hard


@pytest.mark.vc0
def test_folge_anker_im_code_kommentar_je_kommentarblock(tmp_path):
    mod = _modul()
    quelle = tmp_path / "modul.py"
    quelle.write_text(
        "# siehe core/compliance.py:69\n# und #L99999\nx = 1\n# allein #L99999\n",
        encoding="utf-8",
    )
    hard, _soft, _n = mod.check_code_comments(str(quelle), mod.build_index())
    assert len(hard) == 1 and ":2:" in hard[0], hard


@pytest.mark.vc0
def test_fehlendes_symbol_neben_zeilenanker_ist_hart():
    r = _run("- `gibt_es_nicht_xyz` (`core/compliance.py#L69`)\n")
    assert r.returncode == 1, r.stdout
    assert "gibt_es_nicht_xyz" in r.stdout, r.stdout


@pytest.mark.vc0
def test_moduldoc_nennt_symbolfehler_hart():
    """Der Modulkopf beschrieb das fehlende Symbol noch als WARNING ("not blocking") -
    seit #3636 ist es ein harter Fehler. Die Beschreibung erzeugte die Luecke in #4435.
    """
    doc = _modul().__doc__
    assert "WARNING" not in doc and "not blocking" not in doc, doc
    assert "Folge-Anker" in doc, doc


@pytest.mark.vc0
def test_fliesstext_bericht_endet_mit_null():
    """Lücke 3 wird nur gemessen: Der Bericht bricht nichts und zaehlt nicht in die
    Obergrenze, nennt aber seine Zahl."""
    r = subprocess.run(
        [sys.executable, SCRIPT, "--fliesstext"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert r.returncode == 0, r.stdout[-3000:]
    assert "fliesstext:" in r.stdout, r.stdout[-3000:]


@pytest.mark.vc0
def test_fliesstext_findet_undefinierten_bezeichner():
    mod = _modul()
    idx = mod.build_index()
    text = (
        "Der Wert `gibt_es_nicht_xyz` steht in `core/compliance.py`. "
        "Dagegen steht `check_order` in `core/compliance.py`.\n"
        "Ohne Datei: `gibt_es_nicht_xyz` allein.\n"
    )
    befunde = mod.fliesstext_befunde("x.md", text, idx)
    assert len(befunde) == 1, befunde
    assert "gibt_es_nicht_xyz" in befunde[0] and ":1:" in befunde[0], befunde
