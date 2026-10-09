"""#3395 (ARC-E3) — Pfad-gebundene Regeln aus einer Quelle.

Plan: ``docs/3395-pfad-gebundene-regeln/implementation_plan.md``.
Generator: ``scripts/gen_pfad_regeln.py``; Quelle: ``docs/5_engineering_and_devops/pfad_regeln/``.
"""

from __future__ import annotations

import importlib.util
import sys
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "gen_pfad_regeln", REPO / "scripts" / "gen_pfad_regeln.py"
)
gen = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gen  # dataclasses brauchen das Modul in sys.modules
_spec.loader.exec_module(gen)

# Plan 7.4: der Abgleich gegen den eingecheckten Stand beginnt als Warnung und wird in
# einem eigenen PR blockierend, nachdem er einen Durchlauf ohne falschen Befund hatte.
ABGLEICH_MODUS = "warnen"

REGELN = ("datenbank", "kapitalschutz", "langgraph", "tests")
ERWARTET = {
    pfad
    for n in REGELN
    for pfad in (
        f".claude/rules/{n}.md",
        f".cursor/rules/{n}.mdc",
        f".github/instructions/{n}.instructions.md",
    )
}


def _quelle(tmp_path, muster="WERT = ([0-9]+)", code="WERT = 7\n"):
    (tmp_path / "code.py").write_text(code, encoding="utf-8")
    quellen = tmp_path / "quellen"
    quellen.mkdir()
    (quellen / "probe.toml").write_text(
        f"""
name = "probe"
titel = "Probe"
beschreibung = "Probe"
pfade = ["a/b.py", "c/**"]
regeln = "- eine Regel"
flags_hinweis = "keine"

[[deckel]]
adr = "ADR-X1"
name = "Deckel"
basis = "Test"
quellen = [{{ datei = "code.py", muster = '{muster}' }}]
""",
        encoding="utf-8",
    )
    return quellen


def test_eine_quelle_ergibt_eine_regel_je_werkzeug(tmp_path):
    ausgaben = gen.erzeuge(tmp_path, _quelle(tmp_path))
    assert set(ausgaben) == {
        ".claude/rules/probe.md",
        ".cursor/rules/probe.mdc",
        ".github/instructions/probe.instructions.md",
    }
    koerper = {inhalt.split("\n\n", 1)[1] for inhalt in ausgaben.values()}
    assert len(koerper) == 1, "Alle Werkzeuge bekommen denselben Inhalt."


def test_jedes_werkzeug_bindet_die_regel_an_die_pfade(tmp_path):
    a = gen.erzeuge(tmp_path, _quelle(tmp_path))
    assert '  - "a/b.py"\n  - "c/**"' in a[".claude/rules/probe.md"]
    assert "globs: a/b.py,c/**\nalwaysApply: false" in a[".cursor/rules/probe.mdc"]
    assert 'applyTo: "a/b.py,c/**"' in a[".github/instructions/probe.instructions.md"]


def test_der_deckelwert_kommt_aus_dem_code(tmp_path):
    a = gen.erzeuge(tmp_path, _quelle(tmp_path, code="WERT = 42\n"))
    assert (
        "| ADR-X1 | Deckel | `42` | `code.py` | Test |" in a[".claude/rules/probe.md"]
    )


def test_ein_anker_ins_leere_bricht_ab(tmp_path):
    with pytest.raises(gen.AnkerFehlt, match="findet nichts"):
        gen.erzeuge(tmp_path, _quelle(tmp_path, code="ANDERS = 1\n"))


def test_der_generator_ist_deterministisch_und_ohne_zeitstempel(tmp_path):
    quellen = _quelle(tmp_path)
    assert gen.erzeuge(tmp_path, quellen) == gen.erzeuge(tmp_path, quellen)
    text = "".join(gen.erzeuge().values())
    assert gen.erzeuge() == gen.erzeuge()
    for zeichen in ("2026-", "UTC", "generated at", "erzeugt am"):
        assert zeichen not in text


def test_eine_handaenderung_faellt_im_abgleich_auf(tmp_path):
    ausgaben = gen.erzeuge(tmp_path, _quelle(tmp_path))
    for rel, inhalt in ausgaben.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(inhalt, encoding="utf-8", newline="\n")
    assert gen.abweichungen(tmp_path, ausgaben) == []

    ziel = tmp_path / ".cursor/rules/probe.mdc"
    ziel.write_text(ziel.read_text(encoding="utf-8") + "von Hand\n", encoding="utf-8")
    assert gen.abweichungen(tmp_path, ausgaben) == [".cursor/rules/probe.mdc"]


def test_crlf_im_checkout_ist_keine_handaenderung(tmp_path):
    ausgaben = gen.erzeuge(tmp_path, _quelle(tmp_path))
    for rel, inhalt in ausgaben.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(inhalt.replace("\n", "\r\n").encode("utf-8"))
    assert gen.abweichungen(tmp_path, ausgaben) == []


