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
from datetime import date, timedelta

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
        "getattr_widersprueche",
        "schatten_importe",
        "groessen",
        "doku_groessen",
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


# ── 7. Rueckfallwert widerspricht dem Schema (#3627, Schritt 2) ───────────────
#
# Regel 6 prueft, ob der NAME aufloest. Diese prueft, ob der WERT dasselbe sagt.
# `getattr(cfg, "DECISION_CAPTURE_ENABLED", False)` neben `... = True` im Schema sind
# zwei Antworten auf dieselbe Frage: Heute greift der Rueckfallwert nie, aber wer den
# Code liest, muss raten — und faellt die Deklaration weg, kippt das Verhalten lautlos.
# Gemessen am 25.09.2026: 76 Paare, darunter COMPLIANCE_ALLOW_RISK_REDUCING_EXITS
# (Schema an, Rueckfall aus) und DEFAULT_EQUITY (Schema 100.000, Rueckfall 0).


def test_ein_abweichender_rueckfallwert_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        "settings.py",
        """
        class RuntimeConfigState(BaseSettings):
            GLEICH: bool = True
            ANDERS: bool = True
            ZAHL: float = 5400
        """,
    )
    _schreibe(
        tmp_path,
        "core/modul.py",
        """
        a = getattr(config, "GLEICH", True)
        b = getattr(config, "ANDERS", False)
        c = getattr(config, "ZAHL", 5400.0)
        d = getattr(config, "ANDERS", berechne())
        """,
    )
    befunde = regeln.widerspruch_fundstellen(tmp_path, "core")
    # Nur `ANDERS` in Zeile 3: gleiche Werte zaehlen nicht, 5400 == 5400.0 auch nicht,
    # und ein Rueckfallwert, der kein Literal ist, wird nicht geraten.
    assert [(b.zeile, b.was) for b in befunde] == [(3, "ANDERS")], befunde


def test_ein_getenv_default_gilt_als_auslieferungswert(tmp_path):
    """Die meisten Felder stehen als `os.getenv(...)`-Ausdruck da — sonst saehe die
    Regel fast nichts."""
    _schreibe(
        tmp_path,
        "settings.py",
        """
        class RuntimeConfigState(BaseSettings):
            SCHALTER: bool = os.getenv("SCHALTER", "True").lower() == "true"
            TAKT: float = float(os.getenv("TAKT", "30"))
        """,
    )
    _schreibe(
        tmp_path,
        "core/modul.py",
        """
        a = getattr(config, "SCHALTER", False)
        b = getattr(config, "TAKT", 30)
        """,
    )
    befunde = regeln.widerspruch_fundstellen(tmp_path, "core")
    assert [(b.zeile, b.was) for b in befunde] == [(2, "SCHALTER")], befunde


def test_getattr_widersprueche_im_echten_code():
    _melde(
        "getattr_widersprueche",
        regeln.pruefe_getattr_widersprueche(regeln.PAKET, regeln.lade_vertrag()),
    )


# ── 8. Keine zweig-lokalen Schatten-Importe ─────────────────────────────────


def test_schatten_importe_verschachtelung(tmp_path):
    _schreibe(
        tmp_path,
        "core/schatten.py",
        """\
import os
def aussen():
    import os  # Befund 1 (unmittelbar)
    def innen():
        import os  # Befund 2 (unmittelbar in innen)
        """,
    )
    vertrag = {"schatten_importe": {"bereiche": ["core"]}}
    meldungen = "\n".join(regeln.pruefe_schatten_importe(tmp_path, vertrag))
    # #3740: Der Schluessel ist der blosse Name ("os"), die Funktion steht als Zusatz
    # daneben — sonst findet die Ratsche ihre eingefrorenen Ausnahmen nicht wieder.
    assert "Schatten-Import in 'aussen'" in meldungen
    assert "Schatten-Import in 'innen'" in meldungen
    assert "2× os" in meldungen, meldungen


def test_schatten_importe_gegen_den_code():
    _melde(
        "schatten_importe",
        regeln.pruefe_schatten_importe(regeln.PAKET, regeln.lade_vertrag()),
    )


# ── 9. Groessen: Datei- und Funktionslaengen (#3817, ARC-E6 G-0) ─────────────
#
# Zaehlweise wie im Epic-Plan (#3738, Abschnitt 3) festgeschrieben: physische Zeilen
# (``splitlines``), Funktionslaenge ``end_lineno - lineno + 1`` ueber ``ast``. Die
# Schwellen hier sind klein gewaehlt, damit die Probequellen lesbar bleiben; die
# echten Schwellen stehen im Vertrag und werden von
# ``test_groessen_gegen_den_code`` geprueft.


