"""#4862 Paritaet: CI-Stichprobe gegen die eingefrorene run_sim-Referenz (Plan TDD-Schritt 5).

Die schnelle Sim rechnet hier live auf dem Mini-Store; die volle Sim (``run_sim`` mit dem
Replay-Treiber) lief einmalig und liegt als ``voll_referenz.json`` daneben. So faellt eine
Drift der **schnellen** Sim in CI auf, ohne dass dort die Engine bootet. Aendert sich die
volle Sim, wird die Referenz mit dem Kommando aus ihrem Kopf neu erzeugt (vorher den Store
mit ``store_erzeugung`` aus demselben Kopf anlegen).
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.vc1]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "stimmen_paritaet"
SYMBOLE = ["AAA", "BBB", "CCC", "DDD"]
N_VORLAUF, N_TAGE = 20, 30


def _werktage(start: date, n: int):
    tage, d = [], start
    while len(tage) < n:
        if d.weekday() < 5:
            tage.append(d)
        d += timedelta(days=1)
    return tage


def baue_mini_store(ziel: Path) -> None:
    """Der Mini-Store der Fixture — deterministisch, ohne Zufall.

    * ``korpus/{SYM}.parquet``: Tagesbalken mit open = high = low = close. Beide Sims sehen
      damit je Tag genau einen Preis (die volle Sim markiert zum Open, die schnelle fuellt
      zum Schluss) und dieselbe ATR (true range == |close - Vorschluss|).
    * ``kurse.parquet``: dieselben Schlusskurse fuer die schnelle Sim, ab dem Vorlauf.
    * ``decisions.parquet``/``votes.parquet``: eine Stimme je Tag und Symbol um 14:00 UTC
      (vor dem Zyklus der vollen Sim um 15:45 ET), ``vetoed = False`` — das Replay spielt
      kein Veto ab (Plan 2.1, Punkt 4).
    """
    import pandas as pd

    tage = _werktage(date(2025, 12, 1), N_VORLAUF + N_TAGE)
    (ziel / "korpus").mkdir(parents=True, exist_ok=True)
    dec, vot, kurse = [], [], []
    for j, sym in enumerate(SYMBOLE):
        balken = []
        for i, tag in enumerate(tage):
            preis = round(
                100.0 + 10 * j + 4.0 * math.sin((i + 3 * j) / 4.0) + 0.1 * i, 4
            )
            balken.append(
                {
                    "ts": pd.Timestamp(tag),
                    "open": preis,
                    "high": preis,
                    "low": preis,
                    "close": preis,
                    "volume": 1_000_000.0,
                }
            )
            kurse.append({"tag": tag.isoformat(), "symbol": sym, "close": preis})
            if i < N_VORLAUF:
                continue
            did = len(dec) + 1
            dec.append(
                {"did": did, "symbol": sym, "ts": f"{tag.isoformat()}T14:00:00+00:00"}
            )
            phase = (i + 7 * j) % 10
            lstm = 0.9 if phase < 4 else 0.1 if phase in (6, 7) else 0.5
            for agent, score, gewicht in [
                ("LSTMSignalAgent", lstm, 1.0),
                ("MomentumAgent", 0.6, 0.5),
            ]:
                vot.append(
                    {
                        "did": did,
                        "agent": agent,
                        "score": score,
                        "weight": gewicht,
                        "vetoed": False,
                    }
                )
        pd.DataFrame(balken).set_index("ts").to_parquet(
            ziel / "korpus" / f"{sym}.parquet"
        )
    pd.DataFrame(dec).to_parquet(ziel / "decisions.parquet")
    pd.DataFrame(vot).to_parquet(ziel / "votes.parquet")
    pd.DataFrame(kurse).to_parquet(ziel / "kurse.parquet")


REFERENZEN = ["voll_referenz.json", "voll_referenz_auslieferung.json"]


def _referenz(name="voll_referenz.json"):
    return json.loads((FIXTURE / name).read_text("utf-8"))


@pytest.mark.parametrize("name", REFERENZEN)
def test_fixture_kopf_nennt_commit_und_kommando(name):
    kopf = _referenz(name)["_kopf"]

    assert len(kopf["commit"]) >= 9
    assert kopf["kommando"].startswith("python -m research.run_signal_replay")
    assert f"--out tests/fixtures/stimmen_paritaet/{name}" in kopf["kommando"]
    for setzung in kopf["setzungen"]:
        assert f"--set {setzung}" in kopf["kommando"]
    assert (FIXTURE / kopf["strategie"]).is_file()


def store_fingerabdruck(verzeichnis: Path) -> str:
    """SHA-256 ueber den Inhalt des Stores, unabhaengig von der Parquet-Kodierung."""
    import hashlib

    import pandas as pd

    h = hashlib.sha256()
    for rel in ["decisions.parquet", "votes.parquet", "kurse.parquet"] + [
        f"korpus/{s}.parquet" for s in SYMBOLE
    ]:
        h.update(rel.encode())
        h.update(
            pd.read_parquet(verzeichnis / rel).to_csv(lineterminator="\n").encode()
        )
    return h.hexdigest()


@pytest.fixture(scope="module")
def mini_store(tmp_path_factory):
    """``*.parquet`` ist im Repo ignoriert (.gitignore) — der Store wird erzeugt, nicht
    eingecheckt. Der Fingerabdruck im Kopf bindet die Referenz an genau diese Daten."""
    ziel = tmp_path_factory.mktemp("stimmen_paritaet")
    baue_mini_store(ziel)
    return ziel


@pytest.mark.parametrize("name", REFERENZEN)
def test_fixture_entspricht_dem_generator(mini_store, name):
    """Die eingefrorene Referenz gilt nur fuer genau diesen Store."""
    assert store_fingerabdruck(mini_store) == _referenz(name)["_kopf"]["store_sha256"]


def _bericht(mini_store, name):
    """Die schnelle Sim live auf dem Mini-Store gegen die eingefrorene Referenz ``name``."""
    from core.sim.stimmen_paritaet import referenz_setzungen, vergleiche
    from core.sim.stimmen_sim import (
        StimmenSimStrategie,
        StimmenStore,
        ergebnis_dict,
        lade_kurse,
        simuliere,
    )

    voll = _referenz(name)
    kopf = voll["_kopf"]
    strategie = StimmenSimStrategie(
        **json.loads((FIXTURE / kopf["strategie"]).read_text("utf-8"))
    )
    # Die Referenz lief mit genau den Setzungen dieser Strategie.
    assert referenz_setzungen(strategie) == kopf["setzungen"]
    store = StimmenStore.from_verzeichnis(str(mini_store))
    kurse = lade_kurse(str(mini_store / "kurse.parquet"))

    schnell = ergebnis_dict(
        strategie, simuliere(store, kurse, strategie, kopf["von"], kopf["bis"])
    )
    gefuellt = [o for o in schnell["ergebnis"]["orders"] if o["status"] == "filled"]
    assert len(gefuellt) >= 10, "Die Stichprobe muss handeln, sonst prueft sie nichts"
    return vergleiche(schnell, voll, n_tage=N_TAGE)


def test_stichprobe_innerhalb_toleranz(mini_store):
    """Ein Slot: nie zwei Positionen zugleich, nie zwei Kaeufe in einem Zyklus.

    Gemessen (Walkthrough PR_4862): 17 gleiche Trades, Mengen gleich, 0 bp. Faellt dieser
    Test, ist die schnelle Sim gegenueber der eingefrorenen Referenz gedriftet.
    """
    bericht = _bericht(mini_store, "voll_referenz.json")

    assert bericht.innerhalb_toleranz, bericht
    assert bericht.trade_quote == 1.0
    assert all(m[3] == 0.0 for m in bericht.mengen_abweichung)


def test_auslieferung_bekannte_luecke_bleibt_sichtbar(mini_store):
    """Auslieferung (20 Slots): gemessen AUSSERHALB der Toleranz — festgehalten, nicht gelockert.

    Zwei Ursachen, beide gemessen (Walkthrough PR_4862):
      * Am 29.12. kauft die volle Sim AAA und DDD im selben Zyklus; die schnelle Sim sperrt
        DDD per Pacing-Cooldown und kauft es einen Tag spaeter.
      * Ab dem zweiten gehaltenen Titel bemisst die volle Sim rund 5 % kleiner (Cash geteilt
        durch alle 20 Slots statt durch die freien).
    Wird eine der beiden Luecken geschlossen, faellt dieser Test — dann die Referenz neu
    erzeugen und den Befund im Walkthrough nachziehen.
    """
    bericht = _bericht(mini_store, "voll_referenz_auslieferung.json")

    assert not bericht.innerhalb_toleranz
    assert bericht.ursachen == (
        ("2025-12-29", "DDD", "BUY", "buy_pacing_cooldown"),
        ("2025-12-30", "DDD", "BUY", "konsens"),
    )
    assert 1.0 < bericht.max_tagesrendite_bp < 3.0
    assert bericht.endbilanz_bp < N_TAGE * 1.0
