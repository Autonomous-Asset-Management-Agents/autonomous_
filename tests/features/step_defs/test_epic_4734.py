"""Abnahme auf Epic-Ebene für #4734 — scharf geschaltet (#4746).

Die Platzhalter des Makers sind durch echte Prüfungen ersetzt, der `xfail`-Marker ist
gefallen. Jeder Schritt ruft die Module, die die Sub-Issues gebaut haben (Option A des
freigegebenen Plans): `workflow_invarianten`, `vorlagen_beweis`, `impl_apply`,
`governance_beweis`.

**Was ersetzt ist und was nicht.** Ersetzt ist allein **Git** — durch eingefrorene
Dateiinhalte. Die geprüfte Logik läuft echt: `wende_an`, `beweise`, `workflow_freigabe`
und `workflow_invarianten.main` entscheiden hier wirklich, nicht ein Stub.

Zwei Gründe, gemessen und nicht vermutet:

1. `Backend BDD Tests` checkt mit `actions/checkout@v7` ohne `fetch-depth` aus, also mit
   Tiefe 1. Weder `71928a4545c8a~1` noch `origin/main` sind dort vorhanden; ein
   `git fetch` im Test machte die Abnahme vom Netz abhängig.
2. Eine `form: jobs`-Vorlage mit `entfernt` ist **einmalig** anwendbar. Nach der
   Übernahme (#4760) fehlen die zu entfernenden Jobs in `origin/main`, und `wende_an`
   bricht ab: Die Messung gegen eine wandernde Revision fiel von 7 auf 5 von 7. Eine
   Abnahme, die sich durch ihr eigenes Eintreten widerlegt, taugt nicht als Zusage —
   deshalb liegt der Stand, für den die Vorlagen freigegeben wurden, als Fixture im
   Repo (`scripts/tests/fixtures/ci_vor_4760.yml`).

Dieselbe Technik benutzt `scripts/tests/test_workflow_invarianten.py` für seine
Unit-Tests (`_laeufer`, `_echt`); hier steht sie noch einmal, damit die Abnahme ohne
Blick in eine Testdatei lesbar ist.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest
from pytest_bdd import given, scenarios, then, when

WURZEL = pathlib.Path(__file__).resolve().parents[4]
_SKRIPTE = str(WURZEL / "scripts")
if _SKRIPTE not in sys.path:
    sys.path.insert(0, _SKRIPTE)

import governance_beweis as gb  # noqa: E402
import impl_apply as ia  # noqa: E402
import vorlagen_beweis as vb  # noqa: E402
import workflow_invarianten as wi  # noqa: E402

scenarios("../epic_4734.feature")

WORKFLOWS = ".github/workflows/"
PFLICHT = ".github/pflicht_checks.yml"
SIZE = WORKFLOWS + "pr-size-enforcer.yml"
CI = WORKFLOWS + "ci.yml"
GUARD = WORKFLOWS + "governance-guard.yml"

#: Der eingefrorene Stand von `ci.yml`, für den #4697, #4699 und #4701 freigegeben
#: wurden. Siehe Modul-Docstring, Grund 2.
CI_FIXTURE = WURZEL / "scripts" / "tests" / "fixtures" / "ci_vor_4760.yml"

# Wortgleich aus `git show 71928a4545c8a~1:.github/workflows/pr-size-enforcer.yml`
# (Zeilen 16-21); die Zielfassung unterscheidet sich nur in der Konstante.
_SIZE_KOPF = """      - name: Check PR Size
        uses: actions/github-script@v7
        with:
          script: |
            const threshold = %d; // Max allowed changed lines
            const pr = context.payload.pull_request;