def _groessen_vertrag(datei_schwelle=10, funktion_schwelle=5, **teil):
    return {
        "groessen": {
            "bereiche": ["core"],
            "datei_schwelle": datei_schwelle,
            "funktion_schwelle": funktion_schwelle,
            **teil,
        }
    }


def test_groesse_meldet_datei_ueber_der_schwelle(tmp_path):
    _schreibe(tmp_path, "core/lang.py", "x = 1\n" * 12)
    (meldung,) = regeln.pruefe_groessen(tmp_path, _groessen_vertrag())
    assert "core/lang.py" in meldung
    assert "12 Zeilen" in meldung, "Der Befund muss die gemessene Zahl nennen."
    assert (
        "800" not in meldung
    )  # die Schwelle kommt aus dem Vertrag, nicht fest verdrahtet


def test_groesse_schweigt_unter_der_schwelle(tmp_path):
    _schreibe(tmp_path, "core/kurz.py", "x = 1\n" * 9)
    assert regeln.pruefe_groessen(tmp_path, _groessen_vertrag()) == []


def test_groesse_verlangt_das_senken_der_obergrenze(tmp_path):
    """Die Ratsche greift auch nach unten: eine geraeumte Ausnahme muss gestrichen
    werden, sonst kann die Zahl unbemerkt wieder steigen."""
    _schreibe(tmp_path, "core/lang.py", "x = 1\n" * 12)
    vertrag = _groessen_vertrag(ausnahmen_dateien={"core/lang.py": 15})
    (meldung,) = regeln.pruefe_groessen(tmp_path, vertrag)
    assert "nur noch 12" in meldung and "15" in meldung
    assert "Senke" in meldung


def test_groesse_besteht_genau_auf_der_obergrenze(tmp_path):
    _schreibe(tmp_path, "core/lang.py", "x = 1\n" * 12)
    vertrag = _groessen_vertrag(ausnahmen_dateien={"core/lang.py": 12})
    assert regeln.pruefe_groessen(tmp_path, vertrag) == []


def test_groesse_meldet_wachstum_ueber_die_eingefrorene_zahl(tmp_path):
    _schreibe(tmp_path, "core/lang.py", "x = 1\n" * 20)
    vertrag = _groessen_vertrag(ausnahmen_dateien={"core/lang.py": 12})
    (meldung,) = regeln.pruefe_groessen(tmp_path, vertrag)
    assert "20 Zeilen" in meldung and "12" in meldung


def test_groesse_verlangt_das_streichen_einer_geraeumten_ausnahme(tmp_path):
    """Faellt eine Ausnahme unter die Schwelle, ist der Eintrag selbst zu streichen."""
    _schreibe(tmp_path, "core/lang.py", "x = 1\n" * 4)
    vertrag = _groessen_vertrag(ausnahmen_dateien={"core/lang.py": 12})
    (meldung,) = regeln.pruefe_groessen(tmp_path, vertrag)
    assert "Streiche" in meldung and "core/lang.py" in meldung


def test_groesse_nimmt_eine_ausnahme_mit_begruendung_an(tmp_path):
    """Ohne Begruendung ist ein Eintrag ein Aufraeum-Auftrag (nackte Zahl), mit
    Begruendung eine bewusste Ausnahme (Tabelle) — beide Formen gelten (Plan, 2.2)."""
    _schreibe(tmp_path, "core/lang.py", "x = 1\n" * 12)
    vertrag = _groessen_vertrag(
        ausnahmen_dateien={"core/lang.py": {"zeilen": 12, "begruendung": "erzeugt"}}
    )
    assert regeln.pruefe_groessen(tmp_path, vertrag) == []


def test_groesse_meldet_funktion_ueber_der_schwelle(tmp_path):
    """``async def`` und Verschachtelung muessen beide greifen — sonst faellt
    ``ast.AsyncFunctionDef`` stillschweigend durch."""
    _schreibe(
        tmp_path,
        "core/f.py",
        """\
        class K:
            async def lang(self):
                a = 1
                b = 2
                c = 3

                def tief():
                    d = 1
                    e = 2
                    f = 4
                    g = 5
                    return d
        """,
    )
    meldungen = "\n".join(regeln.pruefe_groessen(tmp_path, _groessen_vertrag()))
    assert "K.lang" in meldungen, meldungen
    assert "K.lang.tief" in meldungen, "Der Name muss qualifiziert sein."
    assert "core/f.py:2" in meldungen, "Der Befund muss Datei und Zeile nennen."


