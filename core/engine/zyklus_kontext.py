# core/engine/zyklus_kontext.py
# #4251 (H-2j, ARC-E6 #3738) — umgezogen aus core/engine/trading_loop.py, wortgleich.
# Verantwortlichkeit: Zyklus-Kontext der Handelsschleife (Latenz-Start, Snapshots, Rangfolge,
# Hintergrund-Signale, Portfolio- und Positions-Snapshot, Rang-Trichter).
"""Zyklus-Kontext: was jeder offene Zyklus einmal aufbaut, bevor er Symbole bewertet.

#4251 (H-2j) zieht fünf Methoden aus ``TradingLoopMixin`` hierher (Schnitt-Entscheidung #4184,
``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md``): den Aufbau des Kontexts (Latenz-Start,
200er-Kappung ohne volles Universum, Snapshots), die LSTM-Rangfolge, das Auffrischen von
Earnings-Guard-Cache (#3349) und Regime-Signal (#3361), den Gatekeeper-Portfolio- und
Broker-Positions-Snapshot (GAP9, #2672, #3604) und den Rang-Trichter auf [Bestand ∪ Top-K].

Die Ruempfe sind wortgleich mit dem Stand vor dem Umbau. Abweichung: Die Patch-Ziele der
Tests (``get_config``, ``engine_now``, ``build_portfolio_context``,
``_fetch_position_snapshot``) liest das Modul zur Laufzeit als ``_tl.<name>`` am Kernmodul,
damit Patches auf ``core.engine.trading_loop`` weiter wirken (#4184 §3). Ebenso den
modulweiten ``logger``: Die Earnings-Guard-Warnung bleibt so am Logger
``core.engine.trading_loop``. ``time`` und ``asyncio`` sind frei importiert — der Kontext
schlaeft nicht, und die Latenz-Tests patchen ``time.perf_counter`` global. Der ``_tl``-Import
steht am Dateiende, damit der Kreis Kern <-> Kontext in beiden Ladereihenfolgen traegt.
``TradingLoopMixin`` erbt ``ZyklusKontextMixin``; der Dirigent ``live_trading_loop`` ruft
``_zyklus_kontext_aufbauen`` ueber die MRO.

Plan: ``docs/4251-*/implementation_plan.md``.
"""

import asyncio
import logging
import time
from datetime import timezone

from core.engine.zyklus import ZyklusZustand


