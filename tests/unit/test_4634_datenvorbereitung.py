"""#4634 STW-8: Datenpipeline beim Nutzer — Kurse ueber eigene Keys, Stimmen einmal berechnen.

Plan: ``docs/4634-stw-8-feat-sim-datenpipeline-beim-nutzer-3-jahre/implementation_plan.md``
(TDD-Reihenfolge 1-12). Alles in ``tmp_path``, ohne Netz: ein Fake-Client zaehlt die
Aufrufe von ``get_stock_bars``.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.sim import fetch, vorbereitung

pytestmark = [pytest.mark.vc1]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
SYMBOLE_30 = list(fetch.DEFAULT_UNIVERSE)


def _bars(symbol: str, von: str, bis: str) -> pd.DataFrame:
    """Tagesbars im Alpaca-Format: MultiIndex (symbol, timestamp), UTC, Werktage."""
    tage = pd.bdate_range(von, bis, tz="UTC")
    if len(tage) == 0:
        return pd.DataFrame()
    basis, i = 100.0 + (sum(map(ord, symbol)) % 50), np.arange(len(tage))
    close = basis * np.exp(0.0005 * i) * (1.0 + 0.02 * np.sin(i / 7.0))
    cols = {
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": 1e6,
    }
    idx = pd.MultiIndex.from_product([[symbol], tage], names=["symbol", "timestamp"])
    return pd.DataFrame(cols, index=idx)


class FakeClient:
    """``get_stock_bars`` wie Alpaca; zaehlt Aufrufe, merkt sich die Anfragen."""

    def __init__(self, abbruch_nach=None, beim_aufruf=None):
        self.aufrufe, self.anfragen = 0, []
        self._abbruch_nach, self._beim_aufruf = abbruch_nach, beim_aufruf

    def get_stock_bars(self, req):
        if self._abbruch_nach is not None and self.aufrufe >= self._abbruch_nach:
            raise KeyboardInterrupt("Abbruch simuliert")
        self.aufrufe += 1
        self.anfragen.append(req)
        if self._beim_aufruf:
            self._beim_aufruf(self.aufrufe)
        von = pd.Timestamp(req.start).strftime("%Y-%m-%d")
        bis = pd.Timestamp(req.end).strftime("%Y-%m-%d")
        return type("_Antwort", (), {"df": _bars(req.symbol_or_symbols, von, bis)})()


# --- 1 ---------------------------------------------------------------------------


def test_fetch_nutzt_injizierten_client_und_schreibt_parquet(tmp_path):
    c = FakeClient()
    n = fetch.fetch_corpus_from_alpaca(
        str(tmp_path), ["AAPL", "MSFT"], "2026-01-01", "2026-01-31", client=c
    )
    assert n == 2 and c.aufrufe == 2
    df = pd.read_parquet(tmp_path / "AAPL.parquet")
    assert not isinstance(df.index, pd.MultiIndex)
    assert pd.Timestamp(df.index.max()).strftime("%Y-%m-%d") == "2026-01-30"
    assert "close" in df.columns


# --- 2 ---------------------------------------------------------------------------


def test_fetch_fortsetzen_laedt_nur_fehlende_symbole(tmp_path):
    args = (str(tmp_path), SYMBOLE_30, "2026-01-01", "2026-01-30")
    with pytest.raises(KeyboardInterrupt):
        fetch.fetch_corpus_from_alpaca(*args, client=FakeClient(abbruch_nach=10))
    assert len(list(tmp_path.glob("*.parquet"))) == 10
    c2, c3 = FakeClient(), FakeClient()
    fetch.fetch_corpus_from_alpaca(*args, client=c2)
    assert c2.aufrufe == 20
    fetch.fetch_corpus_from_alpaca(*args, client=c3)
    assert c3.aufrufe == 0


def test_fetch_fortsetzen_bei_wochenende_als_enddatum(tmp_path):
    args = (str(tmp_path), ["AAPL"], "2026-01-01", "2026-01-31")
    fetch.fetch_corpus_from_alpaca(*args, client=FakeClient())
    c2 = FakeClient()
    fetch.fetch_corpus_from_alpaca(*args, client=c2)
    assert c2.aufrufe == 0


# --- 3 ---------------------------------------------------------------------------


def test_fetch_inkrementell_laedt_nur_neue_tage(tmp_path):
    fetch.fetch_corpus_from_alpaca(
        str(tmp_path), ["AAPL"], "2026-01-01", "2026-01-29", client=FakeClient()
    )
    c2 = FakeClient()
    fetch.fetch_corpus_from_alpaca(
        str(tmp_path), ["AAPL"], "2026-01-01", "2026-01-30", client=c2
    )
    assert c2.aufrufe == 1
    assert pd.Timestamp(c2.anfragen[0].start).strftime("%Y-%m-%d") == "2026-01-30"
    assert pd.Timestamp(c2.anfragen[0].end).strftime("%Y-%m-%d") == "2026-01-30"
    df = pd.read_parquet(tmp_path / "AAPL.parquet")
    assert df.index.is_unique and df.index.is_monotonic_increasing
    assert len(df) == len(pd.bdate_range("2026-01-01", "2026-01-30"))


def test_enddatum_wird_auf_letzten_handelstag_normiert():
    assert vorbereitung.letzter_handelstag(date(2026, 2, 2)) == "2026-01-30"
    assert vorbereitung.letzter_handelstag(date(2026, 1, 28)) == "2026-01-27"


# --- 4 ---------------------------------------------------------------------------


def test_fetch_schreibt_atomar(tmp_path, monkeypatch):
    def kaputt(self, pfad, *a, **k):
        Path(pfad).write_bytes(b"halb")
        raise OSError("Platte voll")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", kaputt)
    with pytest.raises(OSError):
        fetch.fetch_corpus_from_alpaca(
            str(tmp_path), ["AAPL"], "2026-01-01", "2026-01-30", client=FakeClient()
        )
    assert (
        not (tmp_path / "AAPL.parquet").exists()
        and (tmp_path / "AAPL.parquet.tmp").exists()
    )
    monkeypatch.undo()
    fetch.fetch_corpus_from_alpaca(
        str(tmp_path), ["MSFT"], "2026-01-01", "2026-01-30", client=FakeClient()
    )
    assert not (tmp_path / "AAPL.parquet.tmp").exists()


# --- 5 ---------------------------------------------------------------------------


def test_manifest_enthaelt_zeitraum_luecken_und_grenzen(tmp_path):
    fetch.fetch_corpus_from_alpaca(
        str(tmp_path), ["SPY", "AAPL"], "2026-01-01", "2026-01-30", client=FakeClient()
    )
    df = pd.read_parquet(tmp_path / "AAPL.parquet")
    df.drop(df.index[5]).to_parquet(tmp_path / "AAPL.parquet")
    m = vorbereitung.schreibe_manifest(
        tmp_path, ["AAPL", "MSFT"], "2026-01-01", "2026-01-30"
    )
    assert json.loads((tmp_path / "korpus.json").read_text("utf-8")) == m
    assert m["quelle"] == "alpaca-iex" and m["zeitraum"] == {
        "von": "2026-01-01",
        "bis": "2026-01-30",
    }
    assert m["universum"] == ["AAPL", "MSFT"]
    aapl = m["titel"]["AAPL"]
    assert aapl["erster_bar"] == "2026-01-01" and aapl["letzter_bar"] == "2026-01-30"
    assert aapl["anzahl_bars"] == len(df) - 1
    assert aapl["fehlende_tage"] == [pd.Timestamp(df.index[5]).strftime("%Y-%m-%d")]
    assert len(aapl["sha256"]) == 64 and m["fehlende_titel"] == ["MSFT"]
    assert {"survivorship", "quelle", "fehlende_titel"} <= {
        g["art"] for g in m["grenzen"]
    }


# --- 6 ---------------------------------------------------------------------------


def _ohne_env_keys(monkeypatch):
    for k in (
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
    ):
        monkeypatch.delenv(k, raising=False)


def test_keys_aus_schluesselbund(monkeypatch):
    _ohne_env_keys(monkeypatch)
    monkeypatch.setattr(
        "core.keychain.load_secrets_from_keychain",
        lambda: monkeypatch.setenv("ALPACA_API_KEY", "k-aus-bund")
        or monkeypatch.setenv("ALPACA_SECRET_KEY", "s-aus-bund")
        or {"ALPACA_API_KEY": "(from keychain)"},
    )
    assert vorbereitung.lade_keys() == ("k-aus-bund", "s-aus-bund")


def test_umgebung_hat_vorrang_vor_schluesselbund(monkeypatch):
    _ohne_env_keys(monkeypatch)
    monkeypatch.setenv("APCA_API_KEY_ID", "k-env")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s-env")
    monkeypatch.setattr(
        "core.keychain.load_secrets_from_keychain",
        lambda: monkeypatch.setenv("ALPACA_API_KEY", "k-bund") or {},
    )
    assert vorbereitung.lade_keys() == ("k-env", "s-env")


def test_ohne_keys_exit_2(tmp_path, monkeypatch, capsys):
    _ohne_env_keys(monkeypatch)
    monkeypatch.setattr("core.keychain.load_secrets_from_keychain", lambda: {})
    assert vorbereitung.main(["--korpus", str(tmp_path)]) == 2
    assert "keine Alpaca-Keys" in capsys.readouterr().err


def test_ohne_keys_exit_2_als_subprozess(tmp_path):
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("APCA_", "ALPACA_"))
    }
    env["PYTEST_CURRENT_TEST"] = "subprozess"
    cmd = [sys.executable, "-m", "core.sim.vorbereitung", "--korpus", str(tmp_path)]
    lauf = subprocess.run(
        cmd, cwd=PAKET, env=env, capture_output=True, text=True, timeout=300
    )
    assert lauf.returncode == 2 and "keine Alpaca-Keys" in lauf.stderr


# --- 7 ---------------------------------------------------------------------------


class FakeRechner:
    """Ein ``StimmenRechner``: eine Stimme je Symbol, zaehlt die berechneten Tage."""

    def __init__(self):
        self.tage = []

    def berechne(self, korpus_dir, tag, symbole):
        self.tage.append(tag)
        dec = pd.DataFrame(
            [
                {"did": i, "symbol": s, "ts": f"{tag}T20:00:00+00:00"}
                for i, s in enumerate(symbole)
            ]
        )
        vot = pd.DataFrame(
            [
                {
                    "did": i,
                    "agent": "MomentumAgent",
                    "score": 0.6,
                    "weight": 0.45,
                    "vetoed": False,
                }
                for i in range(len(symbole))
            ]
        )
        return dec, vot


def _korpus(tmp_path, symbole=("SPY", "AAPL", "MSFT"), bis="2026-01-09"):
    fetch.fetch_corpus_from_alpaca(
        str(tmp_path), list(symbole), "2026-01-01", bis, client=FakeClient()
    )


def test_stimmen_teile_je_tag_und_fortsetzen(tmp_path):
    _korpus(tmp_path)
    tage = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-01", "2026-01-09")]
    erster = FakeRechner()
    vorbereitung.berechne_stimmen(tmp_path, ["AAPL", "MSFT"], erster)
    assert erster.tage == tage
    assert (
        sorted(p.stem for p in (tmp_path / "stimmen" / "teile").glob("*.parquet"))
        == tage
    )
    _korpus(tmp_path, bis="2026-01-12")
    zweiter = FakeRechner()
    vorbereitung.berechne_stimmen(tmp_path, ["AAPL", "MSFT"], zweiter)
    assert zweiter.tage == ["2026-01-12"]


# --- 8 ---------------------------------------------------------------------------


def test_offline_stimmen_rechner_und_zusammenfuehrung_lesbar_fuer_replay_vote_store(
    tmp_path,
):
    from core.sim.stimmen_rechner import OfflineStimmenRechner, StimmenRechner
    from core.sim.stimmen_sim import StimmenStore
    from research.signal_replay import ReplayVoteStore

    _korpus(tmp_path, symbole=("SPY", "AAPL"), bis="2027-12-31")
    rechner = OfflineStimmenRechner()
    assert isinstance(rechner, StimmenRechner)
    d1, v1 = rechner.berechne(tmp_path, "2027-12-31", ["AAPL"])
    d2, v2 = OfflineStimmenRechner().berechne(tmp_path, "2027-12-31", ["AAPL"])
    pd.testing.assert_frame_equal(d1, d2)
    pd.testing.assert_frame_equal(v1, v2)
    assert {"MomentumAgent", "TrendAgent"} <= set(v1["agent"])
    assert v1["score"].between(0.0, 1.0).all()
    assert float(v1.loc[v1["agent"] == "MomentumAgent", "score"].iloc[0]) > 0.5

    # Am ersten Bar stimmt nur, was keine Kurshistorie braucht (#5076).
    d0, v0 = rechner.berechne(tmp_path, "2026-01-05", ["AAPL"])
    assert len(d0) == 1
    assert set(v0["agent"]) <= {"FundamentalsAgent", "ValuationAgent"}
    assert "FundamentalsAgent" in set(v0["agent"])

    vorbereitung.berechne_stimmen(tmp_path, ["AAPL"], rechner)
    st = tmp_path / "stimmen"
    p_dec, p_vot = str(st / "decisions.parquet"), str(st / "votes.parquet")
    assert ReplayVoteStore.from_parquet(p_dec, p_vot) is not None
    assert StimmenStore.from_verzeichnis(str(st)).stimmen("AAPL", "2027-12-31")
    assert pd.read_parquet(p_dec)["did"].is_unique
    assert list(pd.read_parquet(st / "kurse.parquet").columns) == [
        "tag",
        "symbol",
        "close",
    ]


# --- 9 ---------------------------------------------------------------------------


def test_abbruch_per_stopdatei_exit_3(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr("core.keychain.load_secrets_from_keychain", lambda: {})
    c = FakeClient(
        beim_aufruf=lambda n: (
            (tmp_path / "ABBRUCH").write_text("", "utf-8") if n == 3 else None
        )
    )
    rc = vorbereitung.main(
        ["--korpus", str(tmp_path), "--symbols", "AAPL,MSFT,NVDA,AMZN"],
        client=c,
        rechner=FakeRechner(),
        heute=date(2026, 1, 28),
    )
    assert rc == 3 and c.aufrufe == 3
    assert len(list(tmp_path.glob("*.parquet"))) == 3
    assert not list(tmp_path.glob("*.tmp")) and not (tmp_path / "ABBRUCH").exists()


# --- 10 --------------------------------------------------------------------------


def test_fortschritt_json_zeilen(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr("core.keychain.load_secrets_from_keychain", lambda: {})
    rc = vorbereitung.main(
        ["--korpus", str(tmp_path), "--symbols", "AAPL,MSFT"],
        client=FakeClient(),
        rechner=FakeRechner(),
        heute=date(2026, 1, 28),
    )
    assert rc == 0
    z = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    k = [x for x in z if x["phase"] == "kurse"]
    assert [x["symbol"] for x in k] == ["AAPL", "MSFT", "SPY"]
    assert k[-1] == {"phase": "kurse", "fertig": 3, "gesamt": 3, "symbol": "SPY"}
    st = [x for x in z if x["phase"] == "stimmen"]
    assert st and st[-1]["fertig"] == st[-1]["gesamt"] and z[-1]["phase"] == "fertig"
    m = json.loads((tmp_path / "korpus.json").read_text("utf-8"))
    assert m["zeitraum"] == {"von": "2023-01-28", "bis": "2026-01-27"}


# --- 11 --------------------------------------------------------------------------


def test_fallback_wird_als_warning_geloggt(tmp_path, caplog):
    class LeererClient(FakeClient):
        def get_stock_bars(self, req):
            self.aufrufe += 1
            return type("_Antwort", (), {"df": pd.DataFrame()})()

    class StummerRechner:
        def berechne(self, korpus_dir, tag, symbole):
            return pd.DataFrame(columns=["did", "symbol", "ts"]), pd.DataFrame(
                columns=["did", "agent", "score", "weight", "vetoed"]
            )

    with caplog.at_level(logging.WARNING):
        fetch.fetch_corpus_from_alpaca(
            str(tmp_path), ["XXXX"], "2026-01-01", "2026-01-09", client=LeererClient()
        )
        _korpus(tmp_path)
        vorbereitung.berechne_stimmen(tmp_path, ["AAPL"], StummerRechner())
    warnungen = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("XXXX" in w for w in warnungen) and any(
        "keine Stimmen" in w for w in warnungen
    )


# --- 12 --------------------------------------------------------------------------


def _modul_importe(pfad: Path) -> set:
    tree = ast.parse(pfad.read_text(encoding="utf-8"))
    res = {a.name for k in tree.body if isinstance(k, ast.Import) for a in k.names}
    res |= {k.module for k in tree.body if isinstance(k, ast.ImportFrom) and k.module}
    return res


_VERBOTEN = ("core.engine", "core.execution", "core.sim.broker", "alpaca.trading")


def _verboten(namen: set) -> list:
    return sorted(
        n for n in namen if any(n == p or n.startswith(p + ".") for p in _VERBOTEN)
    )


def test_kein_import_aus_engine(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("import core.engine.order_executor\n", encoding="utf-8")
    assert _verboten(_modul_importe(probe)) == ["core.engine.order_executor"]
    for modul in ("vorbereitung.py", "stimmen_rechner.py"):
        assert _verboten(_modul_importe(PAKET / "core" / "sim" / modul)) == []
