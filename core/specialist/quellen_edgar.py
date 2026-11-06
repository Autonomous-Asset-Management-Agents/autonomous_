# core/specialist/quellen_edgar.py
# #4161 (ARC-E6 G-8a2): EDGAR- und Insider-Quellen des Stock Specialist als Mixin.
"""EDGAR- und Insider-Quellen des ``StockSpecialistAgent`` (aus ``core/stock_specialist.py``).

Die zehn Methoden sind wortgleich umgezogen (``test_specialist_quellen_wortgleich.py``):
amtliche Meldungen (Form 4, 8-K, 13D/G), Kongress-Trades, das TFT- und das Vol-Modell auf dem
eigenen Daten-Provider. ``StockSpecialistAgent`` erbt ``EdgarQuellenMixin``.

Zugriffsregel (wie ``ar`` in G-4, ``_ag`` in G-6): ``get_config``, ``_get_data_provider``,
``resolve_cik``, ``_now_utc`` und ``_ETF_NO_INSIDER_FILINGS`` liest das Mixin zur Laufzeit als
``_ss.<name>`` ueber das Modul ``core.stock_specialist``. Dort patchen die Tests sie
(``test_specialist_patch_ziele.py``). Der Logger behaelt den Namen ``core.stock_specialist``.
"""

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("core.stock_specialist")