"""

#: Die fünf Sub-Issues von #4691 und ihre Vorlagendateien — die Zweck-Kennzahl zählt
#: Sub-Issues, nicht Dateien: #4764 bringt vier Dateien in einem Vertrag (#4705 abgelöst).
VORLAGEN = {
    "4697": ("4697-*/docs-job.yml",),
    "4699": ("4699-*/ci-jobs.yml",),
    "4701": ("4701-*/ci-jobs.yml",),
    "4703": ("4703-*/oss-ci.yml",),
    "4764": (
        "4764-*/cia-gate.yml",
        "4764-*/governance-guard.yml",
        "4764-*/plan-gate.yml",
        "4764-*/tor-neulauf.yml",
    ),
}


@pytest.fixture
def kontext() -> dict:
    """Was ein Schritt messt, liest der nächste — ohne globalen Zustand."""
    return {}


def _vorlage(muster: str) -> pathlib.Path:
    treffer = sorted((WURZEL / "docs").glob(muster))
    assert len(treffer) == 1, f"{muster}: {len(treffer)} Treffer, genau einer erwartet"
    return treffer[0]


def _lies(pfad: pathlib.Path) -> str:
    return pfad.read_text(encoding="utf-8")


def _echt() -> dict[str, str]:
    """Der heutige Bestand: alle Workflows und die Zusage-Datei."""
    dateien = sorted((WURZEL / WORKFLOWS).iterdir()) + [WURZEL / PFLICHT]
    return {p.relative_to(WURZEL).as_posix(): _lies(p) for p in dateien if p.is_file()}


def _laeufer(revisionen: dict[str, dict[str, str]]):
    """Ein `subprocess.run`-Ersatz, der `git ls-tree` und `git show` aus Daten bedient."""

    def lauf(cmd, **_kw):
        if cmd[:2] == ["git", "ls-tree"]:
            rev, praefix = cmd[4], cmd[-1]
            if rev not in revisionen:
                return subprocess.CompletedProcess(
                    cmd, 128, "", "fatal: Not a valid object name"
                )
            namen = [n for n in revisionen[rev] if n.startswith(praefix)]
            return subprocess.CompletedProcess(cmd, 0, "\n".join(namen) + "\n", "")
        if cmd[:2] == ["git", "show"]:
            rev, pfad = cmd[2].split(":", 1)
            inhalt = revisionen.get(rev, {}).get(pfad)
            if inhalt is None:
                return subprocess.CompletedProcess(
                    cmd, 128, "", "fatal: path does not exist"
                )
            return subprocess.CompletedProcess(cmd, 0, inhalt, "")
        raise AssertionError("unerwarteter Aufruf: %r" % (cmd,))

    return lauf


def _basis_text(ziel: str) -> str:
    """Der Stand, für den die Vorlage freigegeben wurde."""
    return _lies(CI_FIXTURE) if ziel == CI else _lies(WURZEL / ziel)


class _Lauf:
    """Nimmt Kommandos entgegen, statt sie auszuführen — zählt `gh pr create` mit."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, cwd=None, zeitgrenze=None):
        self.calls.append(list(args))
        return ""

    def anzahl(self, teil: str) -> int:
        return sum(1 for a in self.calls if teil in " ".join(a))


def _freigabe(
    vorlage: pathlib.Path, ziel: str, ergebnis: str, lauf: _Lauf
) -> list[str]:
    """`impl_apply.workflow_freigabe` mit echten Werkzeugen, nur ohne Git.

    `beweis` und `invarianten` sind nicht gestellt: Sie rufen `vorlagen_beweis.beweise`
    und `workflow_invarianten.main` wirklich auf: nur die Revisionen kommen aus
    eingefrorenen Daten statt aus git.
    """
    vorlage_rel = vorlage.relative_to(WURZEL).as_posix()
    basis = _basis_text(ziel)

    def beweis(v, z, basis_rev, kopf_rev, repo="."):
        return vb.beweise(_lies(vorlage), basis, ergebnis, name=vorlage.name)

    def invarianten(argv):
        bestand = _echt()
        return wi.main(
            argv,
            lauf=_laeufer(
                {argv[1]: bestand, argv[3]: {**bestand, ziel: ergebnis}},
            ),
        )

    return ia.workflow_freigabe(
        [ziel],
        [(vorlage_rel, ziel)],
        lauf,
        str(WURZEL),
        probe="c47460000000000000000000000000000000000b",
        beweis=beweis,
        invarianten=invarianten,
    )


# ── 1-2: der Bestand, gegen den gemessen wird ────────────────────────────────────────


@given(
    "origin/main mit den Workflow-Vorlagen aus #4697, #4699, #4701, #4703 und #4764 unter docs/<nr>-<slug>/"
)
def schritt_1(kontext):
    gefunden = {}
    for nr, muster in VORLAGEN.items():
        for m in muster:
            pfad = _vorlage(m)
            assert (
                pfad.parent / "implementation_plan.md"
            ).is_file(), f"{pfad}: kein implementation_plan.md daneben"
            vertrag = vb.vertrag(_lies(pfad), pfad.name)
            assert vertrag.ziel.startswith(WORKFLOWS), vertrag.ziel
            gefunden.setdefault(nr, []).append((pfad, vertrag))
    assert len(gefunden) == 5, sorted(gefunden)
    assert sum(len(v) for v in gefunden.values()) == 8
    kontext["vorlagen"] = gefunden


@given("der Commit 71928a4545c8a (pr-size-enforcer.yml: threshold 1000 → 10000)")
def schritt_2(kontext):
    bestand = _echt()
    assert SIZE in bestand, f"{SIZE} fehlt im Bestand"
    kontext["historisch"] = {
        "71928a4545c8a~1": {**bestand, SIZE: _SIZE_KOPF % 1000},
        "71928a4545c8a": {**bestand, SIZE: _SIZE_KOPF % 10000},
    }


# ── 3-6: das Invariantentor ──────────────────────────────────────────────────────────


@when(
    "`python scripts/workflow_invarianten.py --basis 71928a4545c8a~1 --ziel 71928a4545c8a` läuft"
)
def schritt_3(kontext, capsys):
    code = wi.main(
        ["--basis", "71928a4545c8a~1", "--ziel", "71928a4545c8a"],
        lauf=_laeufer(kontext["historisch"]),
    )
    kontext["historisch_ausgang"] = (code, capsys.readouterr().out)


