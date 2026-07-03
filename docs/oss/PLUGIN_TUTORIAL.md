# Creating a Custom Strategy Plugin

Welcome to the AAAgents OSS ecosystem! One of the core features of the "AAAgents" architecture is the `PluginRegistry`. This allows you to add custom AI/Algorithmic trading strategies without modifying the core `Round Table V2` engine.

## 1. The Architecture

The AAAgents engine uses a "Round Table" of AI Agents. Each agent votes on a given stock symbol between `0.0` (Strong Sell) and `1.0` (Strong Buy). A central `ConsensusEngine` aggregates these votes using weighted averages.

Your custom strategy will be instantiated as an Agent that participates in this Round Table.

## 2. Directory Structure

All custom plugins reside in the `plugins/round_table/` directory at the root of the Backend.

```text
ai_trading_bot/
├── core/
├── plugins/
│   └── round_table/             <- Your code goes here
│       └── my_custom_agent.py   <- Your strategy
├── main.py
```

## 3. The State Contract — Read This First

> [!IMPORTANT]
> The `vote()` method receives a `SymbolEvalState` TypedDict. This state contains **only scalar values** — no DataFrames, no numpy arrays. This is a hard architectural rule enforced by `core/orchestration/graph.py`.

**What the state actually contains:**

```python
class SymbolEvalState(TypedDict):
    symbol: str                          # e.g. "AAPL"
    ohlc: Dict[str, float]               # {"open": 150.0, "high": 152.5,
                                         #  "low": 149.0, "close": 151.8,
                                         #  "volume": 1_200_000.0}
    market_data_keys: List[str]          # Redis keys pointing to cached market data
    current_time: str                    # ISO 8601, e.g. "2026-05-05T08:00:00+00:00"
    signal: Optional[Any]                # Set by the engine after voting — read-only
    error: Optional[str]                 # Set by the engine on failure — read-only
    round_table_scores: Optional[List]   # Set after consensus — read-only
    consensus_ranking: Optional[float]   # Set after consensus — read-only
```

**In `vote()`, read only these fields:**
- `state["symbol"]` — the ticker symbol
- `state["ohlc"]` — the 5 OHLCV scalars for the current bar
- `state.get("current_time", "")` — the bar timestamp as ISO string

> [!WARNING]
> `historical_data`, `news_articles`, `macro_data`, and `portfolio_state` do **not exist** in `SymbolEvalState`. Calling `state.get("historical_data")` always returns `None`. An agent built on those fields will silently produce `score=0.5` on every tick with no error or warning.

## 4. Writing Your First Agent

Create `plugins/round_table/my_custom_agent.py`.

Your agent must inherit from `core.round_table.base_agent.VotingAgent`, implement the `vote` method, and be decorated with `@register_agent`.

The example below is a **Volume Spike Agent** — it uses `ohlc["volume"]` to detect abnormal trading activity as a risk proxy. This pattern is identical to how the built-in `VIXAwareRiskAgent` and `PatternRecognitionAgent` work in production.

```python
from typing import TYPE_CHECKING
import logging
import math

from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

logger = logging.getLogger(__name__)

# Reference volume for normalization (typical S&P 500 daily volume)
_NORMAL_VOLUME_REF = 1_000_000.0


@register_agent("VolumeSpikeAgent")
class VolumeSpikeAgent(VotingAgent):
    """
    Detects abnormal trading volume as a risk signal.

    High volume (> 5x normal)  -> bearish (score < 0.4): market is stressed.
    Low volume  (< 0.2x normal) -> slightly bullish (score > 0.6): calm market.
    Normal volume               -> neutral (score ~= 0.5).

    Reads only state["ohlc"]["volume"] — a single scalar.
    No DataFrames, no external API calls, no blocking I/O.
    """

    default_weight: float = 10.0
    min_weight: float = 0.0
    max_weight: float = 20.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        """
        Evaluates a symbol based on volume relative to a normal baseline.

        Args:
            state: SymbolEvalState — read state["symbol"] and state["ohlc"] only.

        Returns:
            VoteResult with score in [0.0, 1.0] and an audit-ready reasoning string.
        """
        symbol = state["symbol"]
        ohlc = state["ohlc"]

        volume = ohlc.get("volume", _NORMAL_VOLUME_REF)
        volume_ratio = volume / max(_NORMAL_VOLUME_REF, 1.0)

        if volume_ratio > 5.0:
            score = self._clamp(0.5 - math.log10(volume_ratio) * 0.2)
            label = "HIGH_STRESS"
        elif volume_ratio < 0.2:
            score = 0.6
            label = "CALM"
        else:
            score = 0.5
            label = "NORMAL"

        reasoning = (
            f"VolumeSpikeAgent: {symbol} vol={volume:.0f} "
            f"ratio={volume_ratio:.2f}x -> {label} -> score={score:.3f}"
        )

        return VoteResult(
            agent_name=self.__class__.__name__,
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
        )
```

