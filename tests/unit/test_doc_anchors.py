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