def test_die_kapitalschutz_regel_nennt_den_order_deckel_als_interne_policy():
    inhalt = gen.erzeuge()[".claude/rules/kapitalschutz.md"]
    assert "ADR-C01" in inhalt and "interne Risikopolicy" in inhalt
    assert "Art. 57" not in inhalt and "Art.57" not in inhalt


def test_die_kapitalschutz_regel_deckt_den_order_pfad():
    inhalt = gen.erzeuge()[".claude/rules/kapitalschutz.md"]
    for pfad in (
        "ai_trading_bot/core/compliance.py",
        "ai_trading_bot/core/risk_manager.py",
        "ai_trading_bot/core/engine/order_executor.py",
        "ai_trading_bot/core/gateway/**",
    ):
        assert f'"{pfad}"' in inhalt


def test_die_eingecheckten_regeln_sind_aktuell():
    ausgaben = gen.erzeuge()
    assert set(ausgaben) == ERWARTET
    fehler = gen.abweichungen(REPO, ausgaben)
    if not fehler:
        return
    text = (
        "Pfad-Regeln nicht aktuell oder von Hand geaendert — "
        "`python scripts/gen_pfad_regeln.py` laufen lassen: " + ", ".join(fehler)
    )
    if ABGLEICH_MODUS == "blockieren":
        pytest.fail(text, pytrace=False)
    warnings.warn(text, UserWarning, stacklevel=1)


# ── Plan Schritt 5: projektweite Datei um das kuerzen, was jetzt pfadgebunden ist ──
#
# CLAUDE.md:3 setzt sich "Keep it under 150 lines" und stand bei 248. Schritt 5 nimmt
# heraus, was nur fuer bestimmte Pfade gilt; der Rest ist projektweit und bleibt.
# Die Grenze ist eine Ratsche: sie darf nur sinken. Ziel bleibt die selbstgesetzte 150.

CLAUDE_MD_ZIEL = 150
CLAUDE_MD_OBERGRENZE = 204

# (Merkmal, das nur noch in der pfadgebundenen Regel steht, Regelname, ein Pfad, den sie
# abdecken muss)
VERSCHOBEN = (
    (
        "RedisSaver.from_conn_string",
        "langgraph",
        "ai_trading_bot/core/orchestration/**",
    ),
    ("TypedDict", "langgraph", "ai_trading_bot/core/orchestration/**"),
    ("docker-compose.migrate.yml", "datenbank", "ai_trading_bot/alembic/**"),
    ("create_all()", "datenbank", "ai_trading_bot/core/database/**"),
    ("AsyncMock(side_effect", "tests", "ai_trading_bot/tests/**"),
    ("ADR-C01: Max Order Value", "kapitalschutz", "ai_trading_bot/core/compliance.py"),
)


def _claude_md() -> str:
    return (REPO / "CLAUDE.md").read_text(encoding="utf-8")


def test_claude_md_haelt_die_ratsche():
    zeilen = len(_claude_md().splitlines())
    assert zeilen <= CLAUDE_MD_OBERGRENZE, (
        f"CLAUDE.md hat {zeilen} Zeilen, Obergrenze {CLAUDE_MD_OBERGRENZE} "
        f"(Ziel {CLAUDE_MD_ZIEL}, CLAUDE.md:3). Pfadgebundenes gehoert nach "
        "docs/5_engineering_and_devops/pfad_regeln/."
    )


@pytest.mark.parametrize("merkmal,regel,pfad", VERSCHOBEN)
def test_pfadgebundenes_steht_nur_in_der_pfad_regel(merkmal, regel, pfad):
    assert merkmal not in _claude_md(), f"{merkmal!r} steht noch in CLAUDE.md"
    inhalt = gen.erzeuge()[f".claude/rules/{regel}.md"]
    assert merkmal in inhalt, f"{merkmal!r} fehlt in der Regel {regel!r}"
    assert f'"{pfad}"' in inhalt, f"Regel {regel!r} deckt {pfad} nicht ab"


def test_claude_md_zeigt_auf_die_pfad_regeln():
    """Werkzeuge ohne pfadgebundene Ausgabe (Antigravity, Plan §8) muessen die Regeln
    trotzdem finden: CLAUDE.md nennt jede Quelle."""
    text = _claude_md()
    for toml in sorted(gen.QUELLEN.glob("*.toml")):
        assert toml.relative_to(REPO).as_posix() in text, toml.name


def test_eine_regel_ohne_deckel_hat_keine_leere_deckel_tabelle(tmp_path):
    quellen = tmp_path / "quellen"
    quellen.mkdir()
    (quellen / "ohne.toml").write_text(
        'name = "ohne"\ntitel = "Ohne"\nbeschreibung = "x"\npfade = ["a/**"]\n'
        'regeln = "- eine Regel"\n',
        encoding="utf-8",
    )
    inhalt = gen.erzeuge(repo=tmp_path, quellen=quellen)[".claude/rules/ohne.md"]
    assert "## Deckel" not in inhalt and "## Flag-Werte" not in inhalt
    assert "- eine Regel" in inhalt
