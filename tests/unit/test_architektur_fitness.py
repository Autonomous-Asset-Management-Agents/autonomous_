"""#3394 (ARC-E3) — Architekturregeln als Tests.

Zwei Arten von Tests:

* **Selbsttests der Detektoren** gegen konstruierte Quellen. Sie beweisen, dass eine Regel
  zaehlt und Datei und Zeile nennt, statt nur zu bestehen. Sie blockieren immer.
* **Die Regeln gegen den echten Code.** Jede Regel hat in ``tests/architecture/vertrag.toml``
  einen Modus: ``warnen`` meldet Befunde als Warnung und laesst den Pull Request mergefaehig,
  ``blockieren`` laesst ihn scheitern. Eine neue Regel beginnt mit ``warnen``; umgeschaltet
  wird je Regel in einem eigenen PR, wenn sie einen vollstaendigen Durchlauf ohne falschen
  Befund hinter sich hat (Plan #3394, Abschnitt 7.5).

Plan: ``docs/3394-import-linter-und-ast-fitness/implementation_plan.md``.
"""

from __future__ import annotations

import textwrap
import warnings

import pytest

from tests.architecture import regeln

pytestmark = pytest.mark.vc0


class ArchitekturWarnung(UserWarning):
    """Befund einer Regel im Modus ``warnen``."""


def _melde(regel: str, meldungen: list[str]) -> None:
    if not meldungen:
        return
    modus = regeln.lade_vertrag()["modus"][regel]
    text = f"[{regel}]\n" + "\n".join(meldungen)
    if modus == "blockieren":
        pytest.fail(text, pytrace=False)
    assert modus == "warnen", f"Unbekannter Modus {modus!r} fuer {regel}"
    warnings.warn(text, ArchitekturWarnung, stacklevel=2)


def _schreibe(wurzel, rel, quelle):
    pfad = wurzel / rel
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(textwrap.dedent(quelle), encoding="utf-8")


# ── Vertrag ───────────────────────────────────────────────────────────────────


def test_jede_regel_hat_einen_gueltigen_modus():
    modus = regeln.lade_vertrag()["modus"]
    assert set(modus) == {
        "ratsche",
        "stufengrenzen",
        "broker_aufrufer",
        "editionsweiche",
        "uhr_zugriffe",
        "getattr_namen",
    }
    assert set(modus.values()) <= {"warnen", "blockieren"}


def test_der_modus_warnen_laesst_den_lauf_bestehen_und_meldet(monkeypatch):
    monkeypatch.setattr(
        regeln, "lade_vertrag", lambda: {"modus": {"ratsche": "warnen"}}
    )
    with pytest.warns(ArchitekturWarnung, match="core/x.py:3"):
        _melde("ratsche", ["core/x.py:3: neu"])


def test_der_modus_blockieren_laesst_den_lauf_scheitern(monkeypatch):
    monkeypatch.setattr(
        regeln, "lade_vertrag", lambda: {"modus": {"ratsche": "blockieren"}}
    )
    with pytest.raises(pytest.fail.Exception, match="core/x.py:3"):
        _melde("ratsche", ["core/x.py:3: neu"])


# ── 1. Ratsche ────────────────────────────────────────────────────────────────


def _ratschen_baum(tmp_path, anzahl):
    zeilen = "\n".join(
        f'w{i} = getattr(config, "WERT_{chr(65 + i)}", {i})' for i in range(anzahl)
    )
    _schreibe(tmp_path, "core/modul.py", zeilen + "\n")


def test_ratsche_zaehlt_und_scheitert_ueber_der_obergrenze(tmp_path):
    _ratschen_baum(tmp_path, 3)
    vertrag = {"ratsche": {"obergrenzen": {"core": 2}}}
    (meldung,) = regeln.pruefe_ratsche(tmp_path, vertrag)
    assert "3 getattr" in meldung and "Obergrenze 2" in meldung
    assert "core/modul.py:3" in meldung, "Der Befund muss Datei und Zeile nennen."