def test_groesse_zaehlt_kurze_funktionen_nicht(tmp_path):
    _schreibe(tmp_path, "core/f.py", "def kurz():\n    return 1\n")
    assert regeln.pruefe_groessen(tmp_path, _groessen_vertrag()) == []


def test_groesse_zaehlt_testdateien_nicht(tmp_path):
    """Der Bereich ist Produktivcode (Epic #3738, Abschnitt 3: ``ohne: tests/``)."""
    _schreibe(tmp_path, "core/tests/test_lang.py", "x = 1\n" * 50)
    assert regeln.pruefe_groessen(tmp_path, _groessen_vertrag()) == []


def test_groesse_zaehlt_fremdcode_nicht(tmp_path):
    """``pandas-ta`` ist vendoriert (eigene LICENSE, 245 Dateien) und steht in
    ``.pre-commit-config.yaml`` auf derselben Ausschlussliste. Die eingefrorenen
    Zahlen des Epics (27 Dateien, 61 Funktionen) entstehen nur ohne ihn."""
    _schreibe(tmp_path, "core/pandas-ta/lang.py", "x = 1\n" * 50)
    _schreibe(tmp_path, "core/venv/lang.py", "x = 1\n" * 50)
    assert regeln.pruefe_groessen(tmp_path, _groessen_vertrag()) == []


def test_groesse_misst_die_funktionslaenge_wie_der_plan(tmp_path):
    """``end_lineno - lineno + 1`` — eine Zahl, die mit ``sed -n`` nachpruefbar ist."""
    _schreibe(
        tmp_path,
        "core/f.py",
        """\
        def genau_sechs():
            a = 1
            b = 2
            c = 3
            d = 4
            return a
        """,
    )
    befunde = regeln.funktions_groessen(tmp_path, "core")
    assert [(b.was, b.zeile, int(b.zusatz)) for b in befunde] == [("genau_sechs", 1, 6)]


def test_groessen_gegen_den_code():
    _melde("groessen", regeln.pruefe_groessen(regeln.PAKET, regeln.lade_vertrag()))


# ── 9b. Stufe 1: ueber 2000 Zeilen nur mit Begruendung (#3835, ARC-E6 G-8) ────
#
# Unter der Stufe-1-Grenze darf eine nackte Zahl Arbeitsvorrat sein; darueber ist sie
# keine Entscheidung. Eine Datei bleibt dort nur stehen, wenn jemand die
# Zusammengehoerigkeit in ``begruendung`` behauptet (Epic #3738, Abschnitt 5).


def test_ausnahme_ueber_stufe_1_braucht_begruendung(tmp_path):
    _schreibe(tmp_path, "core/lang.py", "x = 1\n" * 30)
    _schreibe(tmp_path, "core/erklaert.py", "x = 1\n" * 30)
    _schreibe(tmp_path, "core/leer.py", "x = 1\n" * 30)
    _schreibe(tmp_path, "core/mittel.py", "x = 1\n" * 15)
    vertrag = _groessen_vertrag(
        stufe_1_datei_schwelle=20,
        ausnahmen_dateien={
            "core/lang.py": 30,
            "core/erklaert.py": {"zeilen": 30, "begruendung": "eine Deklarationsliste"},
            "core/leer.py": {"zeilen": 30, "begruendung": "  "},
            "core/mittel.py": 15,
        },
    )
    meldungen = regeln.pruefe_groessen(tmp_path, vertrag)
    assert len(meldungen) == 2, meldungen
    text = "\n".join(meldungen)
    assert "core/lang.py" in text and "core/leer.py" in text
    assert "Stufe-1-Grenze 20" in text and "begruendung" in text
    assert "core/erklaert.py" not in text, "Mit Begruendung ist sie ein Ergebnis."
    assert "core/mittel.py" not in text, "Unter Stufe 1 bleibt die Zahl Arbeitsvorrat."


def test_der_vertrag_traegt_die_stufe_1_grenze():
    assert regeln.lade_vertrag()["groessen"]["stufe_1_datei_schwelle"] == 2000


# ── 9b2. Stufe 1 auch fuer Funktionen: ueber 400 nur mit Begruendung (#4175, G-8c) ──


def _funktion_mit_zeilen(name: str, zeilen: int) -> str:
    return f"def {name}():\n" + "    x = 1\n" * (zeilen - 1)


