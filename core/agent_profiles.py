# core/agent_profiles.py
# #4874: one profile per Round-Table agent — the single source for the console
# (served with the agents view, core/trading_settings.agents_view) and for the agent
# pages in docs/2_agentic_operations/agents/ (written by scripts/gen_agent_profiles.py).
#
# Rule (owner 28.10.2026): no statement without a code anchor. Every claim names its
# evidence as "path:line[-line]"; tests/unit/test_agent_profiles_4874.py checks that
# each anchor exists and that every settings default equals the literal in settings.py.
# Measured values only from committed result artefacts, with date and scope; anything
# else reads "Not measured". Pure data, no imports from the finance core.

from __future__ import annotations

from typing import Any, Dict, List

_S = "ai_trading_bot/settings.py"
_AG = "ai_trading_bot/core/round_table/agenten"
_RTR0 = "docs/rtr/rtr0-attribution-benchmark/results/RESULTS.md"
_LOO = "docs/rtr/rtr0-mom-lstm-vix-loo/results/RESULTS.md"

# Scope lines for the two committed result artefacts (§5.0b: every number carries its scope).
_RTR0_SCOPE = (
    "RTR-0 replay, generated 2026-07-22 (git de5289c9af): 10 symbols, 2024-06-03 to "
    "2026-03-18, 10 bps cost, 5-bar IC horizon, flag posture as recorded in the file"
)
_LOO_SCOPE = (
    "RTR-0 3-agent leave-one-out, committed 2026-10-03 (git 8c1a55b6): about 500 symbols, "
    "2025-07-15 to 2026-08-01, 261 trading days, marked preliminary in the file"
)

_KPI_DIRECTIONAL = (
    "Per-date cross-sectional Spearman IC of the vote against the 5-bar forward return "
    "(HAC/Newey-West t), hit rate, and the leave-one-out change in consensus Sharpe."
)
_KPI_ANCHORS = [
    "ai_trading_bot/core/analysis/attribution/metrics.py:155-335",
    "ai_trading_bot/core/analysis/attribution/metrics.py:386-420",
    "ai_trading_bot/core/analysis/attribution/metrics.py:555-587",
]

_ALL_EDITIONS = "All editions (every tier is granted all 14 agents)"
_TIER = "ai_trading_bot/core/entitlement/tier.py:60-135"


def _setting(key: str, default: str, line: int, effect: str) -> Dict[str, str]:
    return {"key": key, "default": default, "ref": f"{_S}:{line}", "effect": effect}


def _ev(*items: str) -> List[str]:
    return list(items) + [_TIER, *_KPI_ANCHORS]


