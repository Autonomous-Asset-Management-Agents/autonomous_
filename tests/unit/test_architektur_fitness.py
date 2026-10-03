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
        "getattr_widersprueche",
        "schatten_importe",
        "groessen",
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