@then(
    "endet es mit Exit 1 und einem Befund, der pr-size-enforcer.yml und 1000 → 10000 nennt"
)
def schritt_4(kontext):
    code, ausgabe = kontext["historisch_ausgang"]
    assert code == 1, f"Exit {code}, erwartet 1 — das Tor hat die Lockerung verpasst"
    assert "pr-size-enforcer.yml" in ausgabe
    assert "1000 → 10000" in ausgabe


@when(
    "`python scripts/workflow_invarianten.py --basis origin/main --ziel origin/main` läuft"
)
def schritt_5(kontext, capsys):
    bestand = _echt()
    code = wi.main(
        ["--basis", "origin/main", "--ziel", "origin/main"],
        lauf=_laeufer({"origin/main": bestand}),
    )
    kontext["bestand_ausgang"] = (code, capsys.readouterr().out)


@then("endet es mit Exit 0 und ohne Befund")
def schritt_6(kontext):
    code, ausgabe = kontext["bestand_ausgang"]
    assert (
        code == 0
    ), f"Exit {code} gegen sich selbst — das Tor meldet einen Phantom-Befund"
    assert "BEFUND" not in ausgabe
    assert "nichts aufgeweicht" in ausgabe


# ── 7-9: die Zweck-Kennzahl ──────────────────────────────────────────────────────────


@when(
    "jede der fünf Vorlagen einzeln mit `vorlagen_beweis.wende_an` auf origin/main angewandt wird"
)
def schritt_7(kontext):
    angewandt = {}
    for nr, paare in kontext["vorlagen"].items():
        for pfad, vertrag in paare:
            basis = _basis_text(vertrag.ziel)
            ergebnis = vb.wende_an(_lies(pfad), basis, name=pfad.name)
            assert (
                vb.beweise(_lies(pfad), basis, ergebnis, name=pfad.name) == []
            ), f"{pfad.name}: angewandt und doch nicht beweisbar"
            angewandt.setdefault(nr, []).append((pfad, vertrag.ziel, ergebnis))
    kontext["angewandt"] = angewandt


@when(
    "impl_apply über den so entstandenen Diff entscheidet (Vertragszeile `Vorlage-Beweis:` gesetzt)"
)
def schritt_8(kontext):
    urteile = {}
    for nr, eintraege in kontext["angewandt"].items():
        urteile[nr] = [
            _freigabe(pfad, ziel, ergebnis, _Lauf())
            for pfad, ziel, ergebnis in eintraege
        ]
    kontext["urteile"] = urteile


@then(
    "lässt impl_apply für 5 von 5 Vorlagen einen PR zu (Zweck-Kennzahl, heute 0 von 5)"
)
def schritt_9(kontext):
    zulaessig = [nr for nr, liste in kontext["urteile"].items() if not any(liste)]
    verboten = {
        nr: [r for r in liste if r]
        for nr, liste in kontext["urteile"].items()
        if any(liste)
    }
    assert verboten == {}, f"noch verboten: {verboten}"
    assert len(zulaessig) == 5, sorted(zulaessig)


# ── 10-11: die freihändige Änderung bleibt verboten ──────────────────────────────────


@when("eine der Vorlagen angewandt und zusätzlich eine Zeile frei geändert wird")
def schritt_10(kontext):
    pfad, ziel, ergebnis = kontext["angewandt"]["4764"][-1]
    zeilen = ergebnis.splitlines()
    stelle = next(
        i for i, z in enumerate(zeilen) if z.strip().startswith("timeout-minutes:")
    )
    zeilen[stelle] = zeilen[stelle].split(":")[0] + ": 360"
    kontext["frei"] = (pfad, ziel, "\n".join(zeilen) + "\n")


@then("bricht impl_apply mit Grund `governance` ab, und es entsteht kein PR")
def schritt_11(kontext):
    pfad, ziel, frei = kontext["frei"]
    lauf = _Lauf()
    rest = _freigabe(pfad, ziel, frei, lauf)
    assert rest, "eine freihändige Zeile wurde als Übernahme durchgelassen"
    assert any(ziel in r for r in rest), rest
    assert lauf.anzahl("gh pr create") == 0


# ── 12: beide Fassungen rufen den Beweis ─────────────────────────────────────────────


@then(
    "governance-guard.yml sowie die Vorlage docs/4764-…/governance-guard.yml rufen den Beweis in der Fassung des Basis-Branches auf"
)
def schritt_12():
    installiert = _lies(WURZEL / GUARD)
    vorlage = _lies(_vorlage("4764-*/governance-guard.yml"))
    for name, text in (("installiert", installiert), ("Vorlage", vorlage)):
        assert gb.ENTSCHEIDER in text, f"{name}: ruft {gb.ENTSCHEIDER} nicht"
        assert (
            'git checkout "$BASIS" -- scripts/' in text
        ), f"{name}: kein Basis-Checkout"
        assert (
            'git checkout "$KOPF" -- scripts/' in text
        ), f"{name}: gibt den Baum nicht zurück"