def test_ratsche_verlangt_das_senken_der_obergrenze(tmp_path):
    _ratschen_baum(tmp_path, 1)
    vertrag = {"ratsche": {"obergrenzen": {"core": 2}}}
    (meldung,) = regeln.pruefe_ratsche(tmp_path, vertrag)
    assert "Senke die Obergrenze" in meldung and "auf 1" in meldung


def test_ratsche_besteht_genau_auf_der_obergrenze(tmp_path):
    _ratschen_baum(tmp_path, 2)
    vertrag = {"ratsche": {"obergrenzen": {"core": 2}}}
    assert regeln.pruefe_ratsche(tmp_path, vertrag) == []


def test_ratsche_folgt_der_zaehlmethode_des_plans(tmp_path):
    """``grep -v test`` verwirft Zeilen mit "test" im Pfad oder im Code; ein Default ohne
    Literal-Name (``getattr(config, name, 0)``) zaehlt nicht."""
    _schreibe(
        tmp_path,
        "core/modul.py",
        """\
        a = getattr(config, "ZAEHLT", 1)
        b = getattr(config, "MIT", latest)
        c = getattr(config, name, 0)
        d = getattr(self.config, "AUCH", None)
        e = getattr(settings, "NICHT", 1)
        """,
    )
    _schreibe(tmp_path, "core/tests_hilfe.py", 'x = getattr(config, "WEG", 1)\n')
    fund = regeln.ratschen_fundstellen(tmp_path, "core")
    assert [b.zeile for b in fund] == [1, 4]


def test_ratsche_gegen_den_code():
    _melde("ratsche", regeln.pruefe_ratsche(regeln.PAKET, regeln.lade_vertrag()))


# ── 2. Stufengrenzen ─────────────────────────────────────────────────────────


def test_stufengrenze_rueckwaerts_scheitert_und_nennt_die_grenze(tmp_path):
    _schreibe(tmp_path, "core/__init__.py", "")
    _schreibe(tmp_path, "core/round_table/__init__.py", "")
    _schreibe(
        tmp_path,
        "core/round_table/runner.py",
        "from core.gateway import order_gateway\n",
    )
    _schreibe(tmp_path, "core/gateway/__init__.py", "")
    _schreibe(tmp_path, "core/gateway/order_gateway.py", "")
    _schreibe(
        tmp_path,
        "vertrag.ini",
        """\
        [importlinter]
        root_package = core

        [importlinter:contract:probe]
        name = VC-1 Recherche erreicht die Ausfuehrung nicht
        type = forbidden
        source_modules = core.round_table
        forbidden_modules = core.gateway
        """,
    )
    code, ausgabe = regeln.pruefe_import_vertraege(tmp_path, tmp_path / "vertrag.ini")
    assert code != 0, ausgabe
    assert "VC-1 Recherche erreicht die Ausfuehrung nicht" in ausgabe
    assert "BROKEN" in ausgabe
    assert "core.round_table.runner -> core.gateway" in ausgabe, ausgabe


def test_stufengrenzen_gegen_den_code():
    code, ausgabe = regeln.pruefe_import_vertraege()
    # Fehlt das Werkzeug, darf die Regel nicht still als Warnung durchgehen.
    assert "No module named 'importlinter'" not in ausgabe, (
        "import-linter ist nicht installiert (requirements-ci.txt):\n" + ausgabe[-2000:]
    )
    # Ein gebrochener Vertrag und ein eingefrorener Import, den es nicht mehr gibt
    # ("No matches for ignored import"), sind beide Befunde der Regel.
    _melde("stufengrenzen", [] if code == 0 else [ausgabe[-6000:]])


def test_ein_geraeumter_eingefrorener_import_muss_gestrichen_werden(tmp_path):
    _schreibe(tmp_path, "core/__init__.py", "")
    _schreibe(tmp_path, "core/a.py", "")
    _schreibe(tmp_path, "core/b.py", "")
    _schreibe(
        tmp_path,
        "vertrag.ini",
        """\
        [importlinter]
        root_package = core

        [importlinter:contract:probe]
        name = a kennt b nicht
        type = forbidden
        source_modules = core.a
        forbidden_modules = core.b
        ignore_imports =
            core.a -> core.b
        """,
    )
    code, ausgabe = regeln.pruefe_import_vertraege(tmp_path, tmp_path / "vertrag.ini")
    assert code != 0 and "core.a -> core.b" in ausgabe, ausgabe