def _funktions_stufe_1_meldungen(meldungen: list[str]) -> list[str]:
    return [m for m in meldungen if "Stufe-1-Grenze" in m]


def test_funktion_ueber_stufe_1_braucht_begruendung(tmp_path):
    _schreibe(
        tmp_path,
        "core/lang.py",
        "x = 0\n\n\n"
        + _funktion_mit_zeilen("nackt", 401)
        + "\n\n"
        + _funktion_mit_zeilen("erklaert", 401)
        + "\n\n"
        + _funktion_mit_zeilen("leer", 401)
        + "\n\n"
        + _funktion_mit_zeilen("ohne_eintrag", 401)
        + "\n\n"
        + _funktion_mit_zeilen("mittel", 400),
    )
    vertrag = _groessen_vertrag(
        datei_schwelle=10_000,
        funktion_schwelle=200,
        stufe_1_funktion_schwelle=400,
        ausnahmen_funktionen={
            "core/lang.py": {
                "nackt": 401,
                "erklaert": {"zeilen": 401, "begruendung": "ein Kapitalpfad"},
                "leer": {"zeilen": 401, "begruendung": "  "},
                "mittel": 400,
            }
        },
    )
    meldungen = _funktions_stufe_1_meldungen(regeln.pruefe_groessen(tmp_path, vertrag))
    assert len(meldungen) == 3, meldungen
    text = "\n".join(meldungen)
    assert "core/lang.py:4 nackt" in text, "Meldung nennt Datei und def-Zeile."
    assert " leer" in text and " ohne_eintrag" in text
    assert "Stufe-1-Grenze 400" in text and "begruendung" in text
    assert " erklaert" not in text, "Mit Begruendung ist sie ein Ergebnis."
    assert " mittel" not in text, "Unter Stufe 1 bleibt die Zahl Arbeitsvorrat."


def test_funktion_mit_begruendung_bleibt_obergrenze(tmp_path):
    _schreibe(tmp_path, "core/lang.py", _funktion_mit_zeilen("gewachsen", 408))
    vertrag = _groessen_vertrag(
        datei_schwelle=10_000,
        funktion_schwelle=200,
        stufe_1_funktion_schwelle=400,
        ausnahmen_funktionen={
            "core/lang.py": {
                "gewachsen": {"zeilen": 407, "begruendung": "ein Entscheidungspfad"}
            }
        },
    )
    (meldung,) = regeln.pruefe_groessen(tmp_path, vertrag)
    assert "gewachsen" in meldung and "408 Zeilen, eingefroren sind 407" in meldung


def test_der_vertrag_traegt_die_stufe_1_funktionsgrenze():
    assert regeln.lade_vertrag()["groessen"]["stufe_1_funktion_schwelle"] == 400


@pytest.mark.parametrize(
    "datei, name",
    [
        # #4175 (G-8c): begruendete Ausnahmen, Owner-Entscheidung 05.10.2026.
        ("core/engine/order_executor.py", "OrderExecutorMixin._process_signal_event"),
        ("core/round_table/runner.py", "run_round_table"),
    ],
)
def test_der_stufe_1_funktionsrest_ist_entschieden(datei, name):
    """Abnahme G-8c (#4175): die Funktion liegt unter der Stufe-1-Grenze oder steht
    mit gefuellter Begruendung im Vertrag."""
    teil = regeln.lade_vertrag()["groessen"]
    grenze = teil["stufe_1_funktion_schwelle"]
    (zeilen,) = [
        int(b.zusatz)
        for b in regeln.funktions_groessen(regeln.PAKET, ".")
        if b.datei == datei and b.was == name
    ]
    eintrag = teil["ausnahmen_funktionen"].get(datei, {}).get(name)
    begruendet = (
        isinstance(eintrag, dict) and str(eintrag.get("begruendung", "")).strip()
    )
    assert zeilen <= grenze or begruendet, (
        f"{datei} {name}: {zeilen} Zeilen ueber der Stufe-1-Grenze {grenze}, "
        f"Eintrag {eintrag!r} — weder zerlegt noch begruendet."
    )


# ── 9d. G-10: Groessen scharf, Stufe 2 (#3839, ARC-E6) ────────────────────────


def test_groessen_blockiert():
    """Abnahme G-10: ein Befund ausserhalb der Ausnahmeliste laesst den Lauf scheitern.
    Das Verhalten von ``_melde`` selbst prueft
    ``test_der_modus_blockieren_laesst_den_lauf_scheitern``."""
    assert regeln.lade_vertrag()["modus"]["groessen"] == "blockieren"