PROFILES: Dict[str, Dict[str, Any]] = {
    "FundamentalsAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: fixed rules on the filing data.",
        "flow_role": "Directional voter (counts in the consensus mean)",
        "responsibility": "Rates the business: revenue growth year over year and net margin.",
        "methods": (
            "Starts at 0.5; revenue growth >= 15 % adds 0.2, <= 0 % subtracts 0.2; net margin "
            ">= 20 % adds 0.2, <= 0 % subtracts 0.2. The thresholds are an editorial starting "
            "calibration, not a fitted model."
        ),
        "literature": [],
        "data": (
            "Point-in-time fundamentals cache built from SEC EDGAR (financials_feed.py#L717) company facts (annual), read "
            "as of the evaluation date."
        ),
        "settings": [
            _setting(
                "FUNDAMENTALS_AGENT_ENABLED", "true", 1875, "Off: the agent abstains."
            ),
            _setting(
                "FUNDAMENTALS_AGENT_WEIGHT",
                "0.25",
                1817,
                "Weight in the consensus mean; 0 keeps the reasoning in the record but the vote does not count.",
            ),
        ],
        "default_state": "On, counts with weight 0.25",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains (weight 0) when no fundamentals or neither growth nor margin exist.",
        "kpi": _KPI_DIRECTIONAL,
        "measured": [
            f"Not measured. {_RTR0_SCOPE}: abstained on 100 % of replayed bars ({_RTR0})."
        ],
        "evidence": _ev(
            f"{_AG}/fundamentals.py:53-58",
            f"{_AG}/fundamentals.py:78-126",
            f"{_AG}/_fundamentaldaten.py:31-34",
            f"{_AG}/_fundamentaldaten.py:36-81",
            "ai_trading_bot/core/report/financials_feed.py:717",
            "ai_trading_bot/core/round_table/agenten/_basis.py:38-52",
        ),
    },
    "ValuationAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: fixed rules on the filing data.",
        "flow_role": "Directional voter (counts in the consensus mean when its weight is above 0)",
        "responsibility": "Rates the price against earnings growth and the balance sheet.",
        "methods": (
            "Trailing PEG = price-to-earnings / trailing earnings-per-share growth in %, plus "
            "debt-to-equity. PEG < 1 with debt-to-equity < 1 (or unknown) scores 0.7, PEG > 3 "
            "scores 0.3, otherwise no score. Uses trailing, not forecast, growth because no "
            "local estimates exist. Thresholds are an editorial starting calibration."
        ),
        "literature": [],
        "data": "Same point-in-time SEC EDGAR (financials_feed.py#L717) fundamentals cache, plus the current close price.",
        "settings": [
            _setting(
                "VALUATION_AGENT_ENABLED", "true", 1879, "Off: the agent abstains."
            ),
            _setting(
                "VALUATION_AGENT_WEIGHT",
                "0.0",
                1820,
                "Weight in the consensus mean; at the default 0.0 the reasoning is recorded but the vote does not count.",
            ),
            _setting(
                "VALUATION_MULTIMETRIC_ENABLED",
                "False",
                123,
                "On: when PEG is undefined, falls back to price-to-sales or price-to-book.",
            ),
        ],
        "default_state": "On, weight 0.0 (speaks, does not count)",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains when P/E is missing or earnings growth is <= 0 (PEG undefined).",
        "kpi": _KPI_DIRECTIONAL,
        "measured": [
            f"Not measured. {_RTR0_SCOPE}: abstained on 100 % of replayed bars ({_RTR0})."
        ],
        "evidence": _ev(
            f"{_AG}/valuation.py:49-53",
            f"{_AG}/valuation.py:71-76",
            f"{_AG}/valuation.py:92-132",
            f"{_AG}/valuation.py:157-192",
            f"{_AG}/_fundamentaldaten.py:31-34",
        ),
    },
    "QualityAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: the composite is a pure calculation.",
        "flow_role": "Directional voter (counts in the consensus mean when switched on)",
        "responsibility": "Rates how sound the business is against yesterday's cross-section.",
        "methods": (
            "Composite of the Novy-Marx gross-profits-to-assets ratio and the 9-point Piotroski "
            "F-score (current and prior filing), blended via z-scores with editorial centres and "
            "scales; the vote is the percentile rank against the previous session."
        ),
        "literature": [
            "Novy-Marx, R. (2013). The other side of value: The gross profitability premium. "
            "Journal of Financial Economics 108(1).",
            "Piotroski, J. D. (2000). Value investing: The use of historical financial statement "
            "information to separate winners from losers. Journal of Accounting Research 38.",
        ],
        "data": "Point-in-time SEC EDGAR (financials_feed.py#L717) fundamentals cache (no extra data source).",
        "settings": [
            _setting(
                "QUALITY_AGENT_ENABLED",
                "false",
                1896,
                "Off: the agent abstains (dark).",
            ),
            _setting(
                "QUALITY_AGENT_WEIGHT",
                "0.30",
                1841,
                "Weight in the consensus mean once switched on.",
            ),
        ],
        "default_state": "Off",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains without a composite score or a reference distribution from a completed cycle.",
        "kpi": _KPI_DIRECTIONAL,
        "measured": ["Not measured (no committed result artefact)."],
        "evidence": _ev(
            f"{_AG}/quality.py:58-66",
            f"{_AG}/quality.py:104-151",
            "ai_trading_bot/core/quality_score.py:1-35",
        ),
    },
    "SpecialistAlphaAgent": {
        "type": "AI language model",
        "repeatable": (
            "Depends on the provider: the local Ollama path decodes with temperature 0.0 and a "
            "fixed seed; the Gemini path uses temperature 0.3 and can vary for the same input. "
            "The mapping from report to vote is a fixed rule."
        ),
        "flow_role": "Directional voter (counts in the consensus mean)",
        "responsibility": "Turns the language-model research report on the stock into a vote.",
        "methods": (
            "Score = report sentiment (0-100) / 100, plus 0.05 for a buy and minus 0.05 for a "
            "sell recommendation."
        ),
        "literature": [],
        "data": "Cached research report from the stock specialist (core/stock_specialist.py), written by the configured language model.",
        "settings": [
            _setting(
                "SPECIALIST_ALPHA_AGENT_ENABLED",
                "true",
                1857,
                "Off: the agent abstains.",
            ),
            _setting(
                "SPECIALIST_ALPHA_WEIGHT",
                "0.15",
                1776,
                "Weight in the consensus mean; 0 makes the agent dormant.",
            ),
        ],
        "default_state": "On, counts with weight 0.15 once a report exists",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains (warm-up) until a report for the stock is cached.",
        "kpi": _KPI_DIRECTIONAL,
        "measured": [
            f"Not measured. {_RTR0_SCOPE}: excluded, dormant at the time ({_RTR0})."
        ],
        "evidence": _ev(
            f"{_AG}/specialist_alpha.py:65-67",
            f"{_AG}/specialist_alpha.py:76-104",
            f"{_AG}/specialist_alpha.py:110-140",
            "ai_trading_bot/core/stock_specialist.py:88-90",
            "ai_trading_bot/core/stock_specialist.py:1253",
            "ai_trading_bot/core/llm/provider.py:58-66",
            "ai_trading_bot/core/llm/provider.py:113-115",
        ),
    },
    "NewsSentimentAgent": {
        "type": "AI model (text classifier)",
        "repeatable": (
            "A classifier, not a text generator; bit-identical repeats are not verified. The "
            "fallback VADER lexicon is a fixed rule."
        ),
        "flow_role": "Directional voter (counts in the consensus mean)",
        "responsibility": "Rates the tone of the latest point-in-time headlines on the stock.",
        "methods": (
            "Scores up to 8 headlines with the configured classifier (default "
            "mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis), else the "
            "vendored VADER lexicon; the result is cached for 300 s per headline set."
        ),
        "literature": [],
        "data": "Google News RSS headlines, filtered to the evaluation time.",
        "settings": [
            _setting(
                "NEWS_SENTIMENT_AGENT_ENABLED", "true", 1860, "Off: the agent abstains."
            ),
            _setting(
                "NEWS_SENTIMENT_WEIGHT", "0.20", 1825, "Weight in the consensus mean."
            ),
            _setting(
                "NEWS_SENTIMENT_NLP_ENABLED",
                "True",
                1946,
                "Off: the agent is dormant (the old ticker-only language-model guess was removed).",
            ),
            _setting(
                "NEWS_SENTIMENT_MAX_HEADLINES",
                "8",
                1953,
                "Maximum headlines scored per vote.",
            ),
            _setting(
                "NEWS_SENTIMENT_MODEL",
                "fin-distilroberta",
                1965,
                "Classifier used for the headlines.",
            ),
        ],
        "default_state": "On, counts with weight 0.20 when headlines exist",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains without headlines or without a working classifier.",
        "kpi": _KPI_DIRECTIONAL,
        "measured": [
            f"Not measured. {_RTR0_SCOPE}: excluded, no point-in-time news history offline ({_RTR0})."
        ],
        "evidence": _ev(
            f"{_AG}/news_sentiment.py:110-124",
            f"{_AG}/news_sentiment.py:144-222",
            f"{_AG}/news_sentiment.py:244",
            "ai_trading_bot/core/nlp/news_sentiment.py:9-10",
            "ai_trading_bot/core/nlp/news_sentiment.py:44-48",
            "ai_trading_bot/core/nlp/headlines.py:2",
            "ai_trading_bot/core/nlp/headlines.py:133",
        ),
    },
    "MomentumAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: a formula on daily closes.",
        "flow_role": "Directional voter (counts in the consensus mean)",
        "responsibility": "Rates the price trend over the past year, leaving out the last month.",
        "methods": (
            "12-1 momentum = close 20 bars ago / close 252 bars ago - 1; with 40 to 251 bars "
            "the first available bar is the start (deviation from the textbook 12 months). "
            "Score = 0.5 + 0.5 * tanh(momentum / 0.60) with smoothing on."
        ),
        "literature": [
            "Carhart, M. M. (1997). On persistence in mutual fund performance. Journal of Finance "
            "52(1). One-year return lagged one month; the code measures it on daily closes (20 "
            "bars skipped, 252 bars back) without volatility adjustment."
        ],
        "data": "Daily bars from the engine data provider (Alpaca primary), 365 calendar days.",
        "settings": [
            _setting(
                "MOMENTUM_AGENT_ENABLED", "true", 1851, "Off: the agent abstains."
            ),
            _setting(
                "MOMENTUM_AGENT_WEIGHT", "0.45", 1807, "Weight in the consensus mean."
            ),
            _setting(
                "MOMENTUM_SCORE_SMOOTH_ENABLED",
                "true",
                1628,
                "On: tanh mapping without ties; off: hard clamp of 0.5 + momentum / 0.60.",
            ),
            _setting(
                "MOMENTUM_FALLBACK_ABSTAIN_ENABLED",
                "True",
                1706,
                "On: with fewer than 40 closes the agent abstains instead of voting today's move.",
            ),
        ],
        "default_state": "On, counts with weight 0.45",
        "edition": _ALL_EDITIONS,
        "missing_data": "Fewer than 40 daily closes: abstains (default).",
        "kpi": _KPI_DIRECTIONAL,
        "measured": [
            f"{_RTR0_SCOPE}: IC +0.0045, t +0.13, leave-one-out Sharpe change -0.550, class 'SCHADET' (harms) ({_RTR0}).",
            f"{_LOO_SCOPE}: IC +0.0287, t +1.29, not FDR-significant, leave-one-out Sharpe change +1.237 ({_LOO}).",
        ],
        "evidence": _ev(
            f"{_AG}/momentum.py:102-114",
            f"{_AG}/momentum.py:151-181",
            f"{_AG}/momentum.py:207-231",
            "ai_trading_bot/core/data_provider.py:2",
        ),
    },
    "TrendAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: a formula on daily closes.",
        "flow_role": "Directional voter (counts in the consensus mean when switched on)",
        "responsibility": "Rates the trend over weeks to months.",
        "methods": (
            "Mean of three normalised, saturated MACD signals (8/24, 16/48, 32/96 days); "
            "score = 0.5 + 0.5 * trend. Needs 420 bars of history."
        ),
        "literature": [
            "Baz, J., Granger, N., Harvey, C. R., Le Roux, N. and Rattray, S. (2015). Dissecting "
            "investment strategies in the cross section and time series. SSRN working paper."
        ],
        "data": "Daily closes from the shared feature snapshot.",
        "settings": [
            _setting(
                "TREND_AGENT_ENABLED", "false", 1888, "Off: the agent abstains (dark)."
            ),
            _setting(
                "TREND_AGENT_WEIGHT",
                "0.0",
                1832,
                "Weight in the consensus mean; the flag alone gives no weight, both are needed.",
            ),
        ],
        "default_state": "Off",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains when the history is shorter than 420 bars or the value is not finite.",
        "kpi": _KPI_DIRECTIONAL,
        "measured": ["Not measured (no committed result artefact)."],
        "evidence": _ev(
            f"{_AG}/trend.py:48-55",
            f"{_AG}/trend.py:91-124",
            "ai_trading_bot/core/round_table/features.py:33-45",
        ),
    },
    "VolumeConfirmationAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: a formula on daily volume.",
        "flow_role": "Directional voter (counts in the consensus mean when switched on)",
        "responsibility": "Checks whether recent volume confirms the move.",
        "methods": (
            "Ratio of 5-day to 20-day average volume; score = 0.5 + 0.5 * tanh(ratio - 1). "
            "Scale 1.0 is an editorial starting calibration."
        ),
        "literature": [],
        "data": "Daily volume from the shared feature snapshot.",
        "settings": [
            _setting(
                "VOLUME_CONFIRM_AGENT_ENABLED",
                "false",
                1891,
                "Off: the agent abstains (dark).",
            ),
            _setting(
                "VOLUME_CONFIRM_AGENT_WEIGHT",
                "0.0",
                1833,
                "Weight in the consensus mean; the flag alone gives no weight, both are needed.",
            ),
        ],
        "default_state": "Off",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains without a feature snapshot or a positive, finite ratio.",
        "kpi": _KPI_DIRECTIONAL,
        "measured": ["Not measured (no committed result artefact)."],
        "evidence": _ev(
            f"{_AG}/volume_confirmation.py:43-55",
            f"{_AG}/volume_confirmation.py:85-119",
        ),
    },
    "VIXAwareRiskAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: a rank against yesterday's distribution.",
        "flow_role": "Size input (out of the direction mean while the implied-vol forecast is on)",
        "responsibility": "Rates the stock's expected swing and feeds position sizing.",
        "methods": (
            "Score = 1 - percentile of the stock's ATM 30-day implied volatility against the "
            "previous session's cross-section. Fallback when switched off: market VIX through "
            "a sigmoid around 25."
        ),
        "literature": [],
        "data": "Implied volatility from the Alpaca option chain.",
        "settings": [
            _setting(
                "VIX_RISK_AGENT_ENABLED", "true", 1863, "Off: the agent abstains."
            ),
            _setting(
                "VIXAWARE_IMPLIED_VOL_ENABLED",
                "true",
                1664,
                "On: per-stock implied volatility; off: market-wide VIX, the same for every stock.",
            ),
            _setting(
                "IMPLIED_VOL_FORECAST_ENABLED",
                "true",
                414,
                "On: the agent sizes and leaves the direction mean; off: it votes in the mean.",
            ),
            _setting(
                "VIX_SIZE_INFLUENCE",
                "1.0",
                438,
                "0 = no effect on size (equal weights), 1 = the full volatility-targeting effect.",
            ),
            _setting(
                "VIX_RISK_WEIGHT",
                "0.45",
                1810,
                "Consensus weight, used only while the implied-vol forecast is off.",
            ),
        ],
        "default_state": "On, as size input",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains without implied volatility or a reference distribution.",
        "kpi": (
            "As size input: no dedicated size KPI is implemented. As a direction voice: "
            + _KPI_DIRECTIONAL
        ),
        "measured": [
            f"{_LOO_SCOPE}, measured as a direction voice: IC -0.0361, t -1.35, leave-one-out "
            f"Sharpe change -0.754 ({_LOO}).",
        ],
        "evidence": _ev(
            f"{_AG}/vix_aware.py:206-236",
            f"{_AG}/vix_aware.py:253-300",
            f"{_S}:431-438",
            "ai_trading_bot/core/round_table/consensus.py:183-196",
            "ai_trading_bot/core/implied_vol.py:24",
        ),
    },
    "RegimeDetectionAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: a sigmoid on the market VIX.",
        "flow_role": "Conditioner (not in the mean; damps a buy-leaning consensus in risk-off)",
        "responsibility": "Reads the market-wide risk mood and scales how strongly buy signals count.",
        "methods": (
            "Score = 1 / (1 + exp((VIX - 25) / 10)); without a VIX value a regime label stands "
            "in. Below 0.5 the buy-leaning consensus is pulled towards neutral by up to 50 %."
        ),
        "literature": [],
        "data": "Market VIX or regime label from the market regime model (VIX, SPY fallback).",
        "settings": [
            _setting(
                "REGIME_DETECTION_AGENT_ENABLED", "true", 1871, "Off: no conditioning."
            ),
            _setting(
                "REGIME_CONDITIONER_ENABLED",
                "True",
                1722,
                "On: VIX sigmoid and gradual damping; off: one-bar open/close proxy and the old momentum halving.",
            ),
            _setting(
                "REGIME_VIX_MIDPOINT", "25.0", 1738, "VIX level that maps to 0.5."
            ),
            _setting(
                "REGIME_RISKOFF_DAMP_MAX",
                "0.5",
                1730,
                "Maximum pull towards neutral at full risk-off.",
            ),
        ],
        "default_state": "On",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains without VIX and regime label; then nothing is conditioned.",
        "kpi": "Leave-one-out change in consensus Sharpe (its effect runs through the consensus).",
        "measured": [
            f"{_RTR0_SCOPE}: IC -0.0099, t -0.57, leave-one-out Sharpe change +0.000, class "
            f"'RAUSCHEN' (noise) ({_RTR0}). The file does not record REGIME_CONDITIONER_ENABLED.",
        ],
        "evidence": _ev(
            f"{_AG}/regime.py:96-157",
            f"{_AG}/regime.py:159-168",
            "ai_trading_bot/core/round_table/consensus.py:85-104",
            "ai_trading_bot/core/round_table/consensus.py:389-406",
            "ai_trading_bot/core/market_regime.py:30",
        ),
    },
    "UpsideSkewAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: a rank against yesterday's distribution.",
        "flow_role": "Directional voter (counts in the consensus mean when switched on)",
        "responsibility": "Reads whether the options market pays up for upside on the stock.",
        "methods": (
            "25-delta risk reversal = IV(25-delta call) - IV(25-delta put); the vote is its "
            "percentile rank against the previous session."
        ),
        "literature": [],
        "data": "25-delta call and put implied volatility from the Alpaca option chain.",
        "settings": [
            _setting(
                "UPSIDE_SKEW_AGENT_ENABLED",
                "false",
                356,
                "Off: the agent abstains (dark).",
            ),
            _setting(
                "UPSIDE_SKEW_WEIGHT",
                "0.30",
                359,
                "Weight in the consensus mean once switched on.",
            ),
        ],
        "default_state": "Off",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains without a risk reversal or a reference distribution.",
        "kpi": _KPI_DIRECTIONAL,
        "measured": ["Not measured (no committed result artefact)."],
        "evidence": _ev(
            f"{_AG}/upside_skew.py:33-58",
            f"{_AG}/upside_skew.py:78-124",
            "ai_trading_bot/core/options_skew.py:1-15",
            "ai_trading_bot/core/options_skew.py:82",
        ),
    },
    "LSTMSignalAgent": {
        "type": "AI model (LSTM neural network)",
        "repeatable": "Bit-identical repeats of the model inference are not verified.",
        "flow_role": "Directional voter (counts in the consensus mean)",
        "responsibility": "Places the stock in today's ranking of the trained price model.",
        "methods": (
            "Score = the stock's percentile in the daily LSTM ranking panel (a stock in the top "
            "10 % scores 0.9). Needs at least 30 ranked stocks with distinct scores."
        ),
        "literature": [],
        "data": "Daily LSTM ranking panel; model lstm_model_v2.pth (5-day return predictor).",
        "settings": [
            _setting(
                "LSTM_SIGNAL_AGENT_ENABLED", "true", 1854, "Off: the agent abstains."
            ),
            _setting(
                "LSTM_SIGNAL_WEIGHT", "0.15", 1811, "Weight in the consensus mean."
            ),
            _setting(
                "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK",
                "True",
                2350,
                "On: votes the ranking percentile; off: votes the single-stock prediction.",
            ),
        ],
        "default_state": "On, counts with weight 0.15",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains when the stock is not in the panel or the panel is too thin.",
        "kpi": _KPI_DIRECTIONAL,
        "measured": [
            f"{_LOO_SCOPE}: IC +0.0522, t +4.21, FDR-significant, leave-one-out Sharpe change "
            f"+0.587 ({_LOO}).",
        ],
        "evidence": _ev(
            f"{_AG}/lstm_signal.py:58-66",
            f"{_AG}/lstm_signal.py:85-113",
            f"{_AG}/lstm_signal.py:129-183",
            f"{_AG}/lstm_signal.py:296-311",
            "README.md:123",
        ),
    },
    "RLConfidenceAgent": {
        "type": "AI model (reinforcement-learning policy)",
        "repeatable": "Bit-identical repeats of the model inference are not verified.",
        "flow_role": "Directional voter (muted by default)",
        "responsibility": "Reads the direction of the registered reinforcement-learning strategy.",
        "methods": (
            "Score = 0.5 + 0.5 * tanh(pred / 2), where pred is the prediction field of the "
            "active strategy's signal."
        ),
        "literature": [],
        "data": "Signal of the registered reinforcement-learning strategy (ACTIVE_STRATEGY); model rl_agent_v5.zip (RecurrentPPO).",
        "settings": [
            _setting(
                "RL_CONFIDENCE_AGENT_ENABLED", "true", 1883, "Off: the agent abstains."
            ),
            _setting(
                "RL_CONFIDENCE_WEIGHT",
                "0.0",
                1787,
                "At 0.0 the vote is muted and fully excluded from the consensus.",
            ),
        ],
        "default_state": "On, muted (weight 0.0)",
        "edition": _ALL_EDITIONS,
        "missing_data": "Abstains when the registry or the strategy returns no signal.",
        "kpi": _KPI_DIRECTIONAL,
        "measured": [
            f"Not measured. {_RTR0_SCOPE}: abstained on 100 % of replayed bars ({_RTR0})."
        ],
        "evidence": _ev(
            f"{_AG}/rl_confidence.py:63-74",
            f"{_AG}/rl_confidence.py:96-124",
            f"{_AG}/rl_confidence.py:151-170",
            "README.md:124",
        ),
    },
    "DrawdownGuardAgent": {
        "type": "Deterministic",
        "repeatable": "Yes: a formula on daily closes.",
        "flow_role": "Veto on new buys (not in the mean)",
        "responsibility": "Measures how far the price is below its recent high and can block a new buy.",
        "methods": (
            "Drawdown = (highest close of the last 30 trading days - current close) / that high; "
            "score = 1 - 5 * drawdown. Veto when the drawdown exceeds 0.07 (0.05 on the one-bar "
            "fallback), or the severe threshold when the conditioner is on."
        ),
        "literature": [],
        "data": "Daily bars from the engine data provider (Alpaca primary).",
        "settings": [
            _setting(
                "DRAWDOWN_GUARD_AGENT_ENABLED",
                "true",
                1867,
                "Off: no score and no veto.",
            ),
            _setting(
                "DRAWDOWN_GUARD_VETO_ENABLED",
                "True",
                1608,
                "Off: the agent still scores but never vetoes.",
            ),
            _setting(
                "DRAWDOWN_GUARD_CONDITIONER_ENABLED",
                "False",
                1584,
                "On: vetoes only above the severe threshold; the moderate band damps conviction instead.",
            ),
            _setting(
                "DRAWDOWN_SEVERE_VETO_THRESHOLD",
                "0.25",
                1595,
                "Severe threshold used with the conditioner on.",
            ),
        ],
        "default_state": "On, veto active",
        "edition": _ALL_EDITIONS,
        "missing_data": "Fewer than 5 closes: judges today's high/low only; invalid prices: abstains.",
        "kpi": "Leave-one-out change in consensus Sharpe (covers the veto path); IC and hit rate of the score.",
        "measured": [
            f"{_RTR0_SCOPE}, conditioner off, thresholds 0.07/0.05: IC -0.0684, t -2.03, "
            f"leave-one-out Sharpe change -0.923, class 'SCHADET' (harms) ({_RTR0}).",
            f"{_LOO_SCOPE}: IC -0.0284, t -1.72, leave-one-out Sharpe change -0.620 ({_LOO}).",
        ],
        "evidence": _ev(
            f"{_AG}/drawdown_guard.py:30-60",
            f"{_AG}/drawdown_guard.py:164-191",
            f"{_AG}/drawdown_guard.py:199-237",
            "ai_trading_bot/core/round_table/consensus.py:183",
            "ai_trading_bot/core/data_provider.py:2",
        ),
    },
}