class ZyklusKontextMixin:
    """#4251 (H-2j): Zyklus-Kontext der Handelsschleife; Basis von ``TradingLoopMixin``."""

    async def _zyklus_kontext_aufbauen(self, z: ZyklusZustand) -> None:
        """#4008: Latenz-Start, Snapshots, Rangfolge, Hintergrund-Signale, Portfolio- und
        Positions-Snapshot (je genau einmal pro Zyklus), Rang-Trichter. Kein Aussprung.
        """
        # Latency measurement
        z.t_start = time.perf_counter()
        z.t_data_fetched = z.t_start
        z.t_strategy_done = z.t_start

        z.current_time_utc = _tl.engine_now(timezone.utc)
        # Phase B: this 200-truncation is a slice of an UNRANKED, scanner-ordered
        # list. With the full universe it would silently make "the whole universe"
        # mean "whichever 200 arrived first" — so it must not survive when the
        # flag is ON. The fan-out it incidentally bounded is replaced by the
        # ADR-FU01 semaphore below, never simply dropped. Flag OFF -> identical
        # truncation, identical constant, byte-identical behaviour.
        if not _tl.get_config().FULL_UNIVERSE_TRADING_ENABLED:
            if len(z.symbols_to_process) > 200:
                z.symbols_to_process = z.symbols_to_process[:200]

        logging.info(
            f"Engine: Processing {len(z.symbols_to_process)} symbols: {z.symbols_to_process[:5]}..."
        )

        logging.warning(
            "FETCH_START: Requesting snapshots for %s...",
            z.symbols_to_process[:5],
        )
        z.snapshots = await self._fetch_snapshots_chunked(
            z.symbols_to_process, per_chunk_timeout=15.0
        )
        z.t_data_fetched = time.perf_counter()
        logging.warning("FETCH_SUCCESS: Fetched %d snapshots.", len(z.snapshots))

        await self._kontext_rangfolge(z)
        await self._kontext_signale_auffrischen(z)
        await self._kontext_snapshots(z)
        self._kontext_rang_trichter(z)

    async def _kontext_rangfolge(self, z: ZyklusZustand) -> None:
        """#4008: LSTM-Rangfolge auffrischen, ``z.symbols_order`` setzen."""
        if hasattr(z.local_active_strategy, "update_lstm_rankings"):
            await z.local_active_strategy.update_lstm_rankings(
                z.symbols_to_process,
                z.snapshots,
                self.current_market_data,
                z.current_time_utc,
            )
        elif (
            _tl.get_config().LSTM_RANK_PANEL_STRATEGY_INDEPENDENT
            and self._rank_panel_producer is not None
        ):
            # The keystone of the auditable report: produce the cross-section
            # panel even though the ACTIVE strategy cannot.
            #
            # The panel's only writer is LSTMDynamic.update_lstm_rankings, and
            # ACTIVE_STRATEGY defaults to RLAgent — so the rank the report's
            # recommendation LEADS WITH renders "Platz n/a von n/a" for every
            # symbol in the shipped build. Not an edge case: the default.
            #
            # Dormancy is structural, not preserved: this `elif` can only run
            # where the `if` above is FALSE — i.e. where zero code runs today.
            # There is no old behaviour at this site to keep byte-identical.
            #
            # The producer RANKS, it never trades: run_for_symbol is never
            # called on it, it is registered set_active=False so get_active()
            # can never return it, and its _bought_this_window /
            # high_water_marks / _entry_time stay empty by construction.
            #
            # Failure here must never cost a cycle: the panel is telemetry for
            # the report, and the trading path below does not read it.
            # Increment 2: re-rank the full universe at most once per
            # ROUND_TABLE_RANK_REFRESH_MINUTES (0 = every cycle, byte-identical). The panel
            # store retains the last ranking between refreshes, so the funnel keeps reading it.
            from core.engine.rank_funnel import should_refresh_rank

            _rank_now = time.monotonic()
            if should_refresh_rank(
                getattr(self, "_last_rank_refresh", None),
                _rank_now,
                _tl.get_config().ROUND_TABLE_RANK_REFRESH_MINUTES,
            ):
                try:
                    await self._rank_panel_producer.update_lstm_rankings(
                        z.symbols_to_process,
                        z.snapshots,
                        self.current_market_data,
                        z.current_time_utc,
                    )
                    # advance the timer only on SUCCESS → a failed rank retries next cycle
                    # rather than leaving the panel stale for a full interval.
                    self._last_rank_refresh = _rank_now
                except Exception:  # noqa: BLE001 — a ranker must not break trading
                    logging.warning(
                        "Rank-panel producer failed this cycle — the report's rank "
                        "will be an honest gap; trading is unaffected.",
                        exc_info=True,
                    )

        # LSTMDynamic: process in rank order
        z.symbols_order = z.symbols_to_process
        if getattr(z.local_active_strategy, "strategy_name", None) == "LSTMDynamic":
            rank_cache = getattr(z.local_active_strategy, "_lstm_rank_cache", [])
            if rank_cache:
                rank_order = [s for s, _ in rank_cache]
                z.symbols_order = [s for s in rank_order if s in z.symbols_to_process]
                z.symbols_order += [
                    s for s in z.symbols_to_process if s not in z.symbols_order
                ]

    async def _kontext_signale_auffrischen(self, z: ZyklusZustand) -> None:
        """#4008: Earnings-Guard-Cache (#3349) und Regime-Signal (#3361) anstoßen."""
        # #3349: Earnings-Guard cache producer. The guard only READS the daily
        # cache; without this it stays cold and the guard is inert whatever the
        # setting says. Armed-only, never under SIM_MODE (no network in a replay),
        # single-flight, and OFF the cycle: a first-of-day fill of a large
        # universe takes minutes (EDGAR fair-access pacing) and must not hold
        # up trading. Until it lands, missing symbols read as cold ⇒ BUY proceeds.
        _eg_cfg = _tl.get_config()
        if getattr(_eg_cfg, "EARNINGS_GUARD_ENABLED", False) and not getattr(
            _eg_cfg, "SIM_MODE", False
        ):
            _eg_task = getattr(self, "_earnings_cache_task", None)
            if _eg_task is None or _eg_task.done():
                if _eg_task is not None and not _eg_task.cancelled():
                    _eg_exc = _eg_task.exception()
                    if _eg_exc is not None:
                        _tl.logger.warning(
                            "EarningsGuard cache refresh failed (%s) — "
                            "uncached symbols stay cold (BUYs proceed).",
                            _eg_exc,
                            exc_info=_eg_exc,
                        )
                from core.engine.earnings_guard import ensure_fresh_earnings_cache

                self._earnings_cache_task = asyncio.create_task(
                    asyncio.to_thread(
                        ensure_fresh_earnings_cache,
                        list(z.symbols_to_process),
                        z.current_time_utc,
                    )
                )

        # --- #3361: regime-beyond-VIX signal — refreshed at most once per day, and
        # ONLY while the throttle is armed (dark default ⇒ no fetch, byte-identical).
        # Never under SIM_MODE (a replay must not overwrite the live cache). Runs in
        # a thread; any failure is a WARNING and the order path simply fails open.
        try:
            _rcfg = _tl.get_config()
            if getattr(_rcfg, "REGIME_THROTTLE_ENABLED", True) and not getattr(
                _rcfg, "SIM_MODE", False
            ):
                from core.engine.regime_signal import ensure_fresh_state, provider_fetch

                _rnow = _tl.engine_now(timezone.utc)
                await asyncio.to_thread(
                    ensure_fresh_state,
                    provider_fetch(self.data_provider, _rnow),
                    _rnow.date(),
                )
        except Exception as _regime_exc:  # noqa: BLE001
            logging.warning(
                "RegimeSignal refresh failed (%s) — throttle fails open.",
                _regime_exc,
                exc_info=True,
            )

    async def _kontext_snapshots(self, z: ZyklusZustand) -> None:
        """#4008: Gatekeeper-Portfolio (GAP9) und Broker-Positionen (#2672), je einmal."""
        # --- GAP9: ONE ComplianceGatekeeper portfolio snapshot per cycle ---
        # ARMED by default (GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED=True since #1962).
        # The snapshot is built ONCE here (not per symbol) and injected into every
        # symbol's state, so all parallel evaluations share one consistent portfolio
        # view. When the flag is OFF, the runner sees an empty context = today's
        # byte-identical behaviour. build_portfolio_context never raises (fail-open to None).
        z.portfolio_context = None
        if _tl.get_config().GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED:
            z.portfolio_context = await _tl.build_portfolio_context(
                getattr(self, "api", None),
                getattr(self, "compliance_guardian", None),
            )

        # #2672: ONE authoritative broker-position snapshot per cycle — shared by the
        # rank funnel below AND the position-context channels of every _symbol_state.
        # Deliberately OUTSIDE any feature gate (Archon audit Critical 1): the old
        # funnel-local fetch sat inside `if _top_k > 0` and zeroing that GPU tuning
        # flag would have silently starved every consumer of position truth.
        (
            z.positions_by_symbol,
            z.position_confirmed,
        ) = await _tl._fetch_position_snapshot(getattr(self, "api", None))
        # #3604: full-exit witness for the re-entry lockout (SELL vote / manual).
        try:
            self._note_position_snapshot(z.positions_by_symbol, z.position_confirmed)
        except Exception:  # noqa: BLE001 - observation only
            logging.warning("[ReentryLockout] snapshot note failed.", exc_info=True)

    def _kontext_rang_trichter(self, z: ZyklusZustand) -> None:
        """#4008: Tiefenbewertung auf [Bestand ∪ Top-K] kappen; im Zweifel volles Universum."""
        # Round-table-v2 rank-cache funnel: cap the deep-eval set to [holdings ∪ top-K
        # LSTM-ranked] so the ~500-symbol round-table graph does not saturate the GPU every
        # cycle. ROUND_TABLE_TOP_K_EVAL<=0 → no-op → byte-identical (default is 60 = ACTIVE,
        # settings.py; earlier revisions claimed "<=0 (default)", then "20", then "30" — all
        # went stale, #2672 / #3261 / #4652). It falls back to
        # the FULL universe on ANY doubt — holdings unconfirmed (fetch failed / no api), or a
        # cold / thin / STALE panel — so a held position is never dropped from the exit eval and
        # the engine never keeps trading a shrunken set on an outdated ranking.
        # #4652: with LLM_RANKING_ENABLED the ranking source is chosen by _rang_quelle_waehlen
        # (LLM list → LSTM panel → full universe); flag off → the LSTM path below, unchanged.
        # #4861: source "liste" = the user's own S&P-500 list (no LLM run, no expiry, no merge).
        _top_k = int(_tl.get_config().ROUND_TABLE_TOP_K_EVAL or 0)
        if _top_k > 0:
            try:
                from core.engine.rank_funnel import apply_rank_cache_funnel
                from core.report.lstm_panel_store import get_store

                # Holdings from the shared #2672 snapshot: funnel ONLY with a
                # confirmed book; any doubt → None → skip (full universe).
                _held = set(z.positions_by_symbol) if z.position_confirmed else None

                # Freshness: refuse a stale panel (producer stopped) → full universe + WARN.
                _store = get_store()
                _latest = _store.latest_snapshot_date()
                _max_age = int(
                    _tl.get_config().ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS or 0
                )
                # Freshness + read AS OF the ENGINE clock (engine_now → sim time under
                # SIM_MODE, else datetime.now — byte-identical in live). Must match the
                # clock the producer stamps the snapshot with (current_time_utc); using
                # the wall clock made the panel look ~years stale in SIM, so the funnel
                # silently fell back to the full universe and could not be evaluated.
                _stale = (
                    _latest is None
                    or (z.current_time_utc.date() - _latest).days > _max_age
                )

                # #4652: a due LLM run starts OFF the cycle; this cycle reads only the store.
                _llm_an = _llm_ranking_aktiv(_tl.get_config())
                _liste_an = _liste_aktiv(_tl.get_config())
                if _llm_an:
                    _llm_lauf_ausloesen(self, z, _top_k)

                _xsec, _quelle = None, QUELLE_UNIVERSUM
                if _held is not None and (_llm_an or _liste_an or not _stale):
                    _xsec, _quelle, _ = _rang_quelle_waehlen(
                        self, z, _store, _stale, _top_k, _llm_an, liste_an=_liste_an
                    )

                if _xsec is not None:
                    # The funnel's cold-panel guard (len(xsec) < k → no-op) is an LSTM rule; an
                    # LLM list is already validated and capped, so its own length is the cap.
                    _k = _top_k if _quelle == QUELLE_LSTM else len(_xsec)
                    _before = len(z.symbols_order)
                    z.symbols_order = apply_rank_cache_funnel(
                        z.symbols_order, _held, _xsec, _k
                    )
                    if _quelle == QUELLE_LISTE:
                        # #4861: the list's own order; llm/lstm stay byte-identical.
                        from core.engine.rank_funnel import ordne_nach_rang

                        z.symbols_order = ordne_nach_rang(z.symbols_order, _xsec)
                    if len(z.symbols_order) < _before:
                        logging.info(
                            "[RankFunnel] deep-eval %d/%d (top-%d ∪ %d held); %d deferred.",
                            len(z.symbols_order),
                            _before,
                            _top_k,
                            len(_held),
                            _before - len(z.symbols_order),
                        )
                elif _stale and _latest is not None:
                    logging.warning(
                        "[RankFunnel] LSTM panel stale (latest %s > %dd) — full universe; "
                        "is the rank producer running?",
                        _latest,
                        _max_age,
                    )
            except (
                Exception
            ) as _e:  # noqa: BLE001 — the funnel must never kill the cycle
                logging.warning(
                    "[RankFunnel] skipped this cycle (%s) — full universe.", _e
                )