def test_groessen_traegt_stufe_2():
    teil = regeln.lade_vertrag()["groessen"]
    assert teil["datei_schwelle"] == 1000
    assert teil["funktion_schwelle"] == 200


_STUFE_1_REST = [
    "settings.py",
    # #4161 (G-8a2): EDGAR- und Insider-Quellen liegen in core/specialist/quellen_edgar.py,
    # die Datei liegt unter 2000 (G8_ENTSCHEIDUNG_stock_specialist.md, Nachtrag).
    "core/stock_specialist.py",
    # #4154 (G-8b): Kapitalpfad-Kern, begruendete Ausnahme (Owner-Entscheidung 05.10.2026).
    "core/engine/order_executor.py",
    "core/engine/trading_loop.py",
]


@pytest.mark.parametrize("pfad", _STUFE_1_REST)
def test_der_stufe_1_rest_ist_entschieden(pfad):
    """Abnahme G-8 (#3835): jede der beiden Dateien liegt unter der Stufe-1-Grenze
    oder steht mit gefuellter Begruendung im Vertrag."""
    teil = regeln.lade_vertrag()["groessen"]
    grenze = teil["stufe_1_datei_schwelle"]
    zeilen = len(
        (regeln.PAKET / pfad)
        .read_text(encoding="utf-8-sig", errors="replace")
        .splitlines()
    )
    eintrag = teil["ausnahmen_dateien"].get(pfad)
    begruendet = (
        isinstance(eintrag, dict) and str(eintrag.get("begruendung", "")).strip()
    )
    assert zeilen <= grenze or begruendet, (
        f"{pfad}: {zeilen} Zeilen ueber der Stufe-1-Grenze {grenze}, Eintrag "
        f"{eintrag!r} — weder zerlegt noch begruendet."
    )


# ── 9c. G-0b: einmalig neu eingefroren (#4155, ARC-E6) ────────────────────────
#
# Owner-Entscheidung 05.10.2026: Was seit G-0 durch fachliche Fixes und Namensbruecken
# gewachsen ist, steht einmal auf dem gemessenen Stand — je mit dem verursachenden PR
# als Kommentar ``# #4155 (G-0b): ...`` direkt ueber dem Eintrag. Das ist die eine
# sichtbare Ausnahme von "darf nur sinken"; fuer diese Eintraege gilt die Ratsche ab
# hier sofort blockierend, auch solange ``groessen`` noch auf "warnen" steht.

_G0B_NEU_EINGEFROREN = {
    ("groessen.ausnahmen_dateien", "core/engine/base.py"): ["#3992", "#4014"],
    ("groessen.ausnahmen_dateien", "settings.py"): ["#4131"],
    ("groessen.ausnahmen_funktionen", "core/analysis/attribution/replay.py"): ["#4144"],
    ("doku_groessen.ausnahmen", "docs/5_engineering_and_devops/DEVOPS-CICD.md"): [
        "#4106"
    ],
}


def _g0b_eintraege() -> dict[tuple[str, str], str]:
    """Jeder Vertragseintrag, ueber dem ein ``#4155``-Kommentar steht, mit diesem
    Kommentar. Gelesen aus dem Text, weil ``tomllib`` Kommentare verwirft."""
    eintraege, abschnitt, kommentar = {}, "", None
    for zeile in regeln.VERTRAG.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if zeile.startswith("[") and zeile.endswith("]"):
            abschnitt, kommentar = zeile.strip("[]"), None
        elif zeile.startswith("#"):
            if "#4155" in zeile:
                kommentar = zeile
        elif zeile.startswith('"') and kommentar:
            eintraege[(abschnitt, zeile.split('"')[1])] = kommentar
            kommentar = None
        else:
            kommentar = None
    return eintraege


def test_g0b_jede_neu_einfrierung_nennt_ihre_ursache():
    eintraege = _g0b_eintraege()
    assert set(eintraege) == set(_G0B_NEU_EINGEFROREN), (
        "Genau die vier in G-0b gemessenen Abweichungen tragen den #4155-Kommentar; "
        "eine weitere Neu-Einfrierung braucht einen eigenen, sichtbaren Posten."
    )
    for schluessel, prs in _G0B_NEU_EINGEFROREN.items():
        kommentar = eintraege[schluessel]
        assert "Owner 05.10.2026" in kommentar, kommentar
        for pr in prs:
            assert pr in kommentar, f"{schluessel}: verursachender PR {pr} fehlt."