class EdgarQuellenMixin:
    """Datenquellen EDGAR, Insider, Kongress, TFT und Vol-Band (``self.symbol`` vom Agenten)."""

    def _fetch_earnings_transcript(self) -> str:
        """RPAR T6b (#1271) PR-2: latest earnings-call transcript snippet for the IQ prompt.  # noqa: E501

        Graceful stub (Dual Design Option A): returns "" until the transcript data source is  # noqa: E501
        decided. The caller caps it via ``cap_transcript`` and the IQ path is flag-gated  # noqa: E501
        (``INSIGHT_QUALITY_ENABLED``), so "" simply means no transcript injected — never a crash.  # noqa: E501
        Wiring the real source is the follow-up once the provider + bundle snapshot are confirmed.  # noqa: E501
        """
        return ""

    # ─────────────────────────────────────────────────────────
    # Data gatherers — all free, no LLM
    # ─────────────────────────────────────────────────────────

    async def _fetch_ml_prediction(self) -> Optional[Dict[str, Any]]:
        """Fusion (flag-gated): the per-symbol TFT prediction. **Flag-FIRST** — returns  # noqa: E501
        None before any data I/O unless ``ML_PREDICTION_ENABLED``. Bars come from the  # noqa: E501
        canonical ``data_provider`` (sync → ``asyncio.to_thread``); FeatureBuilder +  # noqa: E501
        model_registry do the rest. Any failure → None (logged at WARNING). The order path  # noqa: E501
        never depends on this; it only populates the report's ``ml_*`` fields."""  # noqa: E501
        if not getattr(_ss.get_config(), "ML_PREDICTION_ENABLED", False):
            return None
        try:
            bars_df = await asyncio.to_thread(
                _ss._get_data_provider().get_data,
                self.symbol,
                _ss._now_utc(),
                5 * 365,
            )
            if bars_df is None or len(bars_df) < 325:
                return None
            # Local, not self.* — returned in the dict so concurrent research() calls on  # noqa: E501
            # the same agent can't race on a shared instance attribute (review fix).  # noqa: E501
            forecast_vol = None
            try:
                from core.ml.vol_model import forecast_forward_vol

                forecast_vol = forecast_forward_vol(bars_df)
            except Exception as exc:
                logger.warning(
                    "[ML] %s: forecast_vol failed: %s", self.symbol, exc
                )  # noqa: E501

            from core.ml.feature_builder import FeatureBuilder

            features_df = FeatureBuilder().build(bars_df, symbol=self.symbol)
            if (
                features_df is None or features_df.empty or len(features_df) < 60
            ):  # noqa: E501
                return None

            from core.ml.model_registry import model_registry

            prediction = await model_registry.get_or_train(
                self.symbol, features_df
            )  # noqa: E501
            if prediction is None:
                return None
            # attention_weights is intentionally NOT returned — it is a non-scalar and must  # noqa: E501
            # never reach the §5.9/BORA-scalar-only state["ml"]; nothing on main reads it.  # noqa: E501
            return {
                "direction": prediction.direction,
                "bear_return_pct": prediction.bear_return_pct,
                "base_return_pct": prediction.base_return_pct,
                "bull_return_pct": prediction.bull_return_pct,
                "confidence": prediction.confidence,
                "forecast_vol": forecast_vol,
            }
        except Exception as exc:
            logger.warning(
                "[ML] prediction failed for %s: %s", self.symbol, exc
            )  # noqa: E501
            return None

    async def _fetch_vol_scenario(self) -> Optional[Dict[str, Any]]:
        """Rich-synthesis card (Direction A): the honest, non-directional HAR-RV
        volatility band. **Flag-FIRST** — returns None before any I/O unless
        ``SPECIALIST_GROUNDING_ENABLED`` (the card gate). Bars come from the same
        canonical ``data_provider`` as ``_fetch_ml_prediction`` (sync → thread), but
        WITHOUT the ``ML_PREDICTION_ENABLED`` gate: the vol band is LLM-free and
        NOT the gate-dead TFT. Any failure / too-little-history → None (logged)."""
        if not getattr(_ss.get_config(), "SPECIALIST_GROUNDING_ENABLED", False):
            return None
        try:
            bars_df = await asyncio.to_thread(
                _ss._get_data_provider().get_data,
                self.symbol,
                _ss._now_utc(),
                5 * 365,
            )
            from core.specialist.scenario import vol_scenario

            return vol_scenario(bars_df)
        except Exception as exc:
            logger.warning(
                "[vol-scenario] failed for %s: %s", self.symbol, exc
            )  # noqa: E501
            return None

    def _load_company_tokens(self) -> set:
        """Load THIS symbol's own name tokens from data/company_cache/<sym>.json
        (for the grounding entity gate — the subject company is always 'known').
        Missing/malformed cache → empty set (company-name sentences are then
        harmlessly over-dropped; precision bias). Never raises."""
        import json
        import os

        try:
            path = os.path.join("data", "company_cache", f"{self.symbol}.json")
            if not os.path.exists(path):
                return set()
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            name = (payload.get("results") or {}).get("name") or ""
            return {
                t for t in re.split(r"[^a-z0-9]+", str(name).lower()) if t
            }  # noqa: E501
        except Exception:
            return set()

    async def _fetch_edgar(
        self,
        *,
        forms: str,
        cutoff_days: int,
        cap: int,
        build_row,
        enrich=None,
    ) -> List[Dict]:
        """Shared SEC EDGAR full-text fetch (RQ-1 B1, #1521). Pipeline per fetcher:  # noqa: E501
          STEP 0 (A1): ETF short-circuit -> [] (no I/O; ETFs have no own issuer filings).  # noqa: E501
          STEP 1 (B1): resolve ticker->CIK, scope the query with ``&ciks=`` + a ``ciks``  # noqa: E501
                       membership match-back so only the issuer's OWN filings survive -- kills  # noqa: E501
                       the "Spy Inc."/"Magnum Opus" false positives where a 3-letter ticker  # noqa: E501
                       matched as a word in an unrelated registrant's filing. Unknown ticker  # noqa: E501
                       keeps the free-text ``q=`` fallback (never regress new/illiquid symbols).  # noqa: E501
          STEP 2 (A2): client-side recency guard on file_date (efts ranks by relevance, NOT  # noqa: E501
                       date, so server-side ``startdt`` alone leaks stale filings).  # noqa: E501
        ``build_row(src)`` produces the per-form output dict; its keys are UNCHANGED vs the  # noqa: E501
        pre-B1 fetchers, so the prompt / escalation / serializer-count contract is preserved.  # noqa: E501
        B1 changes only WHICH filings populate the lists."""
        try:
            import httpx

            # STEP 0 -- A1 ETF short-circuit (before any I/O)
            if self.symbol in _ss._ETF_NO_INSIDER_FILINGS:
                return []

            # STEP 1 -- B1: resolve CIK (None -> free-text fallback, WARNING-logged)  # noqa: E501
            cik = _ss.resolve_cik(self.symbol)
            cutoff = (_ss._now_utc() - timedelta(days=cutoff_days)).strftime("%Y-%m-%d")
            # EFTS-DATE FIX (live finding 2026-07-23): the server applies the
            # custom date range ONLY when BOTH startdt AND enddt are present.
            # With startdt alone it silently relevance-ranks the FULL archive —
            # the top page for a liquid ticker is then 2003-2017 filings, every
            # hit fails the client-side recency guard, and every card showed
            # "0 insider filings" despite 1341 real hits. With enddt the same
            # AAPL query returns exactly the 2 genuine last-45-day Form 4s.
            today = _ss._now_utc().strftime("%Y-%m-%d")
            base = "https://efts.sec.gov/LATEST/search-index"
            if cik:
                url = (
                    f"{base}?q=%22{self.symbol}%22&forms={forms}"
                    f"&ciks={cik}&dateRange=custom&startdt={cutoff}"
                    f"&enddt={today}"
                )
            else:
                logger.warning(
                    "[%s] resolve_cik miss -> free-text EDGAR fallback (forms=%s)",  # noqa: E501
                    self.symbol,
                    forms,
                )
                url = (
                    f"{base}?q=%22{self.symbol}%22&forms={forms}"
                    f"&dateRange=custom&startdt={cutoff}&enddt={today}"
                )

            headers = {
                "User-Agent": "AI-Trading-Bot research@aaagents.de",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    return []
                hits = r.json().get("hits", {}).get("hits", [])

            out: List[Dict] = []
            passed: List[Dict] = (
                []
            )  # B3b: hits parallel to `out`, for optional enrichment
            for hit in hits:
                src = hit.get("_source", {})

                # STEP 1 (cont.) -- B1 match-back: keep only hits whose `ciks` array contains  # noqa: E501
                # the resolved issuer CIK (MEMBERSHIP -- a Form 4 carries the reporting owner's  # noqa: E501
                # CIK AND the issuer's). Normalise both sides to int (map gives padded str;  # noqa: E501
                # `ciks` are padded strs) -- comparing padded-str to int would silently drop  # noqa: E501
                # every row. Skipped on the free-text fallback (no CIK).
                if cik:
                    src_ciks = src.get("ciks") or []
                    if int(cik) not in {
                        int(c) for c in src_ciks if str(c).strip()
                    }:  # noqa: E501
                        continue

                # STEP 2 -- A2 recency guard (client-side; YYYY-MM-DD lexical == chronological)  # noqa: E501
                filed = src.get("file_date", "")
                if filed and filed < cutoff:
                    continue

                out.append(build_row(src))
                passed.append(hit)
                if len(out) >= cap:
                    break
            # RQ-1 B3b (#1536): optional per-row enrichment (e.g. Form 4 buy/sell direction),  # noqa: E501
            # flag-gated INSIDE the enricher -> the default path stays byte-identical.  # noqa: E501
            if enrich is not None:
                await enrich(out, passed, cik)
            return out
        except Exception as e:
            logger.debug("[%s] EDGAR %s: %s", self.symbol, forms, e)
            return []

    async def _fetch_edgar_form4(self) -> List[Dict]:
        """SEC EDGAR Form 4 — insider buy/sell filings (last 45 days)."""
        return await self._fetch_edgar(
            forms="4",
            cutoff_days=45,
            cap=15,
            build_row=lambda src: {
                "filed": src.get("file_date", ""),
                "filer": (src.get("display_names") or ["Unknown"])[0],
                "form": "Form 4",
                "period": src.get("period_of_report", ""),
            },
            enrich=self._enrich_form4_directions,
        )

    async def _enrich_form4_directions(self, rows, hits, cik):
        """RQ-1 B3b (#1536): flag-gated. When SPECIALIST_FORM4_DIRECTION_ENABLED, fetch each  # noqa: E501
        Form 4 document + set rows[i]["direction"] (buy/sell/mixed/neutral). Default OFF -> a  # noqa: E501
        no-op (no extra SEC requests; the row keys stay unchanged). Best-effort: any failure  # noqa: E501
        leaves the row without a direction key rather than raising."""
        if not getattr(
            _ss.get_config(), "SPECIALIST_FORM4_DIRECTION_ENABLED", False
        ):  # noqa: E501
            return
        if not cik:
            return
        import httpx

        from core.specialist.form4_direction import classify_form4_direction

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                for row, hit in zip(rows, hits):
                    _id = hit.get("_id", "")
                    if ":" not in _id:
                        continue
                    adsh, fname = _id.split(":", 1)
                    url = (
                        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                        f"{adsh.replace('-', '')}/{fname}"
                    )
                    row["direction"] = await classify_form4_direction(
                        client, url
                    )  # noqa: E501
        except Exception as e:  # noqa: BLE001 -- enrichment is best-effort, never fatal
            logger.warning(
                "[%s] form4 direction enrichment failed: %s", self.symbol, e
            )  # noqa: E501

    async def _fetch_edgar_8k(self) -> List[Dict]:
        """SEC EDGAR Form 8-K — material events (last 30 days)."""
        return await self._fetch_edgar(
            forms="8-K",
            cutoff_days=30,
            cap=8,
            build_row=lambda src: {
                "filed": src.get("file_date", ""),
                "description": src.get("period_of_report", ""),
                "entity": (
                    src.get("entity_name", "")
                    or (src.get("display_names") or [""])[0]  # noqa: E501
                )[:80],
            },
        )

    async def _fetch_edgar_13d(self) -> List[Dict]:
        """SEC EDGAR Schedule 13D/G — activist investor or large stake disclosures."""  # noqa: E501
        return await self._fetch_edgar(
            forms="SC+13D,SC+13G",
            cutoff_days=90,
            cap=5,
            build_row=lambda src: {
                "filed": src.get("file_date", ""),
                "filer": (src.get("display_names") or ["Unknown"])[0],
                "form": src.get("form_type", "13D/G"),
            },
        )

    async def _fetch_congressional_trades(self) -> Optional[List[Dict]]:
        """Quiver Quant — congressional trading disclosures.

        #2587 dead-source honesty: returns ``None`` when the SOURCE is unavailable
        (non-200 / exception — live finding 2026-07-30: the endpoint answers 404
        without the Quiver API token this deployment has never carried), ``[]``
        only for a real checked-and-empty answer. The caller maps ``None`` into
        ``sources_unavailable`` so the coverage card shows "—", never a fake 0.
        """
        try:
            import httpx

            url = f"https://api.quiverquant.com/beta/live/congresstrading/{self.symbol}"  # noqa: E501
            headers = {
                "User-Agent": "AI-Trading-Bot research@aaagents.de",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    logger.warning(
                        "[%s] Congressional trades HTTP %s — source unavailable "
                        "this cycle (Quiver needs an API token; none configured)",
                        self.symbol,
                        r.status_code,
                    )
                    return None
                data = r.json()
                if not isinstance(data, list):
                    logger.warning(
                        "[%s] Congressional trades: non-list payload — "
                        "source unavailable this cycle",
                        self.symbol,
                    )
                    return None
                return [
                    {
                        "politician": item.get("Representative", ""),
                        "transaction": item.get("Transaction", ""),
                        "amount": item.get("Amount", ""),
                        "date": item.get("TransactionDate", ""),
                    }
                    for item in data[:5]
                ]
        except Exception as e:
            logger.warning(
                "[%s] Congressional trades unavailable: %s", self.symbol, e
            )  # noqa: E501
            return None


# Am Dateiende, nicht am Kopf: ``core.stock_specialist`` importiert dieses Modul, um
# ``EdgarQuellenMixin`` zu erben. Stuende der Import oben, braeche ein Import dieses Moduls
# vor ``core.stock_specialist`` am halb geladenen Kreis. ``_ss.<name>`` liest erst der Aufruf.
from core import stock_specialist as _ss  # noqa: E402
