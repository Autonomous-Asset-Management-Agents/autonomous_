"""#4862 Replay-Treiber: Datum, Fenster und Kadenz erreichen die Sim (Plan TDD-Schritt 3)."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.vc1]

# Alles, was main() in os.environ schreibt. Vorab per monkeypatch belegt, damit undo()
# den Ausgangszustand wiederherstellt — main() schreibt direkt in os.environ.
_SCHLUESSEL = (
    "AAA_SIM_MODE SIM_MODE SIM_DATE SIM_DAYS SIM_WINDOW SIM_CADENCE "
    "AAA_SIM_CORPUS_DIR AAA_SIM_DATE AAA_SIM_WINDOW AAA_SIM_CADENCE FULL_UNIVERSE_MAX_POSITIONS"
).split()


def test_treiber_setzt_aaa_sim_variablen(monkeypatch, tmp_path):
    import core.sim.runner as sim_runner
    import research.signal_replay as replay
    from research import run_signal_replay

    for k in _SCHLUESSEL:
        monkeypatch.setenv(k, "vorher")
    gesehen = {}

    def run_sim_ersatz():
        from settings import RuntimeConfigState

        gesehen["env"] = {k: os.environ.get(k) for k in _SCHLUESSEL}
        cfg = RuntimeConfigState()  # frisch: liest die Umgebung wie ein Engine-Boot
        gesehen["cfg"] = (cfg.SIM_DATE, cfg.SIM_WINDOW, cfg.SIM_CADENCE, cfg.SIM_DAYS)
        return SimpleNamespace(daily_equity=[], timed_out=False, halted=False)

    leer = replay.ReplayVoteStore({})  # ein leerer Store statt der Parquet-Dateien
    monkeypatch.setattr(
        replay.ReplayVoteStore, "from_parquet", classmethod(lambda cls, d, v: leer)
    )
    monkeypatch.setattr(replay, "activate_replay", lambda s: None)
    monkeypatch.setattr(sim_runner, "run_sim", run_sim_ersatz)

    out = tmp_path / "v.json"
    rc = run_signal_replay.main(
        [
            "--store",
            str(tmp_path),
            "--corpus",
            str(tmp_path / "korpus"),
            "--date",
            "2026-01-02",
            "--days",
            "5",
            "--out",
            str(out),
            "--window",
            "15:45-16:00 ET",
            "--cadence",
            "15m",
            "--set",
            "FULL_UNIVERSE_MAX_POSITIONS=3",
        ]
    )

    assert rc == 0 and json.loads(out.read_text("utf-8"))["timed_out"] is False
    env = gesehen["env"]
    assert env["AAA_SIM_DATE"] == "2026-01-02"
    assert env["AAA_SIM_WINDOW"] == "15:45-16:00 ET"
    assert env["AAA_SIM_CADENCE"] == "15m"
    # Die alten Namen bleiben (rein additiv).
    assert env["SIM_WINDOW"] == "15:45-16:00 ET" and env["SIM_DAYS"] == "5"
    assert env["FULL_UNIVERSE_MAX_POSITIONS"] == "3"
    # Wirksam: die Config, die die Engine beim Boot liest, sieht genau diese Werte.
    assert gesehen["cfg"] == ("2026-01-02", "15:45-16:00 ET", "15m", 5)