def _g0b_gemessen(abschnitt: str, schluessel: str, eintrag) -> list[tuple[str, int]]:
    if abschnitt == "groessen.ausnahmen_funktionen":
        return [
            (f"{b.datei} {b.was}", int(b.zusatz))
            for b in regeln.funktions_groessen(regeln.PAKET, "core")
            if b.datei == schluessel and b.was in eintrag
        ]
    wurzel = regeln.REPO if abschnitt == "doku_groessen.ausnahmen" else regeln.PAKET
    quelle = (wurzel / schluessel).read_text(encoding="utf-8-sig", errors="replace")
    return [(schluessel, len(quelle.splitlines()))]


@pytest.mark.parametrize("abschnitt,schluessel", sorted(_G0B_NEU_EINGEFROREN))
def test_g0b_die_ratsche_gilt_sofort_wieder(abschnitt, schluessel):
    """Gemessen darf nie ueber der neu eingefrorenen Zahl liegen — blockierend,
    unabhaengig vom Modus der Regel."""
    teil = regeln.lade_vertrag()
    for name in abschnitt.split("."):
        teil = teil[name]
    eintrag = teil[schluessel]
    if abschnitt == "groessen.ausnahmen_funktionen":
        grenzen = {f"{schluessel} {name}": zahl for name, zahl in eintrag.items()}
    else:
        zahl = eintrag["zeilen"] if isinstance(eintrag, dict) else eintrag
        grenzen = {schluessel: zahl}
    gemessen = _g0b_gemessen(abschnitt, schluessel, eintrag)
    assert {was for was, _ in gemessen} == set(grenzen), gemessen
    for was, n in gemessen:
        assert n <= grenzen[was], (
            f"{was}: {n} Zeilen, eingefroren sind {grenzen[was]} (#4155, G-0b). "
            f"Die einmalige Neu-Einfrierung ist verbraucht — die Zahl darf nur sinken."
        )


# ── 9d. Vorläufige Begründungen (ARC-E6 Neuzuschnitt 06.10.2026, Epic §8) ──────
#
# Owner-Entscheidung 06.10.2026: Die Begründungen für die Kapitalpfad-Hotspots bleiben,
# bis ihre Zerlegung (H-1, H-2, H-4) sie streicht. Bis dahin sagen sie das ausdrücklich
# und nennen ihr Issue - eine Begründung ohne Ablaufdatum wäre wieder eine Dauerausnahme.

# #4240 (H-1k): beide Einträge zu core/engine/order_executor.py (#4183) gestrichen —
# Datei und Dirigent liegen unter den Schwellen.
# #4251 (H-2j): Eintrag zu core/engine/trading_loop.py (#4184) gestrichen — die Datei liegt
# mit 996 Zeilen unter der Schwelle 1000.
# #4281 (H-4h): run_round_table (#4186) gestrichen — in Phasen-Schritte unter 150 zerlegt.
_VORLAEUFIG = {}


@pytest.mark.parametrize("abschnitt,datei,name", sorted(_VORLAEUFIG, key=str))
def test_hotspot_begruendungen_sind_vorlaeufig(abschnitt, datei, name):
    teil = regeln.lade_vertrag()
    for schluessel in abschnitt.split("."):
        teil = teil[schluessel]
    eintrag = teil[datei] if name is None else teil[datei][name]
    begruendung = str(eintrag.get("begruendung", ""))
    issue = _VORLAEUFIG[(abschnitt, datei, name)]
    assert begruendung.startswith("VORLÄUFIG"), begruendung
    assert (
        issue in begruendung
    ), f"{datei} {name or ''}: Zerlegungs-Issue {issue} fehlt."


# ── 9e. Jede Begruendung hat eine Herkunft (M1.2, #4199, Retro #4193 W1) ─────────
#
# In ARC-E6 haben begruendete Ausnahmen Zerlegungen ersetzt (G-8b, G-8c): Eine Begruendung
# ohne Ablauf und ohne Entscheider wurde still zur Dauerausnahme. Erlaubt sind nur noch
# zwei Formen: "VORLÄUFIG bis ... (#N)" - die Zerlegung hat ein Issue - oder
# "Owner-Entscheidung (#N ...)" - der Owner hat im Epic entschieden (Muster #4191).