# ── #4652 (Epic #4638): Ranking-Quelle des Rang-Trichters ────────────────────────────────
# Modulfunktionen statt Methoden: ZyklusKontextMixin traegt genau die fuenf Kontext-Schritte
# (test_zyklus_kontext_4251.py::test_kontext_liegt_in_zyklus_kontext).

QUELLE_LLM = "llm"
QUELLE_LLM_UND_LSTM = "llm_und_lstm"
QUELLE_LISTE = "liste"
QUELLE_LSTM = "lstm"
QUELLE_UNIVERSUM = "universum"
_LLM_QUELLEN = (QUELLE_LLM, QUELLE_LLM_UND_LSTM)
# #4960: Werte von LLM_RANKING_ANBIETER, die die Websuche des Anbieters nutzen. ``system``
# (Default) gehoert nicht dazu, auch wenn LLM_PROVIDER auf Gemini aufloest.
_CLOUD_ANBIETER = ("gemini", "openai", "anthropic")


def _llm_feld(cfg, key: str) -> str:
    """#4654: ein LLM-Ranking-Feld aus ``settings.py``, gegen die Registry geklemmt (WARNING)."""
    from core.trading_settings import REGISTRY, klemme_llm_ranking

    return klemme_llm_ranking(key, getattr(cfg, key, REGISTRY[key].default))


