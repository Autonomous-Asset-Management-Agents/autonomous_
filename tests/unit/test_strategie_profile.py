"""#4629 (STW-3) — benannte Strategien: Pruefung, Obergrenze, Ablage.

Plan: ``docs/4629-*/implementation_plan.md`` §2 Punkt 1, TDD-Reihenfolge Schritt 1.

Anders als ``trading_settings.apply_updates`` klemmt ``pruefe_strategie`` nicht: ein still
geklemmter Wert wuerde eine Strategie versiegeln, die der Nutzer nie angelegt hat.
"""

from __future__ import annotations

import json

import pytest

from core import strategie_profile as sp
from core import trading_settings as ts

pytestmark = [pytest.mark.unit, pytest.mark.vc0]


def test_unbekannter_key_wirft_422():
    with pytest.raises(ts.UnknownSettingError):
        sp.pruefe_strategie("A", {"GIBT_ES_NICHT": 1})


@pytest.mark.parametrize(
    "key",
    [
        "HARD_STOP_LOSS_PCT",
        "HITL_ENABLED",
        "RISK_FORBID_LEVERAGE",
        "TRAILING_STOP_PCT",
        "GATEKEEPER_PDT_CROSSDAY_EXEMPTION",
    ],
)
def test_leitplanke_wirft_422(key):
    with pytest.raises(ts.UngueltigeEinstellungError):
        sp.pruefe_strategie("A", {key: 1})


def test_leitplanken_liegen_nie_in_der_registry():
    """Abgleich der Sperrliste gegen die Registry (Plan §6 „Leitplanken-Liste")."""
    assert not set(sp.LEITPLANKEN_KEYS) & set(ts.REGISTRY)
    assert {k for k, _, _ in ts._GUARDRAIL_VIEW_KEYS} <= set(sp.LEITPLANKEN_KEYS)


@pytest.mark.parametrize(
    "key,wert",
    [
        ("STOP_LOSS_PCT", 9.5),  # hi 7.9
        ("STOP_LOSS_PCT", 0.5),  # lo 1.0
        ("MAX_BUYS_PER_HOUR", 13),  # hi 12
        ("MAX_BUYS_PER_HOUR", 2.5),  # int, aber nicht ganzzahlig
        ("SIGNAL_BUY_THRESHOLD", "abc"),
        ("SIGNAL_BUY_THRESHOLD", float("nan")),
        ("ROTATION_EXIT_ENABLED", "vielleicht"),
        ("ROTATION_EXIT_ENABLED", 1),
    ],
)
def test_wert_ausserhalb_grenzen_wirft_422_ohne_klemmen(key, wert):
    with pytest.raises(ts.UngueltigeEinstellungError):
        sp.pruefe_strategie("A", {key: wert})


def test_ungueltiges_enum_wirft_422():
    with pytest.raises(ts.UngueltigeEinstellungError):
        sp.pruefe_strategie("A", {"EXIT_TRAIL_PROFILE": "aggressiv"})


def test_zu_langer_text_wirft_422():
    lang = "x" * (ts.LLM_RANKING_PROMPT_MAXLEN + 1)
    with pytest.raises(ts.UngueltigeEinstellungError):
        sp.pruefe_strategie("A", {"LLM_RANKING_PROMPT": lang})


def test_llm_quelle_ohne_prompt_wirft_422():
    with pytest.raises(ts.UngueltigeEinstellungError):
        sp.pruefe_strategie("A", {"LLM_RANKING_QUELLE": "llm"})


@pytest.mark.parametrize("name", ["", "   ", "x" * (sp.NAME_MAXLEN + 1)])
def test_leerer_oder_zu_langer_name_wirft_422(name):
    with pytest.raises(ts.UngueltigeEinstellungError):
        sp.pruefe_strategie(name, {"STOP_LOSS_PCT": 5.0})


def test_gueltige_werte_kommen_kanonisch_zurueck():
    werte = sp.pruefe_strategie(
        "A",
        {
            "STOP_LOSS_PCT": 5,
            "MAX_BUYS_PER_HOUR": 3.0,
            "ROTATION_EXIT_ENABLED": "TRUE",
            "EXIT_TRAIL_PROFILE": "Legacy",
        },
    )
    assert werte == {
        "EXIT_TRAIL_PROFILE": "legacy",
        "MAX_BUYS_PER_HOUR": "3",
        "ROTATION_EXIT_ENABLED": "true",
        "STOP_LOSS_PCT": "5.0",
    }
    assert all(isinstance(v, str) for v in werte.values())


