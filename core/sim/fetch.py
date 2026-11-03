"""Sim-Day corpus builder (#2548) — the sustainable data pipeline.

Populates a **pinned, offline** corpus of ``{SYMBOL}.parquet`` daily bars once, so every
``python -m core.sim`` run is offline + reproducible + real (pro-tool pattern: fetch once, run
offline forever). Two sources:

  * ``build_corpus_from_cache`` — import from an existing ``market_data_cache`` (real bars the
    desktop already downloaded) → no creds needed.
  * ``fetch_corpus_from_alpaca`` — download via the Alpaca data client (free IEX feed) when
    creds are present; resumable and incremental since #4634.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# A focused, liquid default universe for a fast first run (override with --symbols).
DEFAULT_UNIVERSE: List[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "JPM",
    "V",
    "XOM",
    "UNH",
    "JNJ",
    "WMT",
    "PG",
    "HD",
    "MA",
    "AVGO",
    "COST",
    "MRK",
    "ABBV",
    "PEP",
    "KO",
    "ADBE",
    "CRM",
    "NFLX",
    "AMD",
    "INTC",
    "CSCO",
    "QCOM",
    "TXN",
]


def _default_desktop_cache() -> Optional[str]:
    """The installed desktop's market_data_cache (real bars), if present."""
    p = os.path.expandvars(
        r"%LOCALAPPDATA%\autonomous\app\ai_trading_bot\market_data_cache"
    )
    return p if os.path.isdir(p) else None


def build_corpus_from_cache(
    out_dir: str,
    cache_dir: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    up_to_date: Optional[str] = None,
) -> int:
    """Build a pinned corpus in ``out_dir`` from an existing daily-bar cache.

    ``up_to_date`` (YYYY-MM-DD) truncates every frame to bars ``<= that date`` so the corpus is
    pinned to the sim day + warm-up (reproducible; no future bars). Returns the symbol count.
    """
    cache_dir = cache_dir or _default_desktop_cache()
    if not cache_dir or not os.path.isdir(cache_dir):
        raise FileNotFoundError(
            f"No bar cache found (cache_dir={cache_dir!r}). Pass --cache-dir or set up the desktop."
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    syms = [s.upper() for s in (symbols or DEFAULT_UNIVERSE)]
    cutoff = pd.Timestamp(up_to_date) if up_to_date else None

    written = 0
    for s in syms:
        src = Path(cache_dir) / f"{s}.parquet"
        if not src.exists():
            logger.warning("corpus: %s not in cache — skipped.", s)
            continue
        if cutoff is None:
            shutil.copyfile(src, out / f"{s}.parquet")
        else:
            df = pd.read_parquet(src)
            idx = df.index
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_localize(None)
                df.index = idx
            # keep bars on/before the cutoff DATE (compare on date, not intraday stamp)
            df = df[pd.DatetimeIndex(df.index).normalize() <= cutoff.normalize()]
            if df.empty:
                logger.warning("corpus: %s has no bars <= %s — skipped.", s, up_to_date)
                continue
            df.to_parquet(out / f"{s}.parquet")
        written += 1
    logger.info(
        "corpus: wrote %d symbols to %s (up_to=%s).", written, out_dir, up_to_date
    )
    return written


#: #4634: Manifest im Korpus-Verzeichnis; ``fetch_corpus_from_alpaca`` pflegt darin je Symbol
#: das abgefragte Enddatum (``abgefragt``), ``core.sim.vorbereitung`` den Rest.
MANIFEST = "korpus.json"


def lies_manifest(out: Path) -> dict:
    """Das Manifest des Korpus, leer wenn es noch keins gibt."""
    pfad = out / MANIFEST
    if not pfad.exists():
        return {}
    return json.loads(pfad.read_text(encoding="utf-8"))


def schreibe_atomar(pfad: Path, schreiben: Callable[[Path], None]) -> None:
    """Schreibt ueber ``<pfad>.tmp`` und ``os.replace`` — ein Abbruch hinterlaesst nie eine
    halbe Zieldatei, hoechstens eine verwaiste ``.tmp``, die der naechste Lauf entfernt.
    """
    tmp = pfad.with_name(pfad.name + ".tmp")
    schreiben(tmp)
    os.replace(tmp, pfad)


def schreibe_manifest_atomar(out: Path, manifest: dict) -> None:
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    schreibe_atomar(out / MANIFEST, lambda p: p.write_text(text, encoding="utf-8"))


def _ohne_tz(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        df = df.copy()
        df.index = idx.tz_convert("UTC").tz_localize(None)
    return df


def _alpaca_client() -> Any:  # pragma: no cover - braucht echte Keys
    from alpaca.data.historical import StockHistoricalDataClient

    key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
    sec = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError(
            "Alpaca data creds not set (APCA_API_KEY_ID / APCA_API_SECRET_KEY)."
        )
    return StockHistoricalDataClient(key, sec)


def fetch_corpus_from_alpaca(
    out_dir: str,
    symbols: List[str],
    start: str,
    end: str,
    *,
    client: Any = None,
    fortsetzen: bool = True,
) -> int:
    """Daily-bar download via the Alpaca data client (free IEX feed). Returns the count of
    symbols that have a corpus file afterwards.

    #4634: ``client`` injizierbar (Tests, Nutzer-Keys aus ``core.sim.vorbereitung``); ohne
    ihn wie bisher aus der Umgebung. Mit ``fortsetzen`` wird ein Symbol nicht neu angefragt,
    wenn das Manifest die Abfrage bis ``end`` vermerkt oder sein letzter Bar auf/nach ``end``
    liegt; sonst nur ``letzter Bar + 1 Tag … end``, angehaengt und nach Index entdoppelt.
    Jede Datei wird atomar geschrieben.
    """
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for verwaist in out.glob("*.tmp"):
        logger.warning("corpus: verwaiste Teildatei %s entfernt.", verwaist.name)
        verwaist.unlink()
    if client is None:
        client = _alpaca_client()
    end_tag = pd.Timestamp(end).normalize()

    written = 0
    for s in symbols:
        ziel = out / f"{s}.parquet"
        alt = (
            _ohne_tz(pd.read_parquet(ziel)) if (fortsetzen and ziel.exists()) else None
        )
        von = pd.Timestamp(start).normalize()
        if alt is not None and not alt.empty:
            letzter = pd.Timestamp(alt.index.max()).normalize()
            vermerkt = lies_manifest(out).get("abgefragt", {}).get(s)
            if letzter >= end_tag or (
                vermerkt and pd.Timestamp(vermerkt).normalize() >= end_tag
            ):
                written += 1
                continue
            von = letzter + pd.Timedelta(days=1)
        req = StockBarsRequest(
            symbol_or_symbols=s,
            timeframe=TimeFrame.Day,
            start=von.to_pydatetime(),
            end=end_tag.to_pydatetime(),
            feed=DataFeed.IEX,
        )
        df = client.get_stock_bars(req).df
        if df is not None and not df.empty:
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(s)
            df = _ohne_tz(df)
            if alt is not None and not alt.empty:
                df = pd.concat([alt, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
            schreibe_atomar(ziel, df.to_parquet)
        elif alt is None or alt.empty:
            logger.warning(
                "corpus: %s ohne Bars von Alpaca (%s … %s) — kein Titel im Korpus.",
                s,
                von.date(),
                end_tag.date(),
            )
        manifest = lies_manifest(out)
        manifest.setdefault("abgefragt", {})[s] = end_tag.strftime("%Y-%m-%d")
        schreibe_manifest_atomar(out, manifest)
        if ziel.exists():
            written += 1
    return written