def _llm_ranking_aktiv(cfg) -> bool:
    """LLM_RANKING_ENABLED und eine LLM-Quelle; sonst der LSTM-Pfad wie heute.

    Eine unbekannte Quelle klemmt ``_llm_feld`` auf ``lstm`` und meldet sie als WARNING.
    """
    if not getattr(cfg, "LLM_RANKING_ENABLED", False):
        return False
    return _llm_feld(cfg, "LLM_RANKING_QUELLE") in _LLM_QUELLEN


def _liste_aktiv(cfg) -> bool:
    """#4861: LLM_RANKING_ENABLED und die Quelle ``liste``. Derselbe Rollback-Schalter wie die
    LLM-Quellen: Flag aus → der LSTM-Pfad wie heute, auch mit gespeicherter Liste."""
    if not getattr(cfg, "LLM_RANKING_ENABLED", False):
        return False
    return _llm_feld(cfg, "LLM_RANKING_QUELLE") == QUELLE_LISTE


def _llm_speicher(owner):
    """Der Lauf-Speicher der Engine, beim ersten Zugriff angelegt."""
    speicher = getattr(owner, "_llm_lauf_speicher", None)
    if speicher is None:
        from core.engine.llm_ranking_lauf import LaufSpeicher

        speicher = LaufSpeicher()
        owner._llm_lauf_speicher = speicher
    return speicher


