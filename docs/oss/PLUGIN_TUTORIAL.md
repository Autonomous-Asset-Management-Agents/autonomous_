# Creating a Custom Strategy Plugin

Welcome to the AAAgents OSS ecosystem! One of the core features of the "BORA" architecture is the `PluginRegistry`. This allows you to add custom AI/Algorithmic trading strategies without modifying the core `Round Table V2` engine.

## 1. The Architecture

The AAAgents engine uses a "Round Table" of AI Agents. Each agent votes on a given stock symbol between `0.0` (Strong Sell) and `1.0` (Strong Buy). A central `ConsensusEngine` aggregates these votes using weighted averages.

Your custom strategy will be instantiated as an Agent that participates in this Round Table.

## 2. Directory Structure

All custom plugins reside in the `plugins/round_table/` directory at the root of the Backend.

```text
AI Trading Bot/
├── core/
├── plugins/
│   └── round_table/         <-- Your code goes here
│       └── my_custom_agent.py   <-- Your strategy
├── main.py
```

## 3. Writing Your First Agent

Create `plugins/round_table/my_custom_agent.py`.
Your agent must inherit from `core.round_table.base_agent.VotingAgent`, implement the `vote` method, and be decorated with `@register_agent`.

```python
from typing import TYPE_CHECKING
import logging
from core.round_table.base_agent import VotingAgent, VoteResult
from core.round_table.registry import register_agent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

logger = logging.getLogger(__name__)

@register_agent("RSIReversionAgent")
class RSIReversionAgent(VotingAgent):
    """
    A simple Mean-Reversion agent using the RSI indicator.
    """
    # Class attributes define the agent's weight
    default_weight: float = 15.0
    min_weight: float = 0.0
    max_weight: float = 30.0
        
    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        """
        Calculates the buy/sell signal based on market context.
        Returns a VoteResult with a score between 0.0 (Sell) and 1.0 (Buy).
        """
        symbol = state["symbol"]
        logger.info(f"[{self.__class__.__name__}] Analyzing {symbol}...")
        
        # 1. Extract data from the state (provided by the engine)
        df = state.get("historical_data")
        
        # If no data is available, vote neutral
        if df is None or df.empty:
            return VoteResult(
                agent_name=self.__class__.__name__,
                symbol=symbol,
                score=0.5,
                weight=self.weight,
                reasoning="Neutral: No historical data available."
            )
            
        # We assume the engine has pre-calculated TA indicators
        if "RSI_14" not in df.columns:
            logger.warning(f"[{self.__class__.__name__}] RSI_14 missing for {symbol}")
            return VoteResult(
                agent_name=self.__class__.__name__,
                symbol=symbol,
                score=0.5,
                weight=self.weight,
                reasoning="Neutral: RSI_14 indicator missing."
            )
            
        latest_rsi = df["RSI_14"].iloc[-1]
        
        # 2. Strategy Logic
        if latest_rsi < 30:
            # Oversold -> Strong Buy signal
            return VoteResult(
                agent_name=self.__class__.__name__,
                symbol=symbol,
                score=0.9,
                weight=self.weight,
                reasoning=f"Strong Buy: RSI is {latest_rsi:.2f} (Oversold)."
            )
        elif latest_rsi > 70:
            # Overbought -> Strong Sell signal
            return VoteResult(
                agent_name=self.__class__.__name__,
                symbol=symbol,
                score=0.1,
                weight=self.weight,
                reasoning=f"Strong Sell: RSI is {latest_rsi:.2f} (Overbought)."
            )
        else:
            # Neutral territory
            return VoteResult(
                agent_name=self.__class__.__name__,
                symbol=symbol,
                score=0.5,
                weight=self.weight,
                reasoning=f"Neutral: RSI is {latest_rsi:.2f}."
            )
```

## 4. Enabling Untrusted Plugins

For security reasons, loading plugins is disabled by default. You need to enable it via environment variables.

In your `.env.oss` file, add:
```env
ALLOW_UNTRUSTED_PLUGINS=true
```

## 5. Running the Engine

When you start the BORA Container (or the local Python script), the engine will scan the `plugins/round_table/` folder and automatically load agents that use the `@register_agent` decorator.

```bash
docker compose -f docker-compose.oss.yml up -d
```

Check the logs to see your agent in action:
```bash
docker compose -f docker-compose.oss.yml logs -f backend
```

You should see logs like:
`[RSIReversionAgent] Analyzing AAPL...`

## Notes on the Context Object
The `state` dictionary passed to your `vote` method is a `SymbolEvalState` TypedDict. It contains:
- `historical_data`: A Pandas DataFrame with OHLCV data and technical indicators.
- `news_articles`: Recent headlines for the symbol.
- `macro_data`: Macroeconomic indicators (VIX, Fed rates).
- `portfolio_state`: Current holdings and cash available.

Explore the `core/round_table/` directory to see how the built-in agents utilize this state!