def test_grenzwerte_selbst_sind_zulaessig():
    s = ts.REGISTRY["STOP_LOSS_PCT"]
    assert sp.pruefe_strategie("A", {"STOP_LOSS_PCT": s.hi}) == {"STOP_LOSS_PCT": "7.9"}
    assert sp.pruefe_strategie("A", {"STOP_LOSS_PCT": s.lo}) == {"STOP_LOSS_PCT": "1.0"}


def test_vierte_strategie_wirft_422():
    bestand = {n: {"STOP_LOSS_PCT": "5.0"} for n in ("A", "B", "C")}
    with pytest.raises(ts.UngueltigeEinstellungError):
        sp.pruefe_obergrenze(bestand, "D")
    # Eine bestehende aendern ist keine vierte.
    sp.pruefe_obergrenze(bestand, "A")
    assert sp.MAX_STRATEGIEN == 3


def test_werte_kanonisch_sortiert():
    werte = {
        "STOP_LOSS_PCT": "5.0",
        "EXIT_TRAIL_PROFILE": "legacy",
        "LLM_RANKING_PROMPT": "geheimer Prompt",
    }
    text = sp.werte_fuer_kette(werte)
    assert list(json.loads(text)) == sorted(werte)
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    # Der Prompt erreicht die Kette nur als Version (wie ts.worm_changes, #4654).
    assert "geheimer Prompt" not in text
    assert json.loads(text)["LLM_RANKING_PROMPT"].startswith("sha256:")


def test_liste_im_klartext_auf_der_kette():
    """#4861 (Plan §2.4): die eigene Liste steht im Klartext, der Prompt bleibt Hash."""
    text = sp.werte_fuer_kette(
        {"LLM_RANKING_LISTE": "AAPL, MSFT", "LLM_RANKING_PROMPT": "Idee"}
    )
    kette = json.loads(text)
    assert kette["LLM_RANKING_LISTE"] == "AAPL, MSFT"
    assert kette["LLM_RANKING_PROMPT"].startswith("sha256:")


@pytest.mark.parametrize("liste", ["", "SAP, 12X"])
def test_liste_ohne_gueltigen_wert_wirft_422(liste):
    with pytest.raises(ts.UngueltigeEinstellungError):
        sp.pruefe_strategie(
            "A", {"LLM_RANKING_QUELLE": "liste", "LLM_RANKING_LISTE": liste}
        )
    werte = sp.pruefe_strategie(
        "A", {"LLM_RANKING_QUELLE": "liste", "LLM_RANKING_LISTE": "AAPL"}
    )
    assert werte["LLM_RANKING_LISTE"] == "AAPL"


def test_ablage_fehlt_heisst_leer(tmp_path):
    assert sp.lade_strategien(tmp_path / "strategien.json") == {}


def test_ablage_unlesbar_wirft(tmp_path):
    pfad = tmp_path / "strategien.json"
    pfad.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(sp.StrategieAblageError):
        sp.lade_strategien(pfad)


def test_ablage_atomar_und_erst_nach_siegel(tmp_path, monkeypatch):
    """``speichere_strategien`` schreibt ueber eine Temp-Datei und ``os.replace``.

    Bricht das Ersetzen ab, bleibt die alte Datei unveraendert und keine Temp-Datei liegen.
    Die Reihenfolge Siegel → Ablage belegt der Routentest
    (``test_routes_steuerung.py::test_strategies_siegel_vor_ablage``).
    """
    pfad = tmp_path / "strategien.json"
    sp.speichere_strategien({"A": {"STOP_LOSS_PCT": "5.0"}}, pfad)
    assert sp.lade_strategien(pfad) == {"A": {"STOP_LOSS_PCT": "5.0"}}

    def kaputtes_replace(src, dst):
        raise OSError("Platte voll")

    monkeypatch.setattr(sp.os, "replace", kaputtes_replace)
    with pytest.raises(OSError):
        sp.speichere_strategien({"B": {"STOP_LOSS_PCT": "4.0"}}, pfad)
    assert sp.lade_strategien(pfad) == {"A": {"STOP_LOSS_PCT": "5.0"}}
    assert [p.name for p in tmp_path.iterdir()] == ["strategien.json"]


