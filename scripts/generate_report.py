#!/usr/bin/env python
# scripts/generate_report.py
# RPT-6 (Epic #1998) — the test/release entrypoint for the auditable v2 report.
"""Generate one auditable v2 research report from the command line.

This is the release-time smoke entrypoint for the RPT-6 pipeline. It lets an
operator produce (and eyeball / audit) a single report **without globally
enabling the specialist registry** — it calls the report pipeline directly, so
``REPORT_GENERATOR_V2_ENABLED`` gating the *specialist decision path* stays
irrelevant here. The flag is still set in-process (below) so any downstream
getattr sees the intended posture, matching how a real run would look.

The pipeline goes to the REAL engine sources via
:class:`core.report.engine_reader.EngineFactSetReader` (bars from the canonical
data provider, the PIT fundamentals cache, the model card) and reaches the LLM
ONLY through RPT-5's ``get_llm_provider()`` seam. Any missing feed degrades to an
honest gap — a report is always produced, never a fabricated number. The
mechanical RPT-4 audit summary is printed to stderr so the release test can
assert integrity.

Report-only and DORMANT: this touches no trading path;
``SPECIALIST_ALPHA_WEIGHT`` stays 0.

Usage
-----
    # print an NVDA report as of 2026-05-01 to stdout (audit summary -> stderr)
    python scripts/generate_report.py --symbol NVDA --as-of 2026-05-01

    # write it to a file instead
    python scripts/generate_report.py --symbol NVDA --as-of 2026-05-01 --out nvda.md
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

# Ensure the repo root (ai_trading_bot/) is importable when run as a script.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Set the posture flag locally BEFORE importing config so a fresh process reflects
# the intended (enabled) mode. The entrypoint calls the pipeline directly, so this
# does not, by itself, activate the specialist registry or any decision path.
os.environ.setdefault("REPORT_GENERATOR_V2_ENABLED", "true")

logger = logging.getLogger("generate_report")


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate one auditable v2 research report (RPT-6, Epic #1998)."
    )
    parser.add_argument("--symbol", required=True, help="Ticker, e.g. NVDA.")
    parser.add_argument(
        "--as-of",
        required=True,
        help="Point-in-time cutoff, ISO date (YYYY-MM-DD). No data after this "
        "calendar day feeds the report.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write the report to this path instead of stdout.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)

    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        logger.error("invalid --as-of %r (expected YYYY-MM-DD)", args.as_of)
        return 2

    symbol = args.symbol.strip().upper()

    # Imported here (after the sys.path / env setup above) so a bare `python
    # scripts/generate_report.py` works from the repo root.
    from core.report.pipeline import build_report_bundle

    logger.info("generating v2 report for %s as of %s ...", symbol, as_of.isoformat())
    # reader=None -> the real EngineFactSetReader; llm=None -> the get_llm_provider
    # seam (Desktop Ollama / Enterprise Gemini). Missing feeds degrade to honest gaps.
    bundle = build_report_bundle(symbol, as_of, reader=None, llm=None)

    # The audit verdict goes to stderr so stdout stays a clean Markdown report.
    print(
        f"AUDIT[{symbol} @ {as_of.isoformat()}]: {bundle.audit.summary()}",
        file=sys.stderr,
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(bundle.markdown)
        logger.info("wrote %d chars to %s", len(bundle.markdown), args.out)
    else:
        sys.stdout.write(bundle.markdown)

    # Non-zero exit if the mechanical audit found a fabricated claim — useful as a
    # release gate. (An honest-gap report with abstained prose audits clean.)
    return 0 if bundle.audit.integrity_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
