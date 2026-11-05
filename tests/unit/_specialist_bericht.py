"""#4153 (G-8a) — Charakterisierungsnetz fuer den Specialist-Bericht, vor dem Umzug (G-8a2).

Plan: ``docs/4153-*/implementation_plan.md``.

``StockSpecialistAgent.research()`` laeuft echt, mit allen ``_fetch_*``-Methoden. Ersetzt
sind nur die Aussengrenzen: HTTP (``httpx.AsyncClient``), Redis, Google Trends, die
CIK-Aufloesung, der Kursdaten-Provider, die Uhr, die Modell-Registry, das LSTM-Panel und
der LLM-Aufruf. Was die Quellen liefern, steht je Quelle unter
``tests/fixtures/specialist_bericht/``.

Aufgezeichnet wird je Szenario das ``gathered`` (wie es an ``_build_report`` geht), der
Bericht ohne ``updated_at`` (Wanduhr), die abgefragten URLs und der Prompt. Abweichungen
nennt ``befunde`` mit dem Pfad des Schluessels.

Hermetik: Eine URL ohne Fixture laesst ``fahre`` scheitern, ebenso der Bau der echten
``HistoricalDataProvider``-Klasse. Beides wuerden die Fetcher sonst still schlucken.

Referenz neu schreiben (nur auf unveraendertem ``core/``, der Diff ist der Befund):

    SPECIALIST_CHARAKTERISIERUNG_NEU=1 pytest tests/unit/test_specialist_bericht_charakterisierung.py
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import sys
import tempfile
import types
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from tests.helpers.config_patch import _patch_get_config  # noqa: E402

FIXTURES = _AI_BOT / "tests" / "fixtures" / "specialist_bericht"
REFERENZ = _AI_BOT / "tests" / "fixtures" / "specialist_bericht_vor_g8a.json"
BARS = _AI_BOT / "tests" / "fixtures" / "report" / "nvda_bars.json"
XSEC = _AI_BOT / "tests" / "fixtures" / "report" / "nvda_lstm_xsec.json"

JETZT = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)
POLYGON_KEY = "FIXTURE-KEY"
CIKS = {"NVDA": "0001045810"}

# Ausgelieferte Werte aus ``settings.py`` (Zeile in Klammern), fest statt aus der Umgebung,
# damit kein gesetztes Env das Netz verschiebt. Abweichung vom Ausgelieferten:
# REPORT_GENERATOR_V2_ENABLED (2023, ausgeliefert True) bleibt AUS — er beruehrt keine
# Datenquelle und braucht eigene LLM-Stubs (Plan §2.1), ebenso INSIGHT_QUALITY_ENABLED.
VORGABE = {
    "SIM_MODE": False,  # 148
    "ML_PREDICTION_ENABLED": False,  # 1135
    "ML_SENTIMENT_BLEND_ENABLED": False,  # 1138
    "SPECIALIST_PROMPT_V2": True,  # 1150
    "SPECIALIST_CARDS_ENABLED": True,  # 1159
    "SPECIALIST_GROUNDING_ENABLED": True,  # 1175
    "LLM_OUTPUT_PARITY": False,  # 1430
    "SPECIALIST_NEWS_V2": False,  # 1439
    "DATA_INTEGRITY_GUARD_ENABLED": True,  # 1450
    "SPECIALIST_FORM4_DIRECTION_ENABLED": False,  # 1466
    "INSIGHT_QUALITY_ENABLED": False,  # 2002
    "REPORT_GENERATOR_V2_ENABLED": False,  # 2023, siehe oben
}
SPECIALIST_FLAGS = (
    "ML_PREDICTION_ENABLED",
    "SPECIALIST_GROUNDING_ENABLED",
    "SPECIALIST_NEWS_V2",
    "SPECIALIST_FORM4_DIRECTION_ENABLED",
    "DATA_INTEGRITY_GUARD_ENABLED",
)
ALLE_AN = dict.fromkeys(SPECIALIST_FLAGS, True)
ALLE_AUS = dict.fromkeys(SPECIALIST_FLAGS, False)

# Diese Hosts werfen im Ausfall-Szenario eine Ausnahme, alle anderen antworten 500.
_AUSNAHME_HOSTS = ("wikimedia.org", "www.reddit.com", "api.finra.org")

# Feste LLM-Antwort im V2-Format (der V1-Parser liest dieselben Zeilen).
SYNTHESE = "\n".join(
    [
        "SUMMARY: NVDA data-center revenue hit a record; insiders sold shares.",
        "SIGNALS: Reddit buzz and a Wikipedia spike point to heavy retail interest.",
        "OUTLOOK: bullish",
        "SCORE: 68",
        "COMPANY: NVIDIA designs GPUs and AI data-center systems.",
        "BULL: NVDA data-center revenue hit a record as Blackwell ramps [H1].",
        "BEAR: The US weighs new export curbs on NVDA AI chips to China [H2].",
        "THESIS: NVDA stays the AI compute leader while export risk caps upside [H1][H2].",
        "REASONS:",
        "- Record data-center revenue",
        "- Insider selling after the run-up",
        "- Export-curb risk",
    ]
)


@dataclass(frozen=True)
class Szenario:
    name: str
    symbol: str
    flags: dict = field(default_factory=dict)  # ueberschreibt VORGABE
    ausfall: bool = False  # jede HTTP-Antwort 500 bzw. Ausnahme, Trends wirft


SZENARIEN: tuple[Szenario, ...] = (
    Szenario("vollbild", "NVDA", flags=ALLE_AN),
    # ETF: die EDGAR-Fetcher kehren vor jeder Abfrage zurueck (_ETF_NO_INSIDER_FILINGS).
    Szenario("etf", "SPY"),
    Szenario("quellen_fallen_aus", "NVDA", ausfall=True),
    Szenario("flags_aus", "NVDA", flags=ALLE_AUS),
)


def _szenario(sz) -> Szenario:
    if isinstance(sz, Szenario):
        return sz
    return next(s for s in SZENARIEN if s.name == sz)


def lade_antworten() -> dict:
    """Alle HTTP-Fixtures, je URL eine Antwort ``{"status", "json" | "text"}``."""
    antworten: dict = {}
    for pfad in sorted(FIXTURES.glob("*.json")):
        if pfad.name.endswith("_company.json"):
            continue
        antworten.update(json.loads(pfad.read_text(encoding="utf-8")))
    return antworten


# ── Aussengrenzen ────────────────────────────────────────────────────────────


class _Antwort:
    def __init__(self, eintrag: dict):
        self.status_code = eintrag["status"]
        self._json = eintrag.get("json")
        self.text = eintrag.get("text", json.dumps(self._json))

    def json(self):
        if self._json is None:
            return json.loads(self.text)
        return copy.deepcopy(self._json)


def _fester_client(antworten: dict, abgefragt: list, ohne_fixture: list, ausfall: bool):
    class _FesterClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, *args, **kwargs):
            url = str(url)
            abgefragt.append(url)
            if url not in antworten:
                ohne_fixture.append(url)
                raise RuntimeError(f"keine Fixture fuer {url}")
            if ausfall:
                if any(f"://{h}/" in url for h in _AUSNAHME_HOSTS):
                    raise ConnectionError(f"Ausfall (Fixture): {url}")
                return _Antwort({"status": 500, "text": "Internal Server Error"})
            return _Antwort(antworten[url])

    return _FesterClient


def _bars() -> pd.DataFrame:
    """``nvda_bars.json`` als OHLCV-Frame, vorn um die ersten 100 Schlusskurse verlaengert.

    Die Fixture hat 260 Zeilen; ``_fetch_ml_prediction`` verlangt mindestens 325, sonst
    endet sie vor FeatureBuilder und Registry. Die Verlaengerung ist fest und datiert die
    100 Kurse auf die Geschaeftstage vor dem ersten Fixture-Tag.
    """
    zeilen = json.loads(BARS.read_text(encoding="utf-8"))
    closes = [float(c) for _, c in zeilen]
    tage = [pd.Timestamp(d) for d, _ in zeilen]
    vorlauf = pd.bdate_range(end=tage[0] - pd.offsets.BDay(1), periods=100)
    closes = closes[:100] + closes
    index = pd.DatetimeIndex(list(vorlauf) + tage, name="timestamp", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
        },
        index=index,
    )


class _FesterProvider:
    def __init__(self, bars: pd.DataFrame):
        self._bars = bars

    def get_data(self, symbol, end, days, *args, **kwargs):
        return self._bars.copy()


class _FestesPanel:
    def __init__(self, xsec: dict):
        self._xsec = xsec

    def cross_section_at(self, as_of):
        return dict(self._xsec)


def _trends_modul(ausfall: bool) -> tuple[types.ModuleType, types.ModuleType]:
    class TrendReq:
        def __init__(self, *args, **kwargs):
            if ausfall:
                raise ConnectionError("Ausfall (Fixture): Google Trends")
            self._kw: list = []

        def build_payload(self, kw_list, **kwargs):
            self._kw = list(kw_list)

        def interest_over_time(self):
            werte = [41.0, 47.0, 52.0, 58.0, 63.0, 55.0, 49.0]
            return pd.DataFrame({k: werte for k in self._kw})

    paket = types.ModuleType("pytrends")
    request = types.ModuleType("pytrends.request")
    request.TrendReq = TrendReq
    paket.request = request
    return paket, request


_TFT = types.SimpleNamespace(
    direction="up",
    bear_return_pct=-2.1,
    base_return_pct=1.4,
    bull_return_pct=4.8,
    confidence=0.62,
    attention_weights=None,
)


class _VerbotenerProvider:
    gebaut: list = []

    def __init__(self, *args, **kwargs):
        _VerbotenerProvider.gebaut.append("HistoricalDataProvider")
        raise RuntimeError("echter HistoricalDataProvider im Netz gebaut")


# Lazy-Importe aus research() und den Fetchern. Vor dem Wechsel ins Arbeitsverzeichnis
# geladen, damit kein Import vom relativen Pfad abhaengt.
_LAZY = (
    "core.data_integrity",
    "core.data_provider",
    "core.ml.feature_builder",
    "core.ml.model_registry",
    "core.ml.vol_model",
    "core.redis_client",
    "core.report.fact_set",
    "core.report.lstm_panel_store",
    "core.report.news_feed",
    "core.specialist.form4_direction",
    "core.specialist.grounding",
    "core.specialist.parser",
    "core.specialist.prompt",
    "core.specialist.scenario",
)


# ── Lauf ─────────────────────────────────────────────────────────────────────


def fahre(
    szenario,
    *,
    antworten: dict | None = None,
    provider_ersetzen: bool = True,
) -> dict:
    """Ein Szenario durch den echten ``research()``-Weg; kanonisches Ergebnis als dict."""
    import importlib

    for name in _LAZY:
        importlib.import_module(name)

    import httpx

    import core.data_provider as data_provider
    import core.report.lstm_panel_store as panel
    import core.stock_specialist as ss
    from core.ml.model_registry import model_registry
    from core.redis_client import RedisClient

    sz = _szenario(szenario)
    if antworten is None:
        antworten = lade_antworten()
    abgefragt: list = []
    ohne_fixture: list = []
    _VerbotenerProvider.gebaut = []
    aufnahme: dict = {"prompt": None, "synthese_aufgerufen": False}

    cfg = types.SimpleNamespace(**{**VORGABE, **sz.flags})
    xsec = json.loads(XSEC.read_text(encoding="utf-8"))
    trends, trends_request = _trends_modul(sz.ausfall)

    # Reihenfolge: MonkeyPatch innen, damit chdir zurueckgenommen ist, bevor das
    # Verzeichnis geloescht wird (unter Windows sonst WinError 32).
    with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as mp:
        _patch_get_config(mp, cfg, consumer_modules=["core.stock_specialist"])
        mp.setattr(
            httpx,
            "AsyncClient",
            _fester_client(antworten, abgefragt, ohne_fixture, sz.ausfall),
        )
        mp.setattr(RedisClient, "get_redis", AsyncMock(return_value=None))
        mp.setitem(sys.modules, "pytrends", trends)
        mp.setitem(sys.modules, "pytrends.request", trends_request)
        mp.setattr(ss, "resolve_cik", lambda symbol: CIKS.get(symbol))
        mp.setattr(ss, "maybe_refresh", AsyncMock(return_value=None))
        mp.setattr(ss, "_now_utc", lambda: JETZT)
        mp.setattr(ss, "_DATA_PROVIDER", None)
        mp.setattr(data_provider, "HistoricalDataProvider", _VerbotenerProvider)
        if provider_ersetzen:
            fest = _FesterProvider(_bars())
            mp.setattr(ss, "_get_data_provider", lambda: fest)
        mp.setattr(model_registry, "get_or_train", AsyncMock(return_value=_TFT))
        mp.setattr(panel, "get_store", lambda: _FestesPanel(xsec))

        # _load_company_tokens liest data/company_cache/<SYM>.json relativ zum
        # Arbeitsverzeichnis: ein leeres Verzeichnis mit nur der Fixture.
        cache = Path(tmp) / "data" / "company_cache"
        cache.mkdir(parents=True)
        for pfad in FIXTURES.glob("*_company.json"):
            sym = pfad.name.split("_")[0].upper()
            (cache / f"{sym}.json").write_text(
                pfad.read_text(encoding="utf-8"), encoding="utf-8"
            )
        mp.chdir(tmp)

        agent = ss.StockSpecialistAgent(
            sz.symbol, "fixture-gemini", polygon_api_key=POLYGON_KEY
        )

        def _synthese(gathered, *args, **kwargs):
            aufnahme["synthese_aufgerufen"] = True
            aufnahme["prompt"] = agent._synthesis_prompt(gathered)
            return {"text": SYNTHESE}

        echter_build = agent._build_report

        def _build_report(gathered, synthesis, **kwargs):
            aufnahme["gathered"] = copy.deepcopy(gathered)
            return echter_build(gathered, synthesis, **kwargs)

        mp.setattr(agent, "_gemini_synthesize", AsyncMock(side_effect=_synthese))
        mp.setattr(agent, "_build_report", _build_report)

        report = asyncio.run(agent.research())

    if ohne_fixture:
        raise AssertionError(
            f"[{sz.name}] URL ohne Fixture abgefragt: {sorted(set(ohne_fixture))}"
        )
    if _VerbotenerProvider.gebaut:
        raise AssertionError(
            f"[{sz.name}] echte Klasse HistoricalDataProvider gebaut — "
            "_get_data_provider ist nicht ersetzt"
        )

    bericht = dataclasses.asdict(report)
    bericht.pop("updated_at")
    return _kanonisch_dict(
        {
            "gathered": aufnahme.get("gathered"),
            "synthese_aufgerufen": aufnahme["synthese_aufgerufen"],
            "prompt": aufnahme["prompt"],
            "bericht": bericht,
            "news_items": agent._news_items,
            "urls": sorted(abgefragt),
        }
    )


# ── Kanonisierung, Referenz, Vergleich ───────────────────────────────────────


def _runde(wert):
    """Floats auf 12 signifikante Stellen: BLAS-Reste zwischen Plattformen fallen weg."""
    if isinstance(wert, bool):
        return wert
    if isinstance(wert, float):
        return float(f"{wert:.12g}")
    if dataclasses.is_dataclass(wert) and not isinstance(wert, type):
        return _runde(dataclasses.asdict(wert))
    if isinstance(wert, dict):
        return {str(k): _runde(v) for k, v in wert.items()}
    if isinstance(wert, (list, tuple)):
        return [_runde(v) for v in wert]
    if isinstance(wert, (datetime, date)):
        return wert.isoformat()
    return wert


def kanonisch(wert) -> str:
    return json.dumps(_runde(wert), sort_keys=True, default=str, ensure_ascii=False)


def _kanonisch_dict(wert) -> dict:
    return json.loads(kanonisch(wert))


def messe_alle(szenarien=SZENARIEN) -> dict:
    return {sz.name: fahre(sz) for sz in szenarien}


def lade_referenz() -> dict:
    return json.loads(REFERENZ.read_text(encoding="utf-8"))


def schreibe_referenz(ist: dict) -> None:
    REFERENZ.write_text(
        json.dumps(ist, sort_keys=True, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# Kausale Reihenfolge der Abschnitte: was abgefragt wurde, was ankam, was daraus wurde.
# So nennt der erste Befund die Ursache, nicht eine ihrer Folgen im Bericht.
_REIHENFOLGE = ("urls", "gathered", "news_items", "synthese_aufgerufen", "prompt")


def _schluessel(k: str) -> tuple:
    return (_REIHENFOLGE.index(k) if k in _REIHENFOLGE else len(_REIHENFOLGE), k)


def befunde(referenz, ist, pfad: str = "") -> list[str]:
    """Abweichungen als ``pfad: referenz=… ist=…``; der erste ist die Ursache."""
    if isinstance(referenz, dict) and isinstance(ist, dict):
        out: list[str] = []
        for k in sorted(set(referenz) | set(ist), key=_schluessel):
            p = f"{pfad}.{k}" if pfad else str(k)
            if k not in referenz:
                out.append(f"{p}: neu, ist={json.dumps(ist[k])[:200]}")
            elif k not in ist:
                out.append(f"{p}: fehlt, referenz={json.dumps(referenz[k])[:200]}")
            else:
                out.extend(befunde(referenz[k], ist[k], p))
        return out
    if isinstance(referenz, list) and isinstance(ist, list):
        out = []
        for i, (r, s) in enumerate(zip(referenz, ist)):
            out.extend(befunde(r, s, f"{pfad}[{i}]"))
        if len(referenz) != len(ist):
            out.append(f"{pfad}: Laenge referenz={len(referenz)} ist={len(ist)}")
        return out
    if referenz != ist:
        return [
            f"{pfad}: referenz={json.dumps(referenz)[:200]} ist={json.dumps(ist)[:200]}"
        ]
    return []