def test_ablage_pfad_folgt_dem_datenverzeichnis(tmp_path, monkeypatch):
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    assert sp.ablage_pfad() == tmp_path / "strategien.json"


# ── #4863 (STW-3b): Anlagereihenfolge und Versionsliste ──────────────────────
# Plan: docs/4863-*/implementation_plan.md §2 Punkte 1 und 3, TDD-Schritte 1 und 3.


def test_ablage_behaelt_anlagereihenfolge(tmp_path):
    pfad = tmp_path / "strategien.json"
    sp.speichere_strategien(
        {
            "Wider stops": {"STOP_LOSS_PCT": "5.0"},
            "Momentum only": {"STOP_LOSS_PCT": "4.0"},
        },
        pfad,
    )
    assert list(sp.lade_strategien(pfad)) == ["Wider stops", "Momentum only"]


def test_ablage_werte_bleiben_sortiert(tmp_path):
    """Ohne ``sort_keys`` auf oberster Ebene bleibt die Datei trotzdem deterministisch."""
    pfad = tmp_path / "strategien.json"
    sp.speichere_strategien({"A": {"STOP_LOSS_PCT": "5.0", "MAX_POSITIONS": "3"}}, pfad)
    werte = json.loads(pfad.read_text(encoding="utf-8"))["strategien"]["A"]
    assert list(werte) == ["MAX_POSITIONS", "STOP_LOSS_PCT"]


def test_bestandsdatei_alphabetisch_wird_so_gelesen(tmp_path):
    """Keine Migration (Plan §2 Punkt 1): eine alte, alphabetisch geschriebene Datei
    behaelt ihre Dateireihenfolge, ab dann gilt sie als Anlagereihenfolge."""
    pfad = tmp_path / "strategien.json"
    pfad.write_text(
        json.dumps(
            {"strategien": {"B": {"STOP_LOSS_PCT": "4.0"}, "Z": {}, "A": {}}},
            indent=2,
        ),
        encoding="utf-8",
    )
    assert list(sp.lade_strategien(pfad)) == ["B", "Z", "A"]


def _definition(aktion, name, werte, ts_="2026-10-28T10:00:00+00:00"):
    return {
        "event_type": "strategy_definition",
        "timestamp": ts_,
        "actor": "operator",
        "aktion": aktion,
        "name": name,
        "werte": sp.werte_fuer_kette(werte),
    }


def _kette(log_dir, tag, zeilen):
    log_dir.mkdir(parents=True, exist_ok=True)
    pfad = log_dir / f"audit_log_{tag}.jsonl"
    with pfad.open("a", encoding="utf-8") as f:
        for z in zeilen:
            f.write((z if isinstance(z, str) else json.dumps(z)) + "\n")
    return pfad


def test_versionen_neueste_zuerst_mit_hash_und_feldern(tmp_path):
    _kette(
        tmp_path,
        "2026-10-28",
        [
            _definition("create", "A", {"STOP_LOSS_PCT": "5.0", "MAX_POSITIONS": "3"}),
            _definition(
                "update",
                "A",
                {"STOP_LOSS_PCT": "4.0", "MAX_POSITIONS": "3"},
                "2026-10-28T11:00:00+00:00",
            ),
        ],
    )
    versionen = sp.lies_versionen(tmp_path, "A")
    assert [v.aktion for v in versionen] == ["update", "create"]
    assert versionen[0].felder == ["STOP_LOSS_PCT"]
    assert versionen[1].felder == ["MAX_POSITIONS", "STOP_LOSS_PCT"]
    assert versionen[0].zeitpunkt == "2026-10-28T11:00:00+00:00"
    for v in versionen:
        assert v.hash.startswith("sha256:")
        assert v.hash_kuerzel == v.hash[len("sha256:") :][:8]


def test_versionen_hash_gleich_strategie_hash(tmp_path):
    werte = {"STOP_LOSS_PCT": "5.0"}
    _kette(tmp_path, "2026-10-28", [_definition("create", "A", werte)])
    assert sp.lies_versionen(tmp_path, "A")[0].hash == sp.strategie_hash(werte)


