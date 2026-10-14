import logging
from typing import Any, Dict


class ABTestExperiment:
    """
    A framework to compare two independent simulation runs (Model A vs. Model B)
    over the exact same historical data, comparing their total returns and max drawdowns.
    """

    def __init__(
        self, model_a_results: Dict[str, Any], model_b_results: Dict[str, Any]
    ):
        self.model_a = model_a_results
        self.model_b = model_b_results

    def _calculate_max_drawdown(self, daily_equity: list) -> float:
        if not daily_equity:
            return 0.0

        equities = [day["equity"] for day in daily_equity]
        max_drawdown_pct = 0.0
        peak = equities[0]

        for eq in equities:
            if eq > peak:
                peak = eq

            dd_pct = (eq - peak) / peak * 100
            if dd_pct < max_drawdown_pct:
                max_drawdown_pct = dd_pct

        return max_drawdown_pct

    def generate_report(self) -> Dict[str, Any]:
        """
        Calculates comparative statistics and determines the winning model.
        """
        ret_a = self.model_a.get("total_return", 0.0)
        ret_b = self.model_b.get("total_return", 0.0)

        name_a = self.model_a.get("model_name", "Model A")
        name_b = self.model_b.get("model_name", "Model B")

        if ret_a > ret_b:
            winner = name_a
        elif ret_b > ret_a:
            winner = name_b
        else:
            winner = "Tie"

        return {
            "model_a": {
                "name": name_a,
                "total_return": ret_a,
                "max_drawdown_pct": self._calculate_max_drawdown(
                    self.model_a.get("daily_equity", [])
                ),
                "trades_count": len(self.model_a.get("trades", [])),
            },
            "model_b": {
                "name": name_b,
                "total_return": ret_b,
                "max_drawdown_pct": self._calculate_max_drawdown(
                    self.model_b.get("daily_equity", [])
                ),
                "trades_count": len(self.model_b.get("trades", [])),
            },
            "winner": winner,
            "metrics_diff": {"total_return_diff": abs(ret_a - ret_b)},
        }

    def print_summary(self):
        """
        Outputs a human-readable console summary of the A/B test.
        """
        report = self.generate_report()
        logging.info("=== A/B Test Results ===")
        logging.info(
            f"Model A ({report['model_a']['name']}): Return {report['model_a']['total_return']:.2f}%, Max DD {report['model_a']['max_drawdown_pct']:.2f}%, Trades {report['model_a']['trades_count']}"
        )
        logging.info(
            f"Model B ({report['model_b']['name']}): Return {report['model_b']['total_return']:.2f}%, Max DD {report['model_b']['max_drawdown_pct']:.2f}%, Trades {report['model_b']['trades_count']}"
        )
        logging.info(
            f"🏆 WINNER: {report['winner']} (Outperformed by {report['metrics_diff']['total_return_diff']:.2f}%)"
        )
        logging.info("========================")