def profile_for(name: str) -> Dict[str, Any]:
    """The profile of one agent, or {} for a name without one (e.g. SkewSizeTilt)."""
    return PROFILES.get(name, {})


def render_markdown(name: str) -> str:
    """The profile as a markdown section for docs/2_agentic_operations/agents/*.md."""
    p = PROFILES[name]
    lines: List[str] = [
        "## Profile",
        "",
        f"- **Type:** {p['type']}. {p['repeatable']}",
        f"- **Role in the flow:** {p['flow_role']}",
        f"- **Responsibility:** {p['responsibility']}",
        f"- **Method:** {p['methods']}",
        f"- **Data:** {p['data']}",
        f"- **Default:** {p['default_state']}",
        f"- **Edition:** {p['edition']}",
        f"- **Missing data:** {p['missing_data']}",
        f"- **Engine ID (logs):** `{name}`",
        "",
        "| Setting | Default | Effect |",
        "|---|---|---|",
    ]
    for s in p["settings"]:
        lines.append(
            f"| `{s['key']}` | `{s['default']}` (`{s['ref']}`) | {s['effect']} |"
        )
    lines += ["", f"**KPI:** {p['kpi']}", ""]
    lines += [f"- {m}" for m in p["measured"]]
    if p["literature"]:
        lines += ["", "**Literature:**", ""] + [f"- {ref}" for ref in p["literature"]]
    lines += ["", "**Evidence:** " + ", ".join(f"`{e}`" for e in p["evidence"])]
    return "\n".join(lines)
