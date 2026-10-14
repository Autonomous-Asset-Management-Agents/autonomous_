"""#3899 — Aufzeichnung der pytest-Kollektion je CI-Job.

Der Bericht `scripts/bericht_kollektion.py` braucht je Job die Liste der eingesammelten
Tests. Statt eines zweiten Laufs mit `--collect-only` (die Backend-Jobs ziehen ein
PyTorch-Image) schreibt der Lauf selbst sie mit — nur wenn `KOLLEKTION_JOB` gesetzt ist.
"""

import importlib.util
from pathlib import Path

import pytest

from tests import kollektion_aufzeichnung as ka

# vc0: Plattform - CI-Berichtswesen, keine Stufe der Wertschoepfungskette.
pytestmark = [pytest.mark.unit, pytest.mark.vc0]


def _env(job, **extra):
    return {"KOLLEKTION_JOB": job, **extra}


def test_ohne_variable_wird_nichts_geschrieben(tmp_path):
    assert ka.aufzeichnen({}, tmp_path, tmp_path, ["tests/test_a.py::t"]) is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("job", ["../x", "a/b", "a\\b", ".versteckt", "mit leer"])
def test_jobname_ist_kein_pfad(tmp_path, job):
    assert ka.aufzeichnen(_env(job), tmp_path, tmp_path, ["x.py::t"]) is None
    assert list(tmp_path.iterdir()) == []


def test_schreibt_wurzel_und_node_ids(tmp_path):
    repo = tmp_path
    rootdir = repo / "ai_trading_bot"

    geschrieben = ka.aufzeichnen(
        _env("backend-iron-dome"),
        rootdir,
        repo,
        ["tests/unit/test_a.py::test_x", "tests/unit/test_b.py::T::t"],
    )

    assert geschrieben == repo / "kollektion" / "backend-iron-dome.txt"
    zeilen = geschrieben.read_text(encoding="utf-8").splitlines()
    assert zeilen[0] == "# wurzel: ai_trading_bot"
    assert zeilen[1:] == ["tests/unit/test_a.py::test_x", "tests/unit/test_b.py::T::t"]


def test_zweiter_lauf_im_selben_job_haengt_an(tmp_path):
    """backend-iron-dome ruft pytest zweimal (ci.yml:278, :289) — beide zaehlen."""
    ka.aufzeichnen(_env("j"), tmp_path, tmp_path, ["test_a.py::t"])
    pfad = ka.aufzeichnen(_env("j"), tmp_path, tmp_path, ["test_b.py::t"])
    text = pfad.read_text(encoding="utf-8")
    assert "test_a.py::t" in text and "test_b.py::t" in text


def test_nur_ein_xdist_arbeiter_schreibt(tmp_path):
    """Unter `-n 4` sammelt jeder Arbeiter dieselben Tests ein; einer genuegt."""
    for arbeiter in ("gw1", "gw2", "gw3"):
        env = _env("j", PYTEST_XDIST_WORKER=arbeiter)
        assert ka.aufzeichnen(env, tmp_path, tmp_path, ["test_a.py::t"]) is None
    assert not (tmp_path / "kollektion").exists()
    env = _env("j", PYTEST_XDIST_WORKER="gw0")
    assert ka.aufzeichnen(env, tmp_path, tmp_path, ["test_a.py::t"]) is not None


def test_rootdir_ausserhalb_des_repos_faellt_auf_punkt(tmp_path):
    repo = tmp_path / "repo"
    pfad = ka.aufzeichnen(_env("j"), Path("/anderswo"), repo, ["x.py::t"])
    assert pfad.read_text(encoding="utf-8").splitlines()[0] == "# wurzel: ."


def test_ausgabe_versteht_der_bericht(tmp_path):
    """Die Naht: Was hier geschrieben wird, liest `bericht_kollektion.lies_kollektion`."""
    repo = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "bericht_kollektion", repo / "scripts" / "bericht_kollektion.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise AssertionError("scripts/bericht_kollektion.py nicht ladbar")
    bk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bk)

    # Wurzel wie im echten Lauf: rootdir ai_trading_bot unter dem Repository.
    fake_repo = tmp_path
    pfad = ka.aufzeichnen(
        _env("j"),
        fake_repo / "ai_trading_bot",
        fake_repo,
        ["tests/unit/test_a.py::test_x"],
    )
    assert bk.lies_kollektion(pfad.read_text(encoding="utf-8")) == {
        "ai_trading_bot/tests/unit/test_a.py"
    }