def _llm_rhythmus(cfg=None):
    """#4654: der gespeicherte Rhythmus (``LLM_RANKING_RHYTHMUS``), geklemmt; Default je_refresh."""
    from core.engine.llm_ranking_lauf import Rhythmus

    if cfg is None:
        cfg = _tl.get_config()
    return Rhythmus(_llm_feld(cfg, "LLM_RANKING_RHYTHMUS"))


def _rang_quelle_waehlen(
    owner,
    z: ZyklusZustand,
    store,
    stale: bool,
    top_k: int,
    llm_an: bool,
    liste_an: bool = False,
):
    """#4652: (xsec | None, quelle, status) fuer den Rang-Trichter. None → ganzes Universum.

    #4861 ``liste_an``: die eigene Liste, in jedem Zyklus mit ``pruefe_symbolliste`` gegen
    ``z.symbols_to_process`` geprueft. Kein ``LaufSpeicher``, keine Hoechstdauer, kein LSTM-
    Auffuellen (Owner-Entscheide 2 und 3); Status ``dauerhaft``. Den Pruefstand legt sie fuer
    den QA-Trichter ab (``owner._liste_stand``). Kein gueltiger Wert → WARNING, Stand ``None``,
    LSTM-Panel wie beim LLM-Rueckfall.

    Kaskade: gueltige LLM-Liste (frisch oder veraltet_aber_gueltig) → ``als_cross_section``;
    abgelaufen oder keiner → WARNING, LSTM-Panel wie heute; Panel veraltet → ganzes Universum.
    ``llm_und_lstm``: LLM-Raenge zuerst, aufgefuellt mit dem LSTM-Top ohne Doppelte, gekappt auf
    ``top_k`` (Mischregel offen beim Owner, Plan §6). Flag aus → nur das LSTM-Panel, ohne Log.
    """
    from core.report.lstm_panel_store import active_cross_section

    def _lstm():
        # #2680: the ONE ranking seam (was an unconditional cross_section_ma — flag-gated now,
        # so the cloud stays byte-identical and the whole pipeline shares one basis).
        return None if stale else active_cross_section(store, z.current_time_utc)

    if liste_an:
        return _liste_waehlen(owner, z, top_k, _lstm)

    if not llm_an:
        xsec = _lstm()
        return xsec, (QUELLE_UNIVERSUM if xsec is None else QUELLE_LSTM), None

    from core.engine.llm_ranking import als_cross_section
    from core.engine.llm_ranking_lauf import ABGELAUFEN, hoechstdauer

    cfg = _tl.get_config()
    speicher = _llm_speicher(owner)
    dauer = hoechstdauer(
        _llm_rhythmus(cfg), float(cfg.ROUND_TABLE_RANK_REFRESH_MINUTES or 0)
    )
    liste, status, alter = speicher.gueltige_liste(z.current_time_utc, dauer)
    if liste is None:
        if status == ABGELAUFEN:
            logging.warning(
                "[RankFunnel] LLM-Liste abgelaufen (Alter %s, Hoechstdauer %s) — "
                "Rueckfall auf das LSTM-Panel.",
                alter,
                dauer,
            )
        else:
            logging.warning(
                "[RankFunnel] keine LLM-Liste (noch kein erfolgreicher Lauf) — "
                "Rueckfall auf das LSTM-Panel."
            )
        xsec = _lstm()
        quelle = QUELLE_UNIVERSUM if xsec is None else QUELLE_LSTM
    else:
        z.llm_lauf_id = speicher.letzter_erfolg().lauf_id
        rang = list(liste[:top_k])
        quelle = QUELLE_LLM
        if _llm_feld(cfg, "LLM_RANKING_QUELLE") == QUELLE_LLM_UND_LSTM:
            lstm = _lstm()
            if lstm:
                lstm_top = [
                    s
                    for s, _ in sorted(lstm.items(), key=lambda kv: kv[1], reverse=True)
                ]
                rang = (rang + [s for s in lstm_top if s not in rang])[:top_k]
                quelle = QUELLE_LLM_UND_LSTM
        xsec = als_cross_section(rang)

    z.rang_quelle = quelle
    logging.info("[RankFunnel] quelle=%s status=%s alter=%s", quelle, status, alter)
    return xsec, quelle, status


