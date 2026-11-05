# core/telemetry_flush.py
# INF-13 P2 (#1456) — the DORMANT flush daemon for the desktop telemetry store.
#
# P1 (#1373) captures crash/stability spans to a local, PII-scrubbed JSONL store
# with ZERO egress. This module is the (still dormant) bridge toward automatic
# egress: it reads that store and hands batches to a transport — but ONLY when
# egress is allowed. Egress is gated, default-OFF, by TWO independent switches:
#   * opt-in consent           (TELEMETRY_CRASH_CONSENT — §25 TDDDG, #1368 Gate ④)
#   * the egress master switch  (TELEMETRY_EGRESS_ENABLED — client side of the
#                                activation gate; the always-on backend is itself
#                                Terraform-gated, #1457, ~20-user threshold)
# AND a wired transport (the real OTLP exporter is P3 — until then `sender` is
# None and this is a no-op). With the gate off it performs ZERO network activity.
# Never raises — telemetry must never break the app.
from __future__ import annotations

import json
import os

_DEFAULT_DAYS = 7.0


def _flag(name: str) -> bool:
    """Read a telemetry flag across both editions: OSS ``get_config()``
    (SimpleNamespace of globals) first, then the cloud ``config.py`` module
    attribute. Defaults to ``False``. Never raises."""
    try:
        import config

        cfg = config.get_config()
        val = getattr(cfg, name, None)
        if val is None:
            val = getattr(config, name, False)
        return bool(val)
    except Exception:
        return False


def egress_allowed() -> bool:
    """Egress requires BOTH opt-in consent and the egress master switch — both
    default OFF (#1368 Gate ④ + activation gate #1457). No default-on path."""
    return _flag("TELEMETRY_EGRESS_ENABLED") and _flag("TELEMETRY_CRASH_CONSENT")


class TelemetryFlusher:
    """Reads the local telemetry store and hands batches to ``sender`` — but only
    when ``gate()`` allows egress. Offline-first: on a failed/absent send the
    records are retained for a later attempt. Dormant by default (gate off and/or
    no sender). Never raises."""

    def __init__(self, store_dir: str, sender=None, gate=None):
        # sender: callable(list[dict]) -> bool  (True = delivered; None = no transport)
        # gate:   callable() -> bool            (defaults to egress_allowed)
        self._dir = store_dir
        self._path = os.path.join(store_dir, "telemetry.jsonl")
        self._sender = sender
        self._gate = gate or egress_allowed

    def _snapshot_lines(self) -> list:
        with open(self._path, "r", encoding="utf-8") as fh:
            return [ln.rstrip("\n") for ln in fh if ln.strip()]

    def _drop_first(self, count: int) -> None:
        """Remove the first ``count`` lines (the batch we just handled), keeping
        anything appended since the snapshot. Atomic; never raises."""
        try:
            remaining = self._snapshot_lines()[count:]
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                if remaining:
                    fh.write("\n".join(remaining) + "\n")
            os.replace(tmp, self._path)
        except Exception:
            pass

    def flush(self) -> int:
        """Attempt one flush. Returns the number of records delivered (0 when
        dormant, offline, or empty). Performs ZERO egress when the gate is off."""
        try:
            if not self._gate():
                return 0  # dormant: consent/egress switch off -> no egress
            if self._sender is None:
                return 0  # no transport wired yet (P3)
            if not os.path.exists(self._path):
                return 0
            lines = self._snapshot_lines()
            if not lines:
                return 0
            records = []
            for ln in lines:
                try:
                    records.append(json.loads(ln))
                except Exception:
                    pass  # drop unparseable
            if not records:
                self._drop_first(len(lines))  # purge garbage
                return 0
            if not self._sender(records):
                return 0  # offline / failed -> keep for retry
            self._drop_first(len(lines))
            return len(records)
        except Exception:
            return 0