def test_begruendung_ohne_herkunft_ist_ein_befund():
    vertrag = _groessen_vertrag(
        ausnahmen_dateien={
            "core/frei.py": {"zeilen": 30, "begruendung": "ein Thema, gut lesbar"},
            "core/vorlaeufig.py": {
                "zeilen": 30,
                "begruendung": "VORLÄUFIG bis H-9 (#9999): wird zerlegt.",
            },
            "core/owner.py": {
                "zeilen": 30,
                "begruendung": "Owner-Entscheidung (#3738 §8.2): ein Schema.",
            },
            "core/nackt.py": 30,
        },
        ausnahmen_funktionen={
            "core/f.py": {"lang": {"zeilen": 40, "begruendung": "historisch gewachsen"}}
        },
    )
    meldungen = regeln.pruefe_begruendungen(vertrag)
    text = "\n".join(meldungen)
    assert len(meldungen) == 2, meldungen
    assert "core/frei.py" in text and "core/f.py lang" in text
    assert (
        "VORLÄUFIG bis" in text and "Owner-Entscheidung" in text
    ), "Der Befund muss die beiden erlaubten Formen nennen."


@pytest.mark.parametrize(
    "begruendung",
    [
        "VORLÄUFIG bis H-1: ohne Issue",  # Ablauf ohne Issue
        "Owner-Entscheidung: ohne Fundstelle",  # Entscheider ohne Fundstelle
        "Wird später VORLÄUFIG bis #4183 zerlegt",  # VORLÄUFIG nicht am Anfang
        "  ",
    ],
)
def test_halbe_herkunft_zaehlt_nicht(begruendung):
    vertrag = _groessen_vertrag(
        ausnahmen_dateien={"core/x.py": {"zeilen": 30, "begruendung": begruendung}}
    )
    assert len(regeln.pruefe_begruendungen(vertrag)) == 1


def test_begruendungen_im_vertrag_haben_herkunft():
    _melde("groessen", regeln.pruefe_begruendungen(regeln.lade_vertrag()))


# ── #4188 (H-K): kalte Grossdateien tragen Messwert und Datum ──
#
# Epic #3738 §8.2: "Kalte Dateien bleiben mit der Begruendung 'kalt' und ihrer gemessenen
# Aenderungsrate im Vertrag. Steigt die Rate, kommen sie nach." Eine "kalt"-Begruendung
# ohne Messwert und Datum ist nicht nachpruefbar und damit eine stille Dauerausnahme.

_KALT = "Owner-Entscheidung (#3738 §8.2): kalt"

# Gemessen mit scripts/mess_hotspots.py --ohne-github (#4182) am 2026-10-08:
# ueber datei_schwelle 1000 und hoechstens 3 Commits in 60 Tagen.
_KALTE_DATEIEN = [
    "core/stock_specialist.py",
    "gui/gui.py",
    "core/data_provider.py",
    "core/specialist/grounding.py",
    "scripts/train_v4_lightgbm.py",
    "core/report/auditor.py",
]


def _kalt_vertrag(begruendung, als_funktion=False):
    eintrag = {"zeilen": 30, "begruendung": begruendung}
    if als_funktion:
        return _groessen_vertrag(ausnahmen_funktionen={"core/f.py": {"lang": eintrag}})
    return _groessen_vertrag(ausnahmen_dateien={"core/x.py": eintrag})


@pytest.mark.parametrize(
    "begruendung, fehlt",
    [
        (f"{_KALT}.", "Commits/<t> Tage"),
        (f"{_KALT}: 0 Commits, gemessen 2026-10-08.", "Commits/<t> Tage"),
        (f"{_KALT}: 0 Commits/60 Tage.", "gemessen <JJJJ-MM-TT>"),
    ],
)
def test_kalt_ohne_messwert_ist_befund(begruendung, fehlt):
    (meldung,) = regeln.pruefe_kalt_begruendungen(_kalt_vertrag(begruendung))
    assert "core/x.py" in meldung
    assert fehlt in meldung, "Der Befund muss die fehlende Angabe nennen."


@pytest.mark.parametrize(
    "datum", ["2026-13-01", (date.today() + timedelta(days=1)).isoformat()]
)
def test_kalt_mit_ungueltigem_oder_kuenftigem_datum_ist_befund(datum):
    begruendung = f"{_KALT}: 0 Commits/60 Tage, gemessen {datum}."
    (meldung,) = regeln.pruefe_kalt_begruendungen(_kalt_vertrag(begruendung))
    assert datum in meldung