def test_versionen_nur_seit_letztem_create(tmp_path):
    _kette(
        tmp_path,
        "2026-10-28",
        [
            _definition("create", "A", {"STOP_LOSS_PCT": "5.0"}),
            _definition("update", "A", {"STOP_LOSS_PCT": "4.0"}),
            _definition("delete", "A", {"STOP_LOSS_PCT": "4.0"}),
            _definition("create", "A", {"STOP_LOSS_PCT": "3.0"}),
        ],
    )
    versionen = sp.lies_versionen(tmp_path, "A")
    assert [v.aktion for v in versionen] == ["create"]
    assert versionen[0].hash == sp.strategie_hash({"STOP_LOSS_PCT": "3.0"})


def test_versionen_andere_namen_und_event_typen_ignoriert(tmp_path):
    fremd = {**_definition("create", "A", {"STOP_LOSS_PCT": "1.0"})}
    fremd["event_type"] = "strategy_assignment"
    _kette(
        tmp_path,
        "2026-10-28",
        [
            _definition("create", "B", {"STOP_LOSS_PCT": "5.0"}),
            fremd,
            {"event_type": "settings_change", "name": "A"},
            _definition("create", "A", {"STOP_LOSS_PCT": "2.0"}),
        ],
    )
    versionen = sp.lies_versionen(tmp_path, "A")
    assert len(versionen) == 1
    assert versionen[0].hash == sp.strategie_hash({"STOP_LOSS_PCT": "2.0"})


def test_versionen_unlesbare_zeile_wird_uebersprungen_und_geloggt(tmp_path, caplog):
    _kette(
        tmp_path,
        "2026-10-28",
        [
            _definition("create", "A", {"STOP_LOSS_PCT": "5.0"}),
            '{"event_type": "strategy_definition", "name": "A", "akt',
        ],
    )
    with caplog.at_level("WARNING", logger="core.strategie_profile"):
        versionen = sp.lies_versionen(tmp_path, "A")
    assert len(versionen) == 1
    assert any(
        r.levelname == "WARNING" and "1" in r.getMessage() for r in caplog.records
    )


def test_versionen_kettenreihenfolge_ueber_tagesdateien(tmp_path):
    """Die Reihenfolge folgt Dateiname und Zeile, nicht dem ``timestamp``."""
    _kette(
        tmp_path,
        "2026-10-29",
        [
            _definition(
                "update", "A", {"STOP_LOSS_PCT": "4.0"}, "2026-01-01T00:00:00+00:00"
            )
        ],
    )
    _kette(
        tmp_path,
        "2026-10-28",
        [
            _definition(
                "create", "A", {"STOP_LOSS_PCT": "5.0"}, "2026-12-31T00:00:00+00:00"
            )
        ],
    )
    _kette(tmp_path.parent / "fremd", "2026-10-30", [])  # anderes Verzeichnis
    (tmp_path / "audit_log_kaputt.jsonl").write_text(
        json.dumps(_definition("delete", "A", {})) + "\n", encoding="utf-8"
    )
    assert [v.aktion for v in sp.lies_versionen(tmp_path, "A")] == ["update", "create"]


def test_versionen_ohne_create_liefert_gefundene_und_warnt(tmp_path, caplog):
    """Plan §2 Punkt 3 / §6 „Rotation": ohne ``create`` vergleicht das erste ``update``
    mit dem leeren Satz und zeigt alle Keys; das wird auf WARNING geloggt."""
    _kette(
        tmp_path,
        "2026-10-28",
        [_definition("update", "A", {"STOP_LOSS_PCT": "4.0", "MAX_POSITIONS": "3"})],
    )
    with caplog.at_level("WARNING", logger="core.strategie_profile"):
        versionen = sp.lies_versionen(tmp_path, "A")
    assert [v.felder for v in versionen] == [["MAX_POSITIONS", "STOP_LOSS_PCT"]]
    assert any("ohne Ketten-Eintrag" in r.getMessage() for r in caplog.records)
    assert sp.lies_versionen(tmp_path, "Z") == []


def test_versionen_fehlendes_verzeichnis_ist_oserror(tmp_path):
    with pytest.raises(OSError):
        sp.lies_versionen(tmp_path / "gibt-es-nicht", "A")