def _liste_waehlen(owner, z: ZyklusZustand, top_k: int, lstm):
    """#4861: der Zweig ``liste`` von ``_rang_quelle_waehlen`` (dort beschrieben)."""
    from core.engine.llm_ranking import als_cross_section, pruefe_symbolliste
    from core.engine.llm_ranking_lauf import DAUERHAFT

    rang, verworfen = pruefe_symbolliste(
        _llm_feld(_tl.get_config(), "LLM_RANKING_LISTE"),
        set(z.symbols_to_process),
        top_k,
    )
    if rang:
        owner._liste_stand = (z.current_time_utc, rang, verworfen)
        xsec, quelle, status = als_cross_section(rang), QUELLE_LISTE, DAUERHAFT
    else:
        owner._liste_stand = None
        logging.warning(
            "[RankFunnel] eigene Liste ohne gueltigen Wert im Universum dieses Zyklus "
            "(%d verworfen) — Rueckfall auf das LSTM-Panel.",
            len(verworfen),
        )
        xsec, status = lstm(), None
        quelle = QUELLE_UNIVERSUM if xsec is None else QUELLE_LSTM
    z.rang_quelle = quelle
    logging.info("[RankFunnel] quelle=%s status=%s alter=%s", quelle, status, None)
    return xsec, quelle, status


