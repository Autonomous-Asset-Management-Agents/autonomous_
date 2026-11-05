"""#3712 schnelle Sim: bytegleich, Strategien unabhaengig (Plan TDD-Schritt 4)."""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.vc1]

TAGE = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]


def mini_store(verzeichnis):
    """Synthetischer Vote-Store im Format aus #4634 plus Kurse — klein, aber mit Handel."""
    import pandas as pd

    dec, vot, kurse = [], [], []
    did = 0
    for i, tag in enumerate(TAGE):
        for j, sym in enumerate(["AAA", "BBB", "CCC"]):
            did += 1
            dec.append({"did": did, "symbol": sym, "ts": f"{tag}T20:00:00+00:00"})
            lstm = 0.9 if (i + j) % 3 else 0.1
            for agent, score, w in [
                ("LSTMSignalAgent", lstm, 1.0),
                ("MomentumAgent", 0.6, 0.5),
            ]:
                vot.append({"did": did, "agent": agent, "score": score, "weight": w})
                vot[-1]["vetoed"] = False
            kurse.append({"tag": tag, "symbol": sym, "close": 100.0 + 3 * i - 5 * j})
    pd.DataFrame(dec).to_parquet(verzeichnis / "decisions.parquet")
    pd.DataFrame(vot).to_parquet(verzeichnis / "votes.parquet")
    pd.DataFrame(kurse).to_parquet(verzeichnis / "kurse.parquet")
    return verzeichnis


def _strategien(tmp_path):
    """Drei Strategien; Pacing an: sein Kaufgedaechtnis darf nicht zwischen ihnen leben."""
    pfade = []
    for name, gewicht, slots in [("a", 1.0, 2), ("b", 0.1, 3), ("c", 3.0, 1)]:
        s = {
            "name": name,
            "gewichte": {"MomentumAgent": gewicht},
            "max_positions": slots,
        }
        s.update(buy_threshold=0.7, pacing_aktiv=True)
        (tmp_path / f"{name}.json").write_text(json.dumps(s), encoding="utf-8")
        pfade.append(str(tmp_path / f"{name}.json"))
    return pfade


def _lauf(tmp_path, store, strategien, out):
    from core.sim.stimmen_sim import main

    ziel = tmp_path / out
    args = ["--store", str(store), "--von", TAGE[0], "--bis", TAGE[-1]]
    assert main(args + ["--strategien", *strategien, "--out", str(ziel)]) == 0
    return ziel


def test_zwei_laeufe_bytegleich(tmp_path):
    store = mini_store(tmp_path)
    strategien = _strategien(tmp_path)

    eins = _lauf(tmp_path, store, strategien, "eins.json").read_bytes()
    zwei = _lauf(tmp_path, store, strategien, "zwei.json").read_bytes()

    assert eins == zwei
    gefuellt = [
        o
        for s in json.loads(eins)["strategien"]
        for o in s["ergebnis"]["orders"]
        if o["status"] == "filled"
    ]
    assert gefuellt, "Die Stichprobe muss handeln, sonst prueft sie nichts"


def test_drei_strategien_ein_lauf_unabhaengig(tmp_path):
    store = mini_store(tmp_path)
    a, b, c = _strategien(tmp_path)

    allein = json.loads(_lauf(tmp_path, store, [a], "allein.json").read_text("utf-8"))
    zusammen = json.loads(
        _lauf(tmp_path, store, [b, a, c], "zusammen.json").read_text("utf-8")
    )

    nach_name = {s["strategie"]: s for s in zusammen["strategien"]}
    assert nach_name["a"] == allein["strategien"][0]
    # Gleichheit allein reicht nicht: ein geteiltes Kaufgedaechtnis blockt in beiden
    # Laeufen jeden Kauf — gleich, aber falsch. Strategie a muss handeln.
    gefuellt = [
        o for o in nach_name["a"]["ergebnis"]["orders"] if o["status"] == "filled"
    ]
    assert len(gefuellt) >= 2
    assert nach_name["a"]["ergebnis"] != nach_name["c"]["ergebnis"]