# ── 3. Genau ein Broker-Aufrufer ─────────────────────────────────────────────


def test_broker_aufrufer_gruen_mit_genau_einem_aufrufer(tmp_path):
    _schreibe(
        tmp_path,
        "core/gateway/order_gateway.py",
        "def senden(b, r):\n    return b.submit_order(r)\n",
    )
    _schreibe(tmp_path, "core/lesen.py", "def f(b):\n    return b.get_orders()\n")
    vertrag = {
        "broker_aufrufer": {
            "bereich": "core",
            "gateway": ["core/gateway/order_gateway.py"],
        }
    }
    assert regeln.pruefe_broker_aufrufer(tmp_path, vertrag) == []


def test_broker_aufruf_am_gateway_vorbei_nennt_datei_und_zeile(tmp_path):
    _schreibe(
        tmp_path,
        "core/gateway/order_gateway.py",
        "def senden(b, r):\n    return b.submit_order(r)\n",
    )
    _schreibe(
        tmp_path,
        "core/abkuerzung.py",
        """\
        import asyncio

        async def weg(client, oid):
            senden = client.submit_order
            await asyncio.to_thread(client.cancel_order_by_id, oid)
        """,
    )
    vertrag = {
        "broker_aufrufer": {
            "bereich": "core",
            "gateway": ["core/gateway/order_gateway.py"],
        }
    }
    meldungen = "\n".join(regeln.pruefe_broker_aufrufer(tmp_path, vertrag))
    assert "core/abkuerzung.py:4: submit_order" in meldungen, meldungen
    assert (
        "core/abkuerzung.py:5: cancel_order_by_id" in meldungen
    ), "Auch ein Methodenverweis ohne direkten Aufruf fuehrt zum Broker."


def test_eine_geraeumte_ausnahme_muss_gestrichen_werden(tmp_path):
    _schreibe(tmp_path, "core/sauber.py", "x = 1\n")
    vertrag = {
        "broker_aufrufer": {
            "bereich": "core",
            "gateway": [],
            "ausnahmen": {"core/sauber.py": {"submit_order": 1}},
        }
    }
    (meldung,) = regeln.pruefe_broker_aufrufer(tmp_path, vertrag)
    assert "nur noch 0" in meldung and "Streiche" in meldung


def test_broker_aufrufer_gegen_den_code():
    _melde(
        "broker_aufrufer",
        regeln.pruefe_broker_aufrufer(regeln.PAKET, regeln.lade_vertrag()),
    )


# ── 4. Keine Editionsweiche ausserhalb der Composition Root ──────────────────


def test_editionsweiche_nennt_datei_und_zeile(tmp_path):
    _schreibe(
        tmp_path,
        "core/weiche.py",
        '''\
        """Erwaehnt DEPLOYMENT_MODE nur im Docstring."""
        import os

        # DEPLOYMENT_MODE im Kommentar zaehlt nicht
        if os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL":
            pass
        lokal = os.environ["DEPLOYMENT_MODE"] == "LOCAL"
        modus = config.DEPLOYMENT_MODE
        ed = resolve_edition(schluessel)
        ''',
    )
    vertrag = {"editionsweiche": {"bereich": "core"}}
    meldungen = "\n".join(regeln.pruefe_editionsweichen(tmp_path, vertrag))
    for zeile in (5, 7, 8, 9):
        assert f"core/weiche.py:{zeile}:" in meldungen, meldungen
    assert "core/weiche.py:1:" not in meldungen
    assert "core/weiche.py:4:" not in meldungen


def test_die_composition_root_darf_die_edition_entscheiden(tmp_path):
    _schreibe(
        tmp_path,
        "core/zusammenbau.py",
        'import os\nmodus = os.getenv("DEPLOYMENT_MODE")\n',
    )
    vertrag = {
        "editionsweiche": {
            "bereich": "core",
            "composition_root": ["core/zusammenbau.py"],
        }
    }
    assert regeln.pruefe_editionsweichen(tmp_path, vertrag) == []