def _llm_lauf_ausloesen(owner, z: ZyklusZustand, top_k: int) -> None:
    """#4652: startet einen faelligen LLM-Lauf als Single-Flight-Task AUSSERHALB des Zyklus.

    Muster des Earnings-Cache-Tasks (``_kontext_signale_auffrischen``): ein laufender Task wird
    nicht verdoppelt, der Fehler eines beendeten Tasks wird hier als WARNING sichtbar. Faellig
    nach ``should_refresh_rank`` (eigener Takt, eine Anfrage je Refresh-Intervall, auch nach
    einem Fehlschlag) und ``ist_lauf_faellig`` (Rhythmus). Unter SIM_MODE nur mit Stub- oder
    Ollama-Provider (Plan §6). Wirft nie — die Auswahl dieses Zyklus haengt nicht daran.

    #4960: der Takt ist ``takt_minuten`` (eigener Takt fuer ``stuendlich``/``alle_4_stunden``,
    sonst der Refresh). ``LLM_RANKING_ANBIETER`` und ``websuche`` gehen an den Task; den
    Provider loest erst der Task auf, nie dieser synchrone Zyklus. Websuche nur bei explizit
    gewaehltem Cloud-Anbieter, ``system`` (Default) sucht nie. Die SIM-Sperre prueft den
    effektiven Anbieter.
    """
    try:
        task = getattr(owner, "_llm_ranking_task", None)
        if task is not None:
            if not task.done():
                return  # single-flight
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    logging.warning(
                        "[RankFunnel] LLM-Ranking-Lauf fehlgeschlagen (%s) — die gueltige "
                        "Liste bleibt, bis sie ablaeuft.",
                        exc,
                        exc_info=exc,
                    )
            owner._llm_ranking_task = None

        from core.engine.llm_ranking_lauf import ist_lauf_faellig, takt_minuten
        from core.engine.rank_funnel import should_refresh_rank

        cfg = _tl.get_config()
        rhythmus = _llm_rhythmus(cfg)
        jetzt_mono = time.monotonic()
        refresh = should_refresh_rank(
            getattr(owner, "_llm_letzter_refresh", None),
            jetzt_mono,
            takt_minuten(rhythmus, cfg.ROUND_TABLE_RANK_REFRESH_MINUTES or 0),
        )
        speicher = _llm_speicher(owner)
        erfolg = speicher.letzter_erfolg()
        if not ist_lauf_faellig(
            rhythmus,
            erfolg.zeitpunkt_utc if erfolg is not None else None,
            z.current_time_utc,
            refresh,
        ):
            return

        prompt = _llm_feld(cfg, "LLM_RANKING_PROMPT")
        provider = getattr(owner, "_llm_ranking_provider", None)
        if not prompt:
            owner._llm_letzter_refresh = jetzt_mono
            logging.warning(
                "[RankFunnel] LLM_RANKING_PROMPT ist leer — kein LLM-Lauf, Rueckfall auf LSTM."
            )
            return
        anbieter = _llm_feld(cfg, "LLM_RANKING_ANBIETER")
        if getattr(cfg, "SIM_MODE", False) and provider is None:
            from core.llm.health import resolved_provider_name

            effektiv = resolved_provider_name() if anbieter == "system" else anbieter
            if effektiv != "ollama":
                owner._llm_letzter_refresh = jetzt_mono
                logging.warning(
                    "[RankFunnel] SIM_MODE: LLM-Lauf nur mit Stub- oder Ollama-Provider "
                    "(effektiver Anbieter %r) — kein Lauf, Rueckfall auf LSTM.",
                    effektiv,
                )
                return

        asyncio.get_running_loop()  # ohne laufende Schleife kein Task (und kein Koroutinen-Leck)
        owner._llm_letzter_refresh = jetzt_mono
        owner._llm_ranking_task = asyncio.create_task(
            _llm_lauf(
                speicher,
                prompt,
                list(z.symbols_to_process),
                top_k,
                provider,
                z.current_time_utc,
                quelle=_llm_feld(cfg, "LLM_RANKING_QUELLE"),
                anbieter=anbieter,
                websuche=anbieter in _CLOUD_ANBIETER,
            )
        )
    except Exception:  # noqa: BLE001 — die LLM-Vorauswahl darf den Zyklus nie kosten
        logging.warning("[RankFunnel] LLM-Lauf nicht gestartet.", exc_info=True)


