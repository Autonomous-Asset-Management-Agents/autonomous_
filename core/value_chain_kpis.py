import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class StageAlert:
    def __init__(self, stage: str, message: str, is_missing: bool = False):
        self.stage = stage
        self.message = message
        self.is_missing = is_missing


class ValueChainMonitor:
    """
    ARC-E5.6: Value Chain KPIs and Alarms.
    Maps existing metrics to VC stages and issues alerts if stages fail to deliver.
    """

    # Define mapping of VC stages to their expected metrics
    STAGE_METRICS = {
        "VC-1": "data_coverage",
        "VC-2": "decisions_count",
        "VC-4": "blocked_orders",
        "VC-3": "filled_orders",
        "VC-5": "pnl_pct",
        "VC-6": "audit_records",
    }

    # Thresholds are non-binding (Owner decision pending)
    THRESHOLDS = {
        "VC-1": 0.0,
        "VC-2": 0.0,
        "VC-4": -1.0,  # Just as placeholder
        "VC-3": -1.0,
        "VC-5": -100.0,
        "VC-6": 0.0,
    }

    def __init__(self):
        self._history: List[Dict[str, Any]] = []

    def evaluate(
        self, metrics: Dict[str, Any]
    ) -> tuple[Dict[str, Any], List[StageAlert]]:
        """
        Evaluate metrics against stages, return annotated metrics and non-binding alerts.
        """
        annotated = {}
        alerts = []

        try:
            for stage, metric_key in self.STAGE_METRICS.items():
                val = metrics.get(metric_key)
                if val is None:
                    # Wert gar nicht gebildet
                    alerts.append(
                        StageAlert(
                            stage, f"Wert nicht gebildet: {metric_key}", is_missing=True
                        )
                    )
                    annotated[f"{stage}_{metric_key}"] = None
                else:
                    annotated[f"{stage}_{metric_key}"] = val
                    # Check if missing over time (0 over multiple cycles)
                    # For simplicity in this non-binding phase, we just flag 0 as 'unterschritten' or missing
                    # if it's supposed to be > 0. But wait, "liefert kein Ergebnis mehr" vs "nicht gebildet".
                    if val == 0 and self.THRESHOLDS[stage] > 0:
                        alerts.append(
                            StageAlert(
                                stage,
                                f"Wert unterschritten: {metric_key}={val}",
                                is_missing=False,
                            )
                        )

            self._history.append(metrics)
            # P1 Fix: Prevent unbounded memory leak (keep only the last 3 cycles needed for alerting)
            if len(self._history) > 3:
                self._history.pop(0)

            # Check history for "liefert ueber mehrere Zyklen kein Ergebnis"
            if len(self._history) >= 3:
                recent = self._history[-3:]
                for stage, metric_key in self.STAGE_METRICS.items():
                    vals = [m.get(metric_key) for m in recent]
                    # If it's literally 0 for 3 cycles, maybe it's dead
                    if all(v == 0 for v in vals):
                        alerts.append(
                            StageAlert(
                                stage,
                                f"Stufe liefert ueber 3 Zyklen 0: {metric_key}",
                                is_missing=False,
                            )
                        )

        except Exception as exc:
            # "Die Messung kann den Handel nicht anhalten" -> swallow and log
            logger.warning(f"ValueChainMonitor error: {exc}", exc_info=True)

        return annotated, alerts