# ── #4890 (STW-3c): code und params je Ablehnung ─────────────────────────────
# Plan: docs/4890-*/implementation_plan.md §2 Punkt 3/4, TDD-Reihenfolge Schritt 2.


def _fehler(werte, name="A"):
    with pytest.raises(ts.UngueltigeEinstellungError) as exc:
        sp.pruefe_strategie(name, werte)
    return exc.value


@pytest.mark.parametrize(
    "werte,code,params",
    [
        (
            {"ROTATION_EXIT_ENABLED": "vielleicht"},
            "setting_value_type",
            {
                "key": "ROTATION_EXIT_ENABLED",
                "value": "vielleicht",
                "expected": "boolean",
            },
        ),
        (
            {"EXIT_TRAIL_PROFILE": "aggressiv"},
            "setting_value_not_allowed",
            {
                "key": "EXIT_TRAIL_PROFILE",
                "value": "aggressiv",
                "allowed": ["midterm", "legacy"],
            },
        ),
        (
            {"LLM_RANKING_PROMPT": 5},
            "setting_value_type",
            {"key": "LLM_RANKING_PROMPT", "value": 5, "expected": "text"},
        ),
        (
            {"STOP_LOSS_PCT": True},
            "setting_value_type",
            {"key": "STOP_LOSS_PCT", "value": True, "expected": "number"},
        ),
        (
            {"SIGNAL_BUY_THRESHOLD": "abc"},
            "setting_value_type",
            {"key": "SIGNAL_BUY_THRESHOLD", "value": "abc", "expected": "number"},
        ),
        (
            {"SIGNAL_BUY_THRESHOLD": float("nan")},
            "setting_value_type",
            {
                "key": "SIGNAL_BUY_THRESHOLD",
                "value": "nan",
                "expected": "finite_number",
            },
        ),
        (
            {"MAX_BUYS_PER_HOUR": 2.5},
            "setting_value_type",
            {"key": "MAX_BUYS_PER_HOUR", "value": 2.5, "expected": "integer"},
        ),
        (
            {"STOP_LOSS_PCT": 9.5},
            "setting_value_out_of_range",
            {"key": "STOP_LOSS_PCT", "value": 9.5, "min": 1.0, "max": 7.9},
        ),
        (
            {"LLM_RANKING_QUELLE": "llm"},
            "ranking_prompt_missing",
            {"source": "llm"},
        ),
        (
            {"LLM_RANKING_QUELLE": "liste", "LLM_RANKING_LISTE": ""},
            "ranking_list_missing",
            {},
        ),
        (
            {"HITL_ENABLED": False, "HARD_STOP_LOSS_PCT": 1},
            "guardrail_in_strategy",
            {"keys": ["HARD_STOP_LOSS_PCT", "HITL_ENABLED"]},
        ),
    ],
)
def test_4890_ablehnung_traegt_code_und_params(werte, code, params):
    exc = _fehler(werte)
    assert (exc.code, exc.params) == (code, params)


def test_4890_zu_langer_text_nennt_laenge_nicht_den_text():
    lang = "geheim" * 400
    exc = _fehler({"LLM_RANKING_PROMPT": lang})
    assert exc.code == "setting_text_too_long"
    assert exc.params == {
        "key": "LLM_RANKING_PROMPT",
        "length": len(lang),
        "max": ts.LLM_RANKING_PROMPT_MAXLEN,
    }
    assert "geheim" not in str(exc)


def test_4890_name_und_obergrenze_tragen_code():
    exc = _fehler({"STOP_LOSS_PCT": 5.0}, name="")
    assert (exc.code, exc.params) == (
        "strategy_name_length",
        {"min": 1, "max": sp.NAME_MAXLEN},
    )
    bestand = {n: {"STOP_LOSS_PCT": "5.0"} for n in ("A", "B", "C")}
    with pytest.raises(ts.UngueltigeEinstellungError) as obergrenze:
        sp.pruefe_obergrenze(bestand, "D")
    assert (obergrenze.value.code, obergrenze.value.params) == (
        "strategy_limit_reached",
        {"limit": sp.MAX_STRATEGIEN},
    )