def test_kalt_mit_mehr_als_drei_commits_ist_befund():
    begruendung = f"{_KALT}: 4 Commits/60 Tage, gemessen 2026-10-08."
    (meldung,) = regeln.pruefe_kalt_begruendungen(_kalt_vertrag(begruendung))
    assert "nicht kalt" in meldung and "3" in meldung


@pytest.mark.parametrize("als_funktion", [False, True])
def test_vollstaendige_kalt_begruendung_ist_kein_befund(als_funktion):
    begruendung = (
        f"{_KALT}: 3 Commits/60 Tage, gemessen 2026-10-08 (scripts/mess_hotspots.py)."
    )
    vertrag = _kalt_vertrag(begruendung, als_funktion=als_funktion)
    assert regeln.pruefe_kalt_begruendungen(vertrag) == []
    assert regeln.pruefe_begruendungen(vertrag) == [], "Die Form muss HERKUNFT tragen."


@pytest.mark.parametrize(
    "begruendung",
    [
        "VORLÄUFIG bis H-9 (#9999): wird zerlegt.",
        "Owner-Entscheidung (#3738 §8.2, aus G-8 #3835): ein Schema.",
        "Owner-Entscheidung (#3738 §8.2): Kaltstart des Modells, ein Thema.",
    ],
)
def test_andere_begruendungen_sind_nicht_betroffen(begruendung):
    assert regeln.pruefe_kalt_begruendungen(_kalt_vertrag(begruendung)) == []
    assert regeln.pruefe_kalt_begruendungen(_kalt_vertrag(begruendung, True)) == []


def test_kalt_begruendungen_im_vertrag_tragen_messwert_und_datum():
    _melde("groessen", regeln.pruefe_kalt_begruendungen(regeln.lade_vertrag()))


@pytest.mark.parametrize("pfad", _KALTE_DATEIEN)
def test_kalte_dateien_stehen_nicht_als_nackte_zahl(pfad):
    eintrag = regeln.lade_vertrag()["groessen"]["ausnahmen_dateien"][pfad]
    assert isinstance(eintrag, dict), f"{pfad} steht noch als nackte Zahl im Vertrag."
    assert eintrag["begruendung"].startswith(f"{_KALT}: "), eintrag["begruendung"]


# ── 10. Doku-Groessen: keine neue Sammel-Datei unter docs/ (#3837, ARC-E6 G-9) ──
#
# Dieselbe Ratsche wie Regel 9, fuer Markdown unter ``docs/``. Anlass war
# ``docs/PR_WALKTHROUGH_HISTORY.md`` mit 15.056 Zeilen: Eine Sammel-Datei waechst still,
# bis niemand sie mehr liest. Gemessen wird relativ zum Repository, nicht zum Paket.


def _doku_vertrag(schwelle=10, **teil):
    return {"doku_groessen": {"bereiche": ["docs"], "schwelle": schwelle, **teil}}


def test_keine_neue_sammeldatei(tmp_path):
    _schreibe(tmp_path, "docs/halde.md", "zeile\n" * 12)
    _schreibe(tmp_path, "docs/kurz.md", "zeile\n" * 10)
    (meldung,) = regeln.pruefe_doku_groessen(tmp_path, _doku_vertrag())
    assert "docs/halde.md" in meldung, "Der Befund muss den Pfad nennen."
    assert "12 Zeilen" in meldung, "Der Befund muss die Zeilenzahl nennen."
    assert "[doku_groessen.ausnahmen]" in meldung


def test_doku_groesse_zaehlt_nur_markdown_unter_den_bereichen(tmp_path):
    _schreibe(tmp_path, "docs/daten.json", "{}\n" * 50)
    _schreibe(tmp_path, "README.md", "zeile\n" * 50)
    assert regeln.pruefe_doku_groessen(tmp_path, _doku_vertrag()) == []


def test_doku_groesse_ist_eine_ratsche_in_beide_richtungen(tmp_path):
    _schreibe(tmp_path, "docs/a/gross.md", "zeile\n" * 12)
    _schreibe(tmp_path, "docs/geraeumt.md", "zeile\n" * 3)
    vertrag = _doku_vertrag(ausnahmen={"docs/a/gross.md": 15, "docs/geraeumt.md": 20})
    text = "\n".join(regeln.pruefe_doku_groessen(tmp_path, vertrag))
    assert "Senke" in text and "docs/a/gross.md" in text
    assert "Streiche" in text and "docs/geraeumt.md" in text


def test_doku_groessen_gegen_den_code():
    _melde(
        "doku_groessen",
        regeln.pruefe_doku_groessen(regeln.REPO, regeln.lade_vertrag()),
    )