async def _llm_lauf(
    speicher,
    prompt,
    universum,
    top_k,
    provider,
    zeitpunkt_utc,
    quelle=QUELLE_LLM,
    anbieter=None,
    websuche=False,
):
    """#4652: ein LLM-Lauf im Hintergrund — abfragen, protokollieren, Span ``llm_ranking.lauf``.

    #4654: der Span traegt die Ranking-Quelle; vom Prompt nur den Hash, nie den Klartext.
    #4960: ``anbieter`` (``LLM_RANKING_ANBIETER``) loest ``frage_llm_ranking`` hier im Task auf;
    der Span traegt den effektiven Anbieter, ``websuche`` und ``n_quellen``, nie die URLs.
    """
    from core.engine.llm_ranking import frage_llm_ranking
    from core.engine.llm_ranking_lauf import LlmRankingLauf
    from core.telemetry import get_tracer

    t0 = time.perf_counter()
    with get_tracer(__name__).start_as_current_span("llm_ranking.lauf") as span:
        ergebnis = await frage_llm_ranking(
            prompt,
            set(universum),
            top_k,
            provider=provider,
            anbieter=anbieter,
            websuche=websuche,
        )
        lauf = LlmRankingLauf.aus_ergebnis(ergebnis, zeitpunkt_utc=zeitpunkt_utc)
        speicher.protokolliere(lauf)
        for name, wert in (
            ("prompt_hash", lauf.prompt_hash),
            ("ranking_quelle", quelle),
            ("anbieter", lauf.kanonischer_anbieter or lauf.anbieter or ""),
            ("ok", bool(lauf.ok)),
            ("sim", bool(lauf.sim)),
            ("websuche", bool(lauf.websuche)),
            ("n_quellen", len(lauf.quellen)),
            ("n_geliefert", len(lauf.geliefert)),
            ("n_verworfen", len(lauf.verworfen)),
            ("dauer_s", round(time.perf_counter() - t0, 3)),
        ):
            span.set_attribute(name, wert)
    return lauf


# #4251 (H-2j): Kern-Namen (get_config, engine_now, build_portfolio_context,
# _fetch_position_snapshot, logger) liest das Modul ueber _tl, damit die Patch-Ziele der Tests
# wirken (#4184 §3). Am Dateiende: der Kreis Kern <-> Kontext traegt so in beiden
# Ladereihenfolgen (Muster core/engine/zyklus_vorlauf.py).
from core.engine import trading_loop as _tl  # noqa: E402
