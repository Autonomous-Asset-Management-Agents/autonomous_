"""Datenvorbereitung beim Nutzer (#4634, STW-8; ADR-027 Regeln 8 und 9).

``python -m core.sim.vorbereitung --korpus <ordner> [--symbols A,B,...]`` — nur als
Subprozess, nie im Engine-Prozess (Epic-NFR „Isolation"). Zwei Phasen:
1. ``kurse``: Tageskurse ``heute − 3 Jahre … letzter abgeschlossener Handelstag`` ueber die
   **eigenen** Alpaca-Keys des Nutzers, fortsetzbar und inkrementell; danach ``korpus.json``.
2. ``stimmen``: je Handelstag die Stimmen ueber einen ``StimmenRechner`` als Teil-Datei
   ``stimmen/teile/<tag>.parquet``, am Ende zusammengefuehrt zu ``stimmen/decisions.parquet``,
   ``votes.parquet`` und ``kurse.parquet`` (Eingabe fuer ``core.sim.stimmen_sim``, #3712).
Abbruch: SIGTERM/SIGINT oder Stop-Datei ``ABBRUCH``. Exit-Codes: 0 fertig, 1 Fehler, 2 keine Keys, 3 Stop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from core.sim import fetch

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_FEHLER, EXIT_KEINE_KEYS, EXIT_ABGEBROCHEN = 0, 1, 2, 3
STOP_DATEI = "ABBRUCH"
KALENDER = "SPY"
JAHRE = 3


def letzter_handelstag(heute: date) -> str:
    """Letzter abgeschlossener US-Handelstag ``<= gestern`` (Wochenende uebersprungen)."""
    tag = heute - timedelta(days=1)
    while tag.weekday() >= 5:
        tag -= timedelta(days=1)
    return tag.isoformat()


def _heute_new_york() -> date:
    from zoneinfo import ZoneInfo

    from core.sim.clock import engine_now

    return engine_now(ZoneInfo("America/New_York")).date()


def lade_keys() -> Optional[Tuple[str, str]]:
    """Alpaca-Keys des Nutzers: Umgebung, sonst Schluesselbund (``core.keychain``)."""
    from core import keychain

    try:
        keychain.load_secrets_from_keychain()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "vorbereitung: Schluesselbund nicht lesbar (%s) — nur Umgebung.",
            exc,
        )
    k = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
    s = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    return (k, s) if k and s else None


def _tage(pfad: Path) -> List[str]:
    idx = pd.DatetimeIndex(pd.read_parquet(pfad, columns=[]).index)
    return sorted({t.strftime("%Y-%m-%d") for t in idx})


def handelstage(korpus_dir: Path, symbole: Sequence[str]) -> List[str]:
    """Der Handelskalender des Korpus: die Tage von SPY, ersatzweise die Vereinigung."""
    korpus_dir = Path(korpus_dir)
    spy = korpus_dir / f"{KALENDER}.parquet"
    if spy.exists():
        return _tage(spy)
    logger.warning(
        "vorbereitung: %s fehlt im Korpus — Kalender aus der Vereinigung der Titel.",
        KALENDER,
    )
    return sorted(
        {
            t
            for s in symbole
            if (korpus_dir / f"{s}.parquet").exists()
            for t in _tage(korpus_dir / f"{s}.parquet")
        }
    )


def _sha256(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def schreibe_manifest(
    korpus_dir: Path, universum: Sequence[str], von: str, bis: str
) -> dict:
    """``korpus.json``: Zeitraum, je Titel Bars/Luecken/SHA-256, Grenzen als Klartext."""
    korpus_dir = Path(korpus_dir)
    kalender = handelstage(korpus_dir, universum)
    titel, fehlend = {}, []
    for s in universum:
        p = korpus_dir / f"{s}.parquet"
        if not p.exists():
            fehlend.append(s)
            continue
        tage = _tage(p)
        erster, letzter = tage[0], tage[-1]
        vorhanden = set(tage)
        titel[s] = {
            "erster_bar": erster,
            "letzter_bar": letzter,
            "anzahl_bars": len(tage),
            "fehlende_tage": [
                t for t in kalender if erster <= t <= letzter and t not in vorhanden
            ],
            "sha256": _sha256(p),
        }
    grenzen = [
        {
            "art": "survivorship",
            "text": "Das Universum besteht aus heutigen Titeln; zwischenzeitlich entfernte oder untergegangene Titel fehlen.",
        },
        {
            "art": "quelle",
            "text": "Alpaca IEX-Feed statt konsolidiertem Tape; Volumen und einzelne Kurse koennen abweichen.",
        },
        {
            "art": "fehlende_titel",
            "text": "Ohne Kursdaten: " + (", ".join(fehlend) if fehlend else "keine"),
        },
    ]
    alt = fetch.lies_manifest(korpus_dir)
    manifest = {
        "quelle": "alpaca-iex",
        "zeitraum": {"von": von, "bis": bis},
        "universum": list(universum),
        "kalender": KALENDER,
        "titel": titel,
        "fehlende_titel": fehlend,
        "grenzen": grenzen,
        "abgefragt": alt.get("abgefragt", {}),
    }
    fetch.schreibe_manifest_atomar(korpus_dir, manifest)
    return json.loads((korpus_dir / fetch.MANIFEST).read_text(encoding="utf-8"))


def _melde(**zeile: Any) -> None:
    print(json.dumps(zeile, ensure_ascii=False), flush=True)


def _zusammenfuehren(korpus_dir: Path, symbole: Sequence[str]) -> None:
    ziel = korpus_dir / "stimmen"
    teile = sorted((ziel / "teile").glob("*.parquet"))
    lang = pd.concat([pd.read_parquet(p) for p in teile], ignore_index=True)
    lang = lang.sort_values(["ts", "symbol", "agent"], kind="mergesort")
    entscheidungen = lang[["symbol", "ts"]].drop_duplicates().reset_index(drop=True)
    entscheidungen.insert(0, "did", range(len(entscheidungen)))
    stimmen = lang.merge(entscheidungen, on=["symbol", "ts"])[
        ["did", "agent", "score", "weight", "vetoed"]
    ]
    fetch.schreibe_atomar(ziel / "decisions.parquet", entscheidungen.to_parquet)
    fetch.schreibe_atomar(ziel / "votes.parquet", stimmen.to_parquet)

    kurse = []
    for s in symbole:
        p = korpus_dir / f"{s}.parquet"
        if p.exists():
            df_p = pd.read_parquet(p)
            col = "close" if "close" in df_p.columns else "Close"
            c = df_p[col].astype(float)
            idx = pd.DatetimeIndex(c.index).strftime("%Y-%m-%d")
            kurse.append(pd.DataFrame({"tag": idx, "symbol": s, "close": c.to_numpy()}))
    tabelle = (
        pd.concat(kurse, ignore_index=True)
        if kurse
        else pd.DataFrame(columns=["tag", "symbol", "close"])
    )
    fetch.schreibe_atomar(ziel / "kurse.parquet", tabelle.to_parquet)


def _setze_stand(korpus_dir: Path, stand: Mapping[str, Any]) -> None:
    """Weicht ``stimmen/stand.json`` ab, gilt nichts Gerechnetes mehr (#5071).

    Erst loeschen, dann den Stand schreiben: Ein Abbruch dazwischen laesst den alten Stand
    stehen, und der naechste Lauf loescht erneut.
    """
    from core.sim.stimmen_rechner import STAND_DATEI, lies_stand

    ziel = korpus_dir / "stimmen"
    alt = lies_stand(ziel)
    if alt == dict(stand):
        return
    veraltet = [
        *(ziel / "teile").glob("*.parquet"),
        *(ziel / f"{n}.parquet" for n in ("decisions", "votes", "kurse")),
    ]
    veraltet = [p for p in veraltet if p.exists()]
    if veraltet:
        logger.warning(
            "vorbereitung: Stand %s -> %s, %d Datei(en) verworfen, alle Handelstage neu.",
            alt,
            dict(stand),
            len(veraltet),
        )
    for pfad in veraltet:
        pfad.unlink()
    text = json.dumps(dict(stand), sort_keys=True)
    fetch.schreibe_atomar(
        ziel / STAND_DATEI, lambda p: p.write_text(text, encoding="utf-8")
    )


def berechne_stimmen(
    korpus_dir: Path,
    symbole: Sequence[str],
    rechner: Any,
    abbruch: Callable[[], bool] = lambda: False,
    stand: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Phase ``stimmen``: je fehlendem Handelstag eine Teil-Datei, dann Zusammenfuehrung.

    ``stand``: siehe ``stimmen_rechner.stand``. Ohne ihn zaehlt jede vorhandene Teil-Datei.
    """
    korpus_dir = Path(korpus_dir)
    teile = korpus_dir / "stimmen" / "teile"
    teile.mkdir(parents=True, exist_ok=True)
    for verwaist in teile.glob("*.tmp"):
        verwaist.unlink()
    if stand is not None:
        _setze_stand(korpus_dir, stand)
    tage = handelstage(korpus_dir, symbole)
    leer = []
    for i, tag in enumerate(tage, start=1):
        teil = teile / f"{tag}.parquet"
        if not teil.exists():
            if abbruch():
                return False
            dec, vot = rechner.berechne(korpus_dir, tag, list(symbole))
            if dec.empty:
                leer.append(tag)
            lang = vot.merge(dec, on="did")[
                ["symbol", "ts", "agent", "score", "weight", "vetoed"]
            ]
            fetch.schreibe_atomar(teil, lang.to_parquet)
        _melde(phase="stimmen", fertig=i, gesamt=len(tage), tag=tag)
    if leer:
        logger.warning(
            "vorbereitung: %d Handelstag(e) ohne Stimmen, erster %s, letzter %s — keine Stimmen.",
            len(leer),
            leer[0],
            leer[-1],
        )
    if tage:
        _zusammenfuehren(korpus_dir, symbole)
    return True


class _Abbruch:
    """Kooperativer Abbruch: Signal oder Stop-Datei; die Stop-Datei wird verbraucht."""

    def __init__(self, korpus_dir: Path):
        self._stop = korpus_dir / STOP_DATEI
        self.signal = False

    def __call__(self) -> bool:
        if self._stop.exists():
            self._stop.unlink()
            self.signal = True
        return self.signal


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    client: Any = None,
    rechner: Any = None,
    heute: Optional[date] = None,
) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.sim.vorbereitung")
    ap.add_argument("--korpus", required=True, help="Korpus-Ordner im Datenverzeichnis")
    ap.add_argument("--symbols", help="Komma-Liste; Standard fetch.DEFAULT_UNIVERSE")
    a = ap.parse_args(argv)

    keys = lade_keys()
    if keys is None:
        print("keine Alpaca-Keys", file=sys.stderr)
        return EXIT_KEINE_KEYS

    korpus_dir = Path(a.korpus)
    korpus_dir.mkdir(parents=True, exist_ok=True)
    universum = (
        [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
        if a.symbols
        else list(fetch.DEFAULT_UNIVERSE)
    )
    heute = heute or _heute_new_york()
    bis = letzter_handelstag(heute)
    von = (pd.Timestamp(heute) - pd.DateOffset(years=JAHRE)).strftime("%Y-%m-%d")
    abbruch = _Abbruch(korpus_dir)

    alte = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            alte[sig] = signal.signal(sig, lambda *_: setattr(abbruch, "signal", True))
    try:
        if client is None:  # pragma: no cover
            from alpaca.data.historical import StockHistoricalDataClient

            client = StockHistoricalDataClient(*keys)
        laden = universum + ([KALENDER] if KALENDER not in universum else [])
        for i, s in enumerate(laden, start=1):
            if abbruch():
                return EXIT_ABGEBROCHEN
            fetch.fetch_corpus_from_alpaca(
                str(korpus_dir), [s], von, bis, client=client
            )
            _melde(phase="kurse", fertig=i, gesamt=len(laden), symbol=s)
        schreibe_manifest(korpus_dir, universum, von, bis)

        if rechner is None:
            from core.sim.stimmen_rechner import OfflineStimmenRechner

            rechner = OfflineStimmenRechner()
        stand = rechner.stand() if hasattr(rechner, "stand") else None
        if not berechne_stimmen(korpus_dir, universum, rechner, abbruch, stand=stand):
            return EXIT_ABGEBROCHEN
        _melde(phase="fertig")
        return EXIT_OK
    except Exception:  # noqa: BLE001
        logger.exception("vorbereitung: Lauf fehlgeschlagen.")
        return EXIT_FEHLER
    finally:
        for sig, alt in alte.items():
            signal.signal(sig, alt)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    sys.exit(main())