### Key rules for every plugin agent

| Rule | Why |
|---|---|
| Read only `state["ohlc"]` scalars and `state["symbol"]` | The state contains no DataFrames — other fields return `None` |
| Always return a `VoteResult` — never raise | Exceptions exclude your agent from the current cycle silently |
| Keep `vote()` fully `async` — no blocking calls | Blocking code stalls the `asyncio.gather()` of all 9+ agents |
| Use `self._clamp(score)` to keep score in `[0.0, 1.0]` | The ConsensusEngine expects scores within this range |
| Set `weight=0.0` if your agent has no valid signal | Excludes your vote from the weighted average — better than biasing with 0.5 |
| Write a meaningful `reasoning` string | Required for MiFID II / EU AI Act Art. 13 audit trail |

### Agents with blocking ML inference or async external APIs

**For blocking synchronous code** (e.g. a PyTorch `forward()` pass), use `AsyncAIAgent`. It automatically runs `_run_inference()` in a `ThreadPoolExecutor`:

```python
from core.round_table.base_agent import AsyncAIAgent, VoteResult

@register_agent("MyTorchAgent")
class MyTorchAgent(AsyncAIAgent):
    default_weight: float = 15.0

    def _run_inference(self, state: "SymbolEvalState") -> VoteResult:
        # Runs in a separate thread — blocking torch.forward() is safe here
        symbol = state["symbol"]
        ohlc = state["ohlc"]
        # ... your model inference ...
        return VoteResult(
            agent_name=self.__class__.__name__,
            symbol=symbol,
            score=0.5,
            weight=self.weight,
            reasoning="MyTorchAgent: placeholder",
        )
```

**For async external API calls** (e.g. a REST endpoint), use `VotingAgent` and `await` directly inside `vote()` — exactly as `NewsSentimentAgent` does with Gemini.

## 5. Enabling Untrusted Plugins

Plugin loading is disabled by default. Enable it in your `.env.oss` file:

```env
ALLOW_UNTRUSTED_PLUGINS=true
ROUND_TABLE_PLUGINS_DIR=/app/app/plugins/round_table
```

> [!CAUTION]
> `ALLOW_UNTRUSTED_PLUGINS=true` enables dynamic code loading from `./plugins/round_table/`. Every `.py` file there is executed as the host user at engine boot — this is effectively Arbitrary Code Execution. Only enable this if you have written or fully reviewed every plugin file. Default is `false`.

## 6. Running the Engine

```bash
docker compose -f docker-compose.oss.yml up -d
docker compose -f docker-compose.oss.yml logs -f backend
```

A correctly registered agent appears in the Round Table cycle log:

```
VolumeSpikeAgent: AAPL vol=2450000 ratio=2.45x -> NORMAL -> score=0.500
INFO round_table.runner - Round Table cycle: 10 agents, consensus=0.672 -> BUY
```

## 7. Troubleshooting

If your agent count stays at 9 (the built-in count) after adding your plugin:

| Check | How |
|---|---|
| Plugin loading enabled? | `ALLOW_UNTRUSTED_PLUGINS=true` in `.env.oss` |
| Decorator present? | `@register_agent("YourAgentName")` on the class |
| Method signature correct? | `async def vote(self, state: "SymbolEvalState") -> VoteResult:` |
| File in correct directory? | `plugins/round_table/your_agent.py` |
| Import error hiding the agent? | Check `docker compose logs backend` for `ImportError` at boot |

If your agent appears in the count but always votes `0.5`, you are reading a state field that does not exist. Ensure you use only `state["ohlc"]` and `state["symbol"]`.
