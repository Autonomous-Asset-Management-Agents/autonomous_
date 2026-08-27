"""
report.py — RTR-0 (#1947) gate artifact assembly (truth table + Markdown/JSON).

The output of this module IS the activation gate: every RTR-1..4 PR must cite
its numbers before/after. Assembly rules (plan Gherkin §7):

  * every ``ALL_AGENTS`` member appears exactly once — measured, N/A
    (abstained on every replayed bar) or EXCLUDED (not offline-attributable)
    — nothing is silently dropped;
  * an agent with zero opinions is NEVER classified off zero information —
    it is EXCLUDED with a reason, and its activation needs separate evidence;
  * IC significance is per-date cross-sectional + HAC/Newey-West (M1) —
    "significant" is only claimed AFTER Benjamini-Hochberg FDR correction of
    the HAC p-values across all measured agents (plan §12.5);
  * the run's flag POSTURE (DrawdownGuard conditioner et al.) is embedded in
    ``run_meta`` — the artifact is only reproducible under the same posture
    (M2 reproducibility contract);
  * a negative result is a documented result (prove-it-or-kill-it), and a
    significant NEGATIVE IC can never auto-'keep' (M3: invert-candidate).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.analysis.attribution.metrics import (
    DELTA_SHARPE_NOISE_BAND,
    MIN_CROSS_SECTION,
    LOOResult,
    benjamini_hochberg,
    classify_agent,
    daily_ic_significance,
    fold_ics,
    gate_verdict,
    hit_rate,
    loo_consensus_sharpe,
    opinion_mask,
    spearman_ic,
    vote_correlation_matrix,
)
from core.round_table.agents import ALL_AGENTS

_ALL_AGENT_NAMES: List[str] = [type(a).__name__ for a in ALL_AGENTS]

# M2 — reproducibility contract: a gate artifact without these fields is
# not citable by an RTR-1..4 PR (posture is injected by build_run_meta).
REQUIRED_RUN_META_FIELDS = frozenset(
    {
        "issue",
        "generated_utc",
        "git_sha",
        "source",
        "data_dir",
        "symbols",
        "coverage",
        "start",
        "end",
        "n_bars_replayed",
        "cost_bps",
        "ic_horizon_bars",
        "hac_lag_days",
        "fdr_alpha",
        "seed",
        "vix_series",
        "target",
    }
)


def runtime_posture() -> Dict[str, object]:
    """Snapshot of every config flag that shapes the replayed votes (M2).

    The DrawdownGuard's veto threshold is FLAG-DEPENDENT (agents.py:64-65):
    conditioner ON -> hard veto only above DRAWDOWN_SEVERE_VETO_THRESHOLD,
    OFF -> 0.07 (30d path) / 0.05 (1-bar fallback). A benchmark run is only
    reproducible under the same posture, so it MUST live in ``run_meta``.
    """
    from config import get_config

    cfg = get_config()
    conditioner_on = bool(getattr(cfg, "DRAWDOWN_GUARD_CONDITIONER_ENABLED", False))
    severe = float(getattr(cfg, "DRAWDOWN_SEVERE_VETO_THRESHOLD", 0.25))
    return {
        "DRAWDOWN_GUARD_CONDITIONER_ENABLED": conditioner_on,
        "DRAWDOWN_SEVERE_VETO_THRESHOLD": severe,
        "DRAWDOWN_CONVICTION_DAMP_MAX": float(
            getattr(cfg, "DRAWDOWN_CONVICTION_DAMP_MAX", 0.8)
        ),
        # ADR-comment agents.py:64-65 — the thresholds the replayed
        # DrawdownGuard votes were actually produced under:
        "effective_drawdown_veto_threshold_30d": severe if conditioner_on else 0.07,
        "effective_drawdown_veto_threshold_1bar": severe if conditioner_on else 0.05,
        "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK": bool(
            getattr(cfg, "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK", False)
        ),
        "SPECIALIST_ALPHA_WEIGHT": float(
            getattr(cfg, "SPECIALIST_ALPHA_WEIGHT", 0.0) or 0.0
        ),
        # N1 (#1947 re-review): the Momentum vote is flag-dependent on thin
        # windows (agents.py:387 — insufficient 12-1M history abstains under
        # the flag instead of falling back) — a run under ON produces
        # different Momentum rows and must say so in its posture.
        "MOMENTUM_FALLBACK_ABSTAIN_ENABLED": bool(
            getattr(cfg, "MOMENTUM_FALLBACK_ABSTAIN_ENABLED", False)
        ),
        # M2 limitation, machine-readable: the replicated LOO book models the
        # hard-veto path (runner._apply_agent_veto) only; the conditioner's
        # BUY-conviction damping (runner._score_to_signal) is NOT replicated.
        "loo_book_models": (
            "hard-veto path only (runner._apply_agent_veto); conditioner "
            "BUY-conviction damping (runner._score_to_signal) NOT replicated "
            "— with the conditioner ON the DrawdownGuard's production effect "
            "is understated in the LOO (documented limitation, RUNBOOK §6)"
        ),
    }


def build_run_meta(**fields: object) -> Dict[str, object]:
    """Assemble ``run_meta`` and enforce the M2 reproducibility contract.

    Raises ``ValueError`` naming any missing required field; injects the
    ``posture`` snapshot so no run can be published without it.
    """
    missing = sorted(REQUIRED_RUN_META_FIELDS - set(fields))
    if missing:
        raise ValueError(
            f"run_meta incomplete — missing required fields: {', '.join(missing)}"
        )
    meta: Dict[str, object] = dict(fields)
    meta["posture"] = runtime_posture()
    # N2 (#1947 re-review): the constants that shape the verdicts and the
    # daily-IC series were only pinned via git_sha — inject them so every
    # artifact states the values it was judged under (single source of
    # truth: metrics.py, never literals here).
    meta["constants"] = {
        "DELTA_SHARPE_NOISE_BAND": DELTA_SHARPE_NOISE_BAND,
        "MIN_CROSS_SECTION": MIN_CROSS_SECTION,
    }
    # #2409: every artifact states whether the LSTM row was model-backed —
    # a caller that says nothing gets an honest enabled=False, never an
    # absent field (the CLI sets the real bundle info). Rev 3 (PR #2421):
    # the key is ``lstm`` — the reproducibility contract the owner run
    # checks (enabled, artifacts_dir, sequence_length, input_dim,
    # n_features).
    meta.setdefault("lstm", {"enabled": False})
    return meta


@dataclass
class AgentRow:
    """One truth-table line for one agent of the canonical roster."""

    agent: str
    n_votes: int = 0
    n_opinions: int = 0
    ic: Optional[float] = None  # mean per-date cross-sectional Spearman IC (M1)
    t_stat: Optional[float] = None  # HAC/Newey-West t over the daily IC series
    p_value: Optional[float] = None  # HAC p — the FDR input
    n_days: int = 0  # number of daily ICs (the honest effective N)
    hac_lag: int = 0
    ic_method: str = ""  # per-date-cs-hac | pooled-iid-fallback
    ic_pooled: Optional[float] = None  # descriptive only, never significance
    fdr_significant: bool = False
    shuffled_ic: Optional[float] = None
    fold_ic_values: List[float] = field(default_factory=list)
    hit_rate: Optional[float] = None
    n_signals: int = 0
    loo_delta_sharpe: Optional[float] = None
    sharpe_without: Optional[float] = None
    classification: str = "EXCLUDED"
    verdict: str = ""
    note: str = ""


def build_agent_rows(
    votes_df: pd.DataFrame,
    *,
    fwd_ic: pd.DataFrame,
    fwd_1d: pd.DataFrame,
    exclusions: Dict[str, str],
    cost_bps: float = 10.0,
    fdr_alpha: float = 0.10,
    seed: int = 0,
    ic_horizon: int = 5,
) -> Tuple[List[AgentRow], pd.DataFrame]:
    """Assemble the per-agent truth table + the vote correlation matrix.

    ``fwd_ic``     — the IC/hit-rate target (default 5d forward MtM return).
    ``fwd_1d``     — the 1-day forward return driving the daily-rebalanced
                     LOO consensus book.
    ``ic_horizon`` — label horizon in TRADING DAYS; drives the HAC lag of the
                     daily-IC t-test (M1) and the purge/embargo of the
                     walk-forward folds (M4, day units).
    Returns ``(rows, correlation_matrix)`` with rows in ALL_AGENTS order.
    """
    loo: Dict[str, LOOResult] = {}
    replayed = set(votes_df["agent"].unique()) if not votes_df.empty else set()
    if replayed:
        loo = loo_consensus_sharpe(votes_df, fwd_1d, cost_bps=cost_bps)

    rng = np.random.default_rng(seed)
    rows: List[AgentRow] = []
    measured_idx: List[int] = []
    for name in _ALL_AGENT_NAMES:
        row = AgentRow(agent=name)
        if name in exclusions:
            row.note = exclusions[name]
            row.classification = "EXCLUDED"
        elif name not in replayed:
            row.note = "not present in the replay panel"
            row.classification = "EXCLUDED"
        else:
            agent_votes = votes_df[votes_df["agent"] == name]
            row.n_votes = int(len(agent_votes))
            opinions = agent_votes[opinion_mask(agent_votes)]
            row.n_opinions = int(len(opinions))
            if row.n_opinions == 0:
                row.note = (
                    "abstained on 100% of replayed bars (no offline data "
                    "source for this agent in this run) — not attributable"
                )
                row.classification = "EXCLUDED"
            else:
                merged = opinions.merge(
                    fwd_ic, on=["ts", "symbol"], how="inner"
                ).sort_values(["ts", "symbol"])
                # M1 — per-date cross-sectional IC series + HAC/Newey-West t
                # (lag = label horizon). Never the pooled-iid t of the panel.
                ic_res = daily_ic_significance(merged, lag=ic_horizon)
                row.ic = ic_res.ic_mean
                row.t_stat = ic_res.t_stat
                row.p_value = ic_res.p_value
                row.n_days = ic_res.n_days
                row.hac_lag = ic_res.hac_lag
                row.ic_method = ic_res.method
                pooled = spearman_ic(
                    merged["score"].to_numpy(), merged["fwd_ret"].to_numpy()
                )
                row.ic_pooled = pooled.ic  # descriptive only
                shuffled = daily_ic_significance(
                    merged.assign(
                        fwd_ret=rng.permutation(merged["fwd_ret"].to_numpy())
                    ),
                    lag=ic_horizon,
                )
                row.shuffled_ic = shuffled.ic_mean
                # M4 — folds over unique TRADING DAYS (purge/embargo in days)
                row.fold_ic_values = fold_ics(
                    merged,
                    n_splits=4,
                    label_horizon=ic_horizon,
                    embargo=ic_horizon,
                )
                hr = hit_rate(merged["score"].to_numpy(), merged["fwd_ret"].to_numpy())
                row.hit_rate = None if np.isnan(hr.rate) else hr.rate
                row.n_signals = hr.n_signals
                if name in loo:
                    row.loo_delta_sharpe = loo[name].delta_sharpe
                    row.sharpe_without = loo[name].sharpe_without
                if float(opinions["weight"].max()) == 0.0:
                    row.note = (
                        "dormant (default_weight 0.0) — votes real scores but "
                        "cannot move the consensus; IC measures standalone "
                        "signal quality only (validate-before-activate)"
                    )
                if ic_res.method == "pooled-iid-fallback":
                    fallback_note = (
                        "IC via pooled-iid fallback (no usable cross-section "
                        "— significance is weaker than the per-date HAC path)"
                    )
                    row.note = (
                        f"{row.note}; {fallback_note}" if row.note else (fallback_note)
                    )
                measured_idx.append(len(rows))
        rows.append(row)

    # FDR across ALL measured agents (never per-agent in isolation),
    # on the HAC-corrected p-values (M1)
    if measured_idx:
        pvals = [rows[i].p_value for i in measured_idx]
        significant = benjamini_hochberg(pvals, alpha=fdr_alpha)
        for i, sig in zip(measured_idx, significant):
            rows[i].fdr_significant = bool(sig)
    for i in measured_idx:
        rows[i].classification = classify_agent(
            rows[i].loo_delta_sharpe,
            rows[i].fdr_significant,
            ic=rows[i].ic,  # M3 — sign-aware: significant negative IC != keep
        )
    for row in rows:
        row.verdict = gate_verdict(row.classification)

    corr = vote_correlation_matrix(votes_df) if not votes_df.empty else pd.DataFrame()
    return rows, corr


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(value: Optional[float], spec: str = "+.4f") -> str:
    return "—" if value is None else format(value, spec)


def render_markdown(
    rows: List[AgentRow],
    *,
    correlation: pd.DataFrame,
    run_meta: Dict[str, object],
) -> str:
    """Render the versioned gate artifact as Markdown."""
    lines: List[str] = []
    lines.append("# RTR-0 · Per-Agent Attribution Benchmark — Gate-Artefakt")
    lines.append("")
    lines.append(
        "> Prove-it-or-kill-it (#1947): jede RTR-1..4-Aktivierung muss ihre "
        "Zahl vorher/nachher gegen dieses Artefakt im PR zeigen. Ein Nein "
        "ist ein dokumentiertes Ergebnis."
    )
    lines.append("")
    lines.append("## Run-Parameter")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(run_meta, indent=2, sort_keys=True, default=str))
    lines.append("```")
    lines.append("")
    lines.append("## Wahrheitstabelle (je Agent)")
    lines.append("")
    lines.append(
        "> IC = Mittel der **per-Datum-Cross-Section-Spearman-ICs**; t/p via "
        "**HAC/Newey-West** über die Tages-IC-Serie (Lag = Label-Horizont) — "
        "nie die gepoolte iid-t des Panels (M1). N days = Anzahl Tages-ICs "
        "(das ehrliche effektive N)."
    )
    lines.append("")
    lines.append(
        "| Agent | N votes | N opinions | N days | IC (Ø daily CS) | t (NW) | "
        "p (NW) | FDR-sig | IC shuffled | Fold-ICs | Hit-Rate | N signals | "
        "LOO ΔSharpe | Klasse | Gate-Verdict | Anmerkung |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        folds = (
            ", ".join(f"{v:+.3f}" for v in r.fold_ic_values)
            if r.fold_ic_values
            else "—"
        )
        lines.append(
            f"| {r.agent} | {r.n_votes} | {r.n_opinions} | {r.n_days} | "
            f"{_fmt(r.ic)} | "
            f"{_fmt(r.t_stat, '+.2f')} | {_fmt(r.p_value, '.4f')} | "
            f"{'✔' if r.fdr_significant else '✘'} | {_fmt(r.shuffled_ic)} | "
            f"{folds} | {_fmt(r.hit_rate, '.3f')} | {r.n_signals} | "
            f"{_fmt(r.loo_delta_sharpe, '+.3f')} | {r.classification} | "
            f"{r.verdict} | {r.note} |"
        )
    lines.append("")
    if not correlation.empty:
        lines.append("## Vote-Korrelationsmatrix (Spearman, nur echte Opinions)")
        lines.append("")
        agents = list(correlation.columns)
        lines.append("| |" + "|".join(agents) + "|")
        lines.append("|---|" + "---|" * len(agents))
        for a in agents:
            cells = []
            for b in agents:
                v = correlation.loc[a, b]
                cells.append("—" if pd.isna(v) else f"{v:+.3f}")
            lines.append(f"| {a} |" + "|".join(cells) + "|")
        lines.append("")
    lines.append("## Legende")
    lines.append("")
    lines.append(
        "- **HEBT** → keep: LOO ΔSharpe > +0.05 UND IC FDR-signifikant "
        "mit POSITIVEM Vorzeichen.  \n"
        "- **INVERTED-IC** → invert-candidate: LOO ΔSharpe > +0.05, IC "
        "FDR-signifikant, aber NEGATIV — Vorzeichen-Konflikt, nie "
        "auto-keep (M3); expliziter Gate-Review nötig.  \n"
        "- **SCHADET** → kill: LOO ΔSharpe < −0.05.  \n"
        "- **RAUSCHEN** → fix: messbar, aber kein belastbarer Beitrag.  \n"
        "- **EXCLUDED** → n/a: offline nicht attribuierbar — Aktivierung "
        "braucht eine separate Evidenzquelle."
    )
    lines.append("")
    lines.append("## Limitationen & Posture (M2)")
    lines.append("")
    lines.append(
        "- **Posture-Bindung:** Dieses Artefakt gilt NUR für die in "
        "`run_meta.posture` festgehaltene Flag-Posture (insb. "
        "`DRAWDOWN_GUARD_CONDITIONER_ENABLED` + Schwellen, "
        "agents.py:64-65) — dieselbe CLI unter anderer Posture erzeugt "
        "andere Vetoes und eine andere DrawdownGuard-Zeile.  \n"
        "- **Hard-Veto-Annahme (Limitation):** Das replizierte LOO-Buch "
        "modelliert den Hard-Veto-Pfad (`runner._apply_agent_veto`); die "
        "Conditioner-Dämpfung der BUY-Conviction (`runner._score_to_signal` "
        "→ Positionsgröße, auf aktuellem main zusätzlich BUY→HOLD-Gate "
        "unterhalb der Buy-Schwelle) ist NICHT repliziert. Bei Conditioner "
        "OFF (ADR-DDG-02-Default) ist der Hard-Veto-Pfad der "
        "Produktionspfad — keine Annahme; bei Conditioner ON wird "
        "DrawdownGuards reale Produktionswirkung im LOO unterschätzt.  \n"
        "- **IC-Fallback:** Zeilen mit `pooled-iid-fallback` (kein "
        "nutzbarer Cross-Section) tragen eine schwächere Signifikanz als "
        "der per-Datum-HAC-Pfad und sind in der Anmerkung markiert."
    )
    lines.append("")
    return "\n".join(lines)


def rows_to_payload(
    rows: List[AgentRow],
    *,
    correlation: pd.DataFrame,
    run_meta: Dict[str, object],
) -> Dict[str, object]:
    """Machine-readable twin of the Markdown artifact."""
    return {
        "run_meta": run_meta,
        "agents": [
            {
                "agent": r.agent,
                "n_votes": r.n_votes,
                "n_opinions": r.n_opinions,
                "ic": r.ic,
                "t_stat": r.t_stat,
                "p_value": r.p_value,
                "n_days": r.n_days,
                "hac_lag": r.hac_lag,
                "ic_method": r.ic_method,
                "ic_pooled": r.ic_pooled,
                "fdr_significant": r.fdr_significant,
                "shuffled_ic": r.shuffled_ic,
                "fold_ics": r.fold_ic_values,
                "hit_rate": r.hit_rate,
                "n_signals": r.n_signals,
                "loo_delta_sharpe": r.loo_delta_sharpe,
                "sharpe_without": r.sharpe_without,
                "classification": r.classification,
                "verdict": r.verdict,
                "note": r.note,
            }
            for r in rows
        ],
        "correlation": ({} if correlation.empty else json.loads(correlation.to_json())),
    }


def write_gate_artifact(
    out_dir: str | Path,
    *,
    markdown: str,
    payload: Dict[str, object],
    basename: str = "RESULTS",
) -> Tuple[Path, Path]:
    """Write the versioned artifact pair (``.md`` + ``.json``)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"{basename}.md"
    json_path = out / f"{basename}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return md_path, json_path
