"""
lstm_offline.py — RTR-0 Inc 3 (#2409): dedicated offline LSTM scorer.

Makes the LSTMSignalAgent offline-attributable: loads the repo model bundle
(``data/lstm_model.pth`` + ``scaler_x.pkl`` + ``scaler_y.pkl`` +
``model_metadata.json``) directly via torch and replicates the LIVE
ranking-path feature/scaler pipeline (``LSTMDynamicStrategy``,
core/strategies/lstm_strategy.py) so the replay benchmark can compute
per-date cross-section scores over the panel bars.

Option B of the #2409 plan — a DEDICATED offline scorer instead of reusing
the live strategy object (which would drag Registry/Engine plumbing into the
offline path). The price of Option B is drift risk, and that price is paid
with a TEST-PINNED PARITY CONTRACT
(tests/unit/test_rtr0_lstm_offline.py::TestLiveParityPin): on identical
fixture bars the offline score equals the live ``_get_torch_prediction``
output within 1e-6, and abstentions agree. The heavy shared pieces are
IMPORTED from the model module, never re-implemented:
``create_live_features`` / ``FEATURE_WARMUP_ROWS`` / ``z_to_return`` /
``LSTMModel`` / ``resolve_sequence_length`` (models/torch_model.py) — only
the thin orchestration around them is replicated, with file:line anchors.

Replay integration (``OfflineLstmStrategy``): the strategy-injection hook of
``replay_votes(strategy=...)`` (core/analysis/attribution/replay.py:218-243)
gets a registry strategy whose ``evaluate_for_symbol`` mirrors the live
evaluate contract for a POSITION-LESS book — the REAL ``LSTMSignalAgent``
(agents.py:746-779) and ``RLConfidenceAgent`` (agents.py:883-912) then vote
through their genuine production mappings. Documented limits:

  * no portfolio state offline -> ``in_pos`` is always False; the live SELL
    branch (smart exit, lstm_strategy.py:568-598) is unreachable, actions
    are BUY (top-N) / HOLD exactly as live for a flat book
    (lstm_strategy.py:565-567);
  * RLConfidenceAgent reads the SAME ``lstm_prediction`` field
    (agents.py:898) — exactly as on Desktop where LSTMDynamic is the active
    strategy — so its truth-table row becomes measurable too; its basis (the
    LSTM signal, not an RL policy) is stated in the RUNBOOK;
  * abstention contract preserved: short history / feature failure / serve
    errors abstain (None) exactly like the live path (#1878) — never a
    numeric pseudo-prediction.

Error posture: the live loader logs and degrades (the trading engine must
never crash, lstm_strategy.py:155-158/218-219); this OFFLINE tool raises
``LstmArtifactError`` LOUDLY instead — an operator who requested LSTM
scoring must never silently receive an EXCLUDED row.

PIT day boundary (Rev 3, PR #2421 owner repro): the engine's real
``market_data_cache`` stamps the daily bar of calendar day D at an INTRADAY
UTC time (04:00 EDT / 05:00 EST), never midnight. The live ranking cycle
fetches with wall-clock ``now`` (>= the bar's own timestamp), so day D's bar
is always included; the replay only has the CALENDAR day, and a
midnight-normalized ``as_of`` slice (``index <= 00:00``) excluded day D's
own bar — every symbol failed the tradeability gate, the cross-section came
back empty and both model agents abstained on 100% of bars at EXIT 0.
``_pit_day_end`` therefore slices through the END of the ``as_of`` calendar
day: day D's bar is in (exactly live semantics), day D+1 can never be
(daily bars — no lookahead).

OFFLINE ONLY: nothing in the product imports this module (Airlock §5.7).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# The vote path calls the strategy with market_data={} (agents.py:746-748),
# so the live scalar fallbacks (lstm_strategy.py:304-315: market_data.get(
# "vix", 20.0) / market_data.get("latest_news_sentiment", 0.0)) resolve to
# these constants on every replayed bar.
_VIX_FALLBACK = 20.0
_SENTIMENT_FALLBACK = 0.0

_ARTIFACT_NAMES = (
    "lstm_model.pth",
    "scaler_x.pkl",
    "scaler_y.pkl",
    "model_metadata.json",
)


class LstmArtifactError(RuntimeError):
    """The LSTM model bundle is missing or not loadable (fail LOUD offline)."""


def _pit_day_end(as_of) -> pd.Timestamp:
    """Inclusive PIT boundary: the END of the ``as_of`` calendar day.

    Live parity seam (Rev 3, PR #2421): live ranks with wall-clock ``now``
    (always >= the latest completed bar's timestamp), the replay only has the
    calendar day. Intraday-stamped daily bars (04:00/05:00 UTC in the real
    ``market_data_cache``) must stay inside the slice for their own day —
    and day D+1 must never be (no lookahead on daily bars).
    """
    return (
        pd.Timestamp(as_of).normalize()
        + pd.Timedelta(days=1)
        - pd.Timedelta(1, unit="ns")
    )


@dataclass
class LstmBundle:
    """Loaded model bundle — the exact state the live strategy serves with."""

    model: Any  # models.torch_model.LSTMModel, eval mode
    scaler_x: Any  # sklearn StandardScaler (subset to input_dim if needed)
    scaler_y: Any  # sklearn StandardScaler (z -> real 5d return)
    features_list: List[str]
    sequence_length: int
    input_dim: int
    device: Any
    artifacts_dir: str


def load_lstm_bundle(artifacts_dir: str | Path, device: str = "cpu") -> LstmBundle:
    """Load the LSTM bundle exactly like ``_load_torch_model_assets``
    (core/strategies/lstm_strategy.py:143-219).

    Replicated semantics, in order: metadata-driven serve window
    (``resolve_sequence_length``, #1878), ``weights_only=True`` checkpoint
    load, ensemble ``models.0.`` prefix handling, checkpoint-driven feature
    truncation (``all_features[:input_dim]``), scaler subsetting when the
    scaler carries more features than the checkpoint
    (lstm_strategy.py:204-216). Scalers go through ``safe_joblib_load``
    (CWE-502 provenance check) — same as live (lstm_strategy.py:202-203).
    """
    import torch  # lazy — keep the attribution package importable without ML deps

    from core.ml.asset_integrity import safe_joblib_load

    # Fallback window: the module default the live strategy uses
    # (lstm_strategy.py:48 SEQUENCE_LENGTH = 60) — imported, not duplicated.
    from core.strategies.lstm_strategy import SEQUENCE_LENGTH
    from models.torch_model import LSTMModel, resolve_sequence_length

    base = Path(artifacts_dir)
    paths = {name: base / name for name in _ARTIFACT_NAMES}
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        # live logs+returns (lstm_strategy.py:155-158); offline fails LOUD
        raise LstmArtifactError(f"missing LSTM artifacts: {missing}")

    try:
        metadata = json.loads(paths["model_metadata.json"].read_text(encoding="utf-8"))
        all_features = metadata["features_list"]
        mp = metadata["model_params"]
        sequence_length = resolve_sequence_length(
            metadata, SEQUENCE_LENGTH, "LstmOffline"
        )
        dev = torch.device(device)
        state_dict = torch.load(  # nosec — mirrors lstm_strategy.py:168-170
            paths["lstm_model.pth"], map_location=dev, weights_only=True
        )
        # ensemble prefix + checkpoint-driven input_dim (lstm_strategy.py:171-188)
        first_key = next(iter(state_dict.keys()), "")
        w_key = (
            "models.0.lstm.weight_ih_l0"
            if first_key.startswith("models.")
            else "lstm.weight_ih_l0"
        )
        if w_key in state_dict:
            input_dim = int(state_dict[w_key].shape[1])
            if input_dim != mp["input_dim"]:
                logger.warning(
                    "LstmOffline: checkpoint input_dim=%d vs metadata %d; "
                    "using checkpoint (mirrors lstm_strategy.py:179-185).",
                    input_dim,
                    mp["input_dim"],
                )
            features_list = list(all_features[:input_dim])
        else:
            input_dim = int(mp["input_dim"])
            features_list = list(all_features)
        model = LSTMModel(
            input_dim, mp["hidden_dim"], mp["num_layers"], mp["output_dim"]
        ).to(dev)
        if first_key.startswith("models."):
            state_dict = {
                k.replace("models.0.", ""): v
                for k, v in state_dict.items()
                if k.startswith("models.0.")
            }
        model.load_state_dict(state_dict)
        model.eval()
        scaler_x = safe_joblib_load(str(paths["scaler_x.pkl"]))
        scaler_y = safe_joblib_load(str(paths["scaler_y.pkl"]))
        # scaler subsetting (lstm_strategy.py:204-216)
        n_scaler = getattr(
            scaler_x, "n_features_in_", len(getattr(scaler_x, "mean_", []))
        )
        if n_scaler > input_dim:
            from sklearn.preprocessing import StandardScaler

            sub = StandardScaler()
            sub.mean_ = scaler_x.mean_[:input_dim].copy()
            sub.scale_ = scaler_x.scale_[:input_dim].copy()
            sub.n_features_in_ = input_dim
            scaler_x = sub
    except LstmArtifactError:
        raise
    except Exception as exc:  # noqa: BLE001 — typed, loud offline failure
        raise LstmArtifactError(f"cannot load LSTM bundle from {base}: {exc}") from exc

    return LstmBundle(
        model=model,
        scaler_x=scaler_x,
        scaler_y=scaler_y,
        features_list=features_list,
        sequence_length=int(sequence_length),
        input_dim=input_dim,
        device=dev,
        artifacts_dir=str(base.resolve()),
    )


class LstmOfflineScorer:
    """Replicates the live per-symbol ranking-path inference
    (``_get_torch_prediction``, core/strategies/lstm_strategy.py:234-371).

    Parity is TEST-PINNED against the real live method on fixture bars
    (±1e-6). ``None`` is an ABSTENTION (#1878): short history, degraded
    features and unexpected serve errors all abstain — never a numeric
    pseudo-prediction.
    """

    def __init__(self, bundle: LstmBundle):
        self.bundle = bundle

    @property
    def history_days(self) -> int:
        """Calendar-day fetch window — mirrors ``days = seq_len + 200``
        (lstm_strategy.py:277)."""
        return self.bundle.sequence_length + 200

    def score_history(self, hist: Optional[pd.DataFrame]) -> Optional[float]:
        """Score one symbol's history frame as of its last bar.

        Mirrors lstm_strategy.py:265-371 step by step: min-rows gate
        (max(seq_len, FEATURE_WARMUP_ROWS), #1878 review Finding 1),
        vix/sentiment fallback columns, ``create_live_features``,
        ``tail(seq_len)`` + non-finite zeroing + ``scaler_x.transform``,
        model forward, ``z_to_return`` (real 5d return, #1878 Fix 2).
        """
        import torch  # lazy — see load_lstm_bundle

        from models.torch_model import (
            FEATURE_WARMUP_ROWS,
            FeatureGenerationError,
            create_live_features,
            z_to_return,
        )

        b = self.bundle
        seq_len = b.sequence_length
        min_rows = max(seq_len, FEATURE_WARMUP_ROWS)  # lstm_strategy.py:275
        if hist is None or len(hist) < min_rows:
            logger.warning(
                "LstmOffline: history missing or too short (%d < %d rows incl. "
                "feature warm-up) — abstaining (mirrors lstm_strategy.py:289-298).",
                0 if hist is None else len(hist),
                min_rows,
            )
            return None
        hist = hist.copy()
        # vix / sentiment fallback series — lstm_strategy.py:303-316 with the
        # vote path's market_data={} (agents.py:746-748)
        if "vix" not in hist.columns:
            hist["vix"] = _VIX_FALLBACK
        hist["vix"] = hist["vix"].bfill().ffill().fillna(_VIX_FALLBACK)
        if "market_news_sentiment" not in hist.columns:
            hist["market_news_sentiment"] = _SENTIMENT_FALLBACK
        hist["market_news_sentiment"] = (
            hist["market_news_sentiment"].ffill().fillna(_SENTIMENT_FALLBACK)
        )
        try:
            try:
                features_df = create_live_features(hist)
            except FeatureGenerationError as exc:
                logger.warning(
                    "LstmOffline: feature generation failed — abstaining "
                    "(ADR-SEC-03 / lstm_strategy.py:317-336): %s",
                    exc,
                )
                return None
            if features_df is None or len(features_df) < min_rows:
                logger.warning(
                    "LstmOffline: feature frame missing or too short (%d < %d "
                    "rows) — abstaining (mirrors lstm_strategy.py:337-346).",
                    0 if features_df is None else len(features_df),
                    min_rows,
                )
                return None
            # lstm_strategy.py:347-361
            X_live = (
                features_df[b.features_list]
                .tail(seq_len)
                .values.astype(np.float64, copy=True)
            )
            X_live[~np.isfinite(X_live)] = 0.0
            X_scaled = b.scaler_x.transform(X_live)
            X_tensor = torch.tensor(np.array([X_scaled]), dtype=torch.float32).to(
                b.device
            )
            with torch.no_grad():
                pred = b.model(X_tensor)
            raw_z = float(pred.cpu().numpy()[0][0])
            return z_to_return(b.scaler_y, raw_z, context="LstmOffline")
        except Exception as exc:  # noqa: BLE001 — serve error => abstention
            logger.warning(
                "LstmOffline: prediction failed — abstaining (mirrors "
                "lstm_strategy.py:362-371): %s",
                exc,
                exc_info=True,
            )
            return None

    def predict(self, provider: Any, symbol: str, as_of: Any) -> Optional[float]:
        """One PIT score via the provider's ``get_data(symbol, end, days)``
        contract (core/data_provider.py get_data; ReplayBarsProvider,
        replay.py:112-127) — the same call shape as the live executor path
        (lstm_strategy.py:285-288). The slice runs through the END of the
        ``as_of`` calendar day (``_pit_day_end``) so intraday-stamped daily
        bars stay included for their own day, exactly as live."""
        hist = provider.get_data(symbol, _pit_day_end(as_of), self.history_days)
        return self.score_history(hist)

    def cross_section(
        self, provider: Any, as_of: Any, symbols: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """Per-date cross-section scores, replicating ``update_lstm_rankings``
        (lstm_strategy.py:373-472) for the offline panel.

        * tradeability gate: a symbol without a bar AT ``as_of`` has no
          quote and never enters the ranking (mirrors the snapshot /
          ``latest_trade`` gate, lstm_strategy.py:386-394 — PIT-honest for
          delistings);
        * abstentions are dropped, never ranked (lstm_strategy.py:398-408);
        * ADR-ML-COLLAPSE-01 (lstm_strategy.py:435-472): a cross-section
          whose std is below the live floor carries NO ordering — empty
          ranking, no capital-relevant "Top-N" from input order.

        The returned dict preserves descending-score order (the live
        ``sorted(..., reverse=True)``, lstm_strategy.py:410).
        """
        from core.strategies.lstm_strategy import _LSTM_COLLAPSE_STD_FLOOR

        as_of_norm = pd.Timestamp(as_of).normalize()
        universe = (
            list(symbols)
            if symbols is not None
            else list(getattr(provider, "symbols", []))
        )
        cache: List[Tuple[str, float]] = []
        for symbol in universe:
            # slice through the END of the as_of day — intraday-stamped bars
            # (04:00/05:00 UTC) must stay tradeable on their own day (Rev 3)
            hist = provider.get_data(symbol, _pit_day_end(as_of), self.history_days)
            if hist is None or len(hist) == 0:
                continue
            if pd.Timestamp(hist.index.max()).normalize() != as_of_norm:
                # no bar at the ranking date -> untradeable, not in the
                # cross-section (lstm_strategy.py:386-394)
                continue
            pred = self.score_history(hist)
            if pred is None:
                logger.warning(
                    "LstmOffline [%s]: prediction abstained — symbol excluded "
                    "from the %s ranking (mirrors lstm_strategy.py:398-408).",
                    symbol,
                    as_of_norm.date(),
                )
                continue
            cache.append((symbol, float(pred)))
        ranked = sorted(cache, key=lambda x: x[1], reverse=True)  # :410
        if len(ranked) >= 2:
            spread = float(np.std([p for _, p in ranked]))
            if spread < _LSTM_COLLAPSE_STD_FLOOR:
                logger.error(
                    "LstmOffline: MODEL COLLAPSE @ %s — cross-section std=%.3e "
                    "below floor %.0e over %d symbols; no ranking this date "
                    "(ADR-ML-COLLAPSE-01, lstm_strategy.py:435-472).",
                    as_of_norm.date(),
                    spread,
                    _LSTM_COLLAPSE_STD_FLOOR,
                    len(ranked),
                )
                return {}
        return dict(ranked)


class OfflineLstmStrategy:
    """Registry strategy for ``replay_votes(strategy=...)`` (replay.py:218-243),
    backed by the offline scorer.

    Satisfies the replay-registry contract (replay.py:134-149):
    ``data_provider`` for the DrawdownGuard/Momentum history path and an
    ``evaluate_for_symbol`` whose SignalEvent carries the raw
    ``lstm_prediction`` — the REAL LSTMSignalAgent (agents.py:746-779) and
    RLConfidenceAgent (agents.py:883-912) build their votes from it through
    the genuine production mappings.

    Position-less book: ``in_pos`` is always False offline, so the action is
    BUY for top-N members and HOLD otherwise — exactly the live evaluate
    contract for a flat book (lstm_strategy.py:565-567); the SELL/smart-exit
    branch needs portfolio state and is not offline-attributable.
    """

    def __init__(
        self, provider: Any, scorer: LstmOfflineScorer, top_n: Optional[int] = None
    ):
        from core.strategies.lstm_strategy import LSTM_DYNAMIC_TOP_N

        self.data_provider = provider
        self._scorer = scorer
        self._top_n = int(top_n) if top_n is not None else LSTM_DYNAMIC_TOP_N
        self._xsec_cache: Dict[pd.Timestamp, Dict[str, float]] = {}

    def _cross_section_for(self, current_time: Any) -> Dict[str, float]:
        key = pd.Timestamp(current_time).normalize()
        if key not in self._xsec_cache:
            self._xsec_cache[key] = self._scorer.cross_section(self.data_provider, key)
        return self._xsec_cache[key]

    def score_panel(self) -> pd.DataFrame:
        """The raw per-date cross-sections this strategy served during the
        replay, as a ``ts, symbol, score`` panel (Rev 3, PR #2421).

        This is the offline twin of the live ``_lstm_rank_cache`` the
        ``LstmPanelStore`` observes: RAW model scores per replay date — the
        book-quality ``lstm`` ranking consumes them directly instead of
        deriving the order from the tanh-mapped vote scores (monotone-equal
        order, but raw scores cannot saturate into artificial ties). Empty
        cross-sections (all-abstention dates, collapse guard) contribute no
        rows — the caller surfaces an empty panel honestly as unavailable.
        """
        rows = [
            (ts, symbol, float(score))
            for ts in sorted(self._xsec_cache)
            for symbol, score in self._xsec_cache[ts].items()
        ]
        return pd.DataFrame(rows, columns=["ts", "symbol", "score"])

    async def evaluate_for_symbol(
        self,
        symbol: str,
        ohlc_data: Dict[str, float],
        market_data: Dict,
        current_time: Any,
    ):
        xsec = self._cross_section_for(current_time)
        rank: Optional[int] = None
        in_top_n = False
        pred = 0.0
        # dict preserves descending-score order — mirrors
        # _get_rank_and_in_top_n (lstm_strategy.py:511-515)
        for i, (s, p) in enumerate(xsec.items()):
            if s == symbol:
                rank, in_top_n, pred = i + 1, i < self._top_n, p
                break
        if rank is None:
            return None  # abstention — lstm_strategy.py:546-548
        from core.cloud_logger import DecisionContext
        from core.events import SignalEvent

        action = "BUY" if in_top_n else "HOLD"  # flat book, lstm_strategy.py:565-567
        ctx = DecisionContext(
            symbol=symbol,
            action=action,
            lstm_prediction=float(pred),  # raw continuous pred (#1969)
            current_price=float(ohlc_data.get("close", 0.0) or 0.0),
            reasoning_summary=(
                f"LSTM offline replay (#2409): rank={rank}, top_n={in_top_n}"
            ),
            model_version_id="lstm_offline_2409",
        )
        return SignalEvent(symbol=symbol, action=action, decision_context=ctx)