def test_4890_unbekannter_key_traegt_code():
    with pytest.raises(ts.UnknownSettingError) as exc:
        sp.pruefe_strategie("A", {"Z_KEY": 1, "A_KEY": 2})
    assert exc.value.code == "unknown_setting"
    assert exc.value.params == {"keys": ["A_KEY", "Z_KEY"]}
    assert exc.value.keys == ["A_KEY", "Z_KEY"]


def test_4890_ungueltige_einstellung_verlangt_einen_code():
    """``code`` ist Pflicht-Keyword: ein Raise ohne Code faellt sofort auf."""
    with pytest.raises(TypeError):
        ts.UngueltigeEinstellungError("ohne Code")


def test_4890_ungueltige_einstellung_uebersteht_pickle_und_str():
    import pickle

    exc = ts.UngueltigeEinstellungError(
        "At most 3", code="strategy_limit_reached", params={"limit": 3}
    )
    kopie = pickle.loads(pickle.dumps(exc))
    assert (str(kopie), kopie.code, kopie.params) == (
        "At most 3",
        "strategy_limit_reached",
        {"limit": 3},
    )


def test_4890_ablage_unlesbar_traegt_code_ohne_rohtext(tmp_path):
    pfad = tmp_path / "strategien.json"
    pfad.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(sp.StrategieAblageError) as exc:
        sp.lade_strategien(pfad)
    assert exc.value.code == "strategy_store_unreadable"
    assert "Expecting" not in str(exc.value)


# ── #4891 (STW-4d): zuweisungen.json — Spiegel der Zuweisungen auf der Kette ──────
# Plan: docs/4891-*/implementation_plan.md §2 Punkt 1, TDD-Reihenfolge Schritt 1.


def test_zuweisungen_fehlende_datei_ist_leer(tmp_path):
    assert sp.lade_zuweisungen(tmp_path / "zuweisungen.json") == {}


def test_zuweisung_speichern_und_lesen(tmp_path):
    pfad = tmp_path / "zuweisungen.json"
    sp.speichere_zuweisung(
        "live", "A", "sha256:" + "a" * 64, "2026-10-29T10:00:00+00:00", pfad=pfad
    )
    assert sp.lade_zuweisungen(pfad) == {
        "live": {
            "strategie": "A",
            "strategie_hash": "sha256:" + "a" * 64,
            "timestamp": "2026-10-29T10:00:00+00:00",
        }
    }
    assert set(json.loads(pfad.read_text(encoding="utf-8"))) == {"zuweisungen"}


def test_zuweisung_ueberschreibt_konto(tmp_path):
    pfad = tmp_path / "zuweisungen.json"
    sp.speichere_zuweisung("live", "A", "sha256:a", "t1", pfad=pfad)
    sp.speichere_zuweisung("live", "B", "sha256:b", "t2", pfad=pfad)
    zuweisungen = sp.lade_zuweisungen(pfad)
    assert list(zuweisungen) == ["live"]
    assert zuweisungen["live"]["strategie"] == "B"


@pytest.mark.parametrize(
    "inhalt",
    [
        "{kaputt",
        '{"zuweisungen": []}',
        "[]",
        '{"zuweisungen": {"live": {}}}',
        '{"zuweisungen": {"live": {"strategie": "A"}}}',
        '{"zuweisungen": {"live": {"strategie": "A", "strategie_hash": "h", "timestamp": ""}}}',
    ],
)
def test_zuweisungen_unlesbar_ist_ablagefehler(tmp_path, inhalt):
    pfad = tmp_path / "zuweisungen.json"
    pfad.write_text(inhalt, encoding="utf-8")
    with pytest.raises(sp.StrategieAblageError):
        sp.lade_zuweisungen(pfad)


def test_zugewiesene_konten():
    zuweisungen = {
        "live": {"strategie": "A", "strategie_hash": "h", "timestamp": "t"},
        "paper-1": {"strategie": "B", "strategie_hash": "h", "timestamp": "t"},
    }
    assert sp.zugewiesene_konten(zuweisungen, "A") == ["live"]
    assert sp.zugewiesene_konten(zuweisungen, "B") == ["paper-1"]
    assert sp.zugewiesene_konten(zuweisungen, "C") == []
    assert sp.zugewiesene_konten({}, "A") == []