def test_editionsweiche_gegen_den_code():
    _melde(
        "editionsweiche",
        regeln.pruefe_editionsweichen(regeln.PAKET, regeln.lade_vertrag()),
    )


def test_uhr_zugriffe_gegen_den_code():
    from tests.architecture.regeln import HIER, lade_vertrag, pruefe_uhr_zugriffe

    vertrag = lade_vertrag()
    modus = vertrag["modus"].get("uhr_zugriffe", "warnen")
    fehler = pruefe_uhr_zugriffe(HIER.parent.parent, vertrag)
    if not fehler:
        return
    meldung = "\n".join(fehler)
    if modus == "blockieren":
        pytest.fail(meldung)
    else:
        import warnings

        warnings.warn(meldung, stacklevel=2)


# ── 6. getattr-Namen gegen das Schema (#3627) ───────────────────────
#
# Die Ratsche oben zaehlt die Umgehungen, sie prueft sie nicht. Gemessen am 25.09.2026
# nennen acht Fundstellen einen Namen, den weder RuntimeConfigState noch die Modulebene
# von settings.py kennt — darunter USE_CASH_ONLY an drei Stellen des Sizing-Pfads. Fuer
# sie ist der Rueckfallwert die EINZIGE Quelle: nicht ueber die Umgebung setzbar, in
# keinem erzeugten Register, und ein Tippfehler im Namen faellt nie auf.


def test_getattr_name_findet_den_tippfehler_und_sieht_auch_cfg(tmp_path):
    """Selbsttest: Der Detektor nennt Datei und Zeile — und verfehlt kein ``cfg``."""
    _schreibe(
        tmp_path,
        "settings.py",
        """
        class RuntimeConfigState(BaseSettings):
            ECHTES_FELD: bool = True

        MODULNAME = 3
        """,
    )
    _schreibe(
        tmp_path,
        "core/modul.py",
        """
        a = getattr(config, "ECHTES_FELD", False)
        b = getattr(config, "MODULNAME", 0)
        c = getattr(config, "ECHTES_FELT", False)
        d = getattr(_cfg, "NUR_IM_FALLBACK", 1)
        e = getattr(get_config(), "AUCH_NUR_DA", 1)
        f = getattr(irgendwas, "FREMDES_ATTRIBUT", 1)
        """,
    )
    unbekannt = regeln.unbekannte_konfigurationsnamen(tmp_path, "core")
    assert [(b.zeile, b.was.split()[0]) for b in unbekannt] == [
        (4, "ECHTES_FELT"),
        (5, "NUR_IM_FALLBACK"),
        (6, "AUCH_NUR_DA"),
    ], unbekannt


def test_ein_kommentar_ueber_die_form_ist_keine_fundstelle(tmp_path):
    """Sonst meldet die Regel jeden Text, der sie erklaert — auch ihren eigenen."""
    _schreibe(
        tmp_path,
        "settings.py",
        """
        class RuntimeConfigState(BaseSettings):
            A: int = 1
        """,
    )
    _schreibe(
        tmp_path,
        "core/modul.py",
        """
        # So nicht: getattr(config, "ERFUNDEN", 1)
        x = 1  # auch hier nicht: getattr(cfg, "ERFUNDEN", 1)
        y = getattr(config, "ERFUNDEN", 1)
        """,
    )
    unbekannt = regeln.unbekannte_konfigurationsnamen(tmp_path, "core")
    assert [b.zeile for b in unbekannt] == [4], unbekannt


def test_testdateien_duerfen_erfundene_namen_abfragen(tmp_path):
    _schreibe(
        tmp_path,
        "settings.py",
        """
        class RuntimeConfigState(BaseSettings):
            A: int = 1
        """,
    )
    _schreibe(
        tmp_path,
        "core/tests/test_x.py",
        """
        x = getattr(config, "ERFUNDEN", 1)
        """,
    )
    assert regeln.unbekannte_konfigurationsnamen(tmp_path, "core") == []


def test_getattr_namen_im_echten_code():
    _melde(
        "getattr_namen",
        regeln.pruefe_getattr_namen(regeln.PAKET, regeln.lade_vertrag()),
    )
