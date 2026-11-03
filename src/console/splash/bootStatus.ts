// src/console/splash/bootStatus.ts — map raw engine boot-log lines to a small set of friendly,
// human status phases for the BootSplash. The splash used to print the RAW last log line under the
// wordmark (uvicorn/torch/registry output), which reads as cryptic system noise. This turns it into
// calm progress text. The raw log stays available (Settings → Engine-Log, and the error-state tail).
// Pure + no React, so it is unit-tested in isolation.

interface Phase {
  label: string;
  patterns: RegExp[];
}

// Ordered EARLY → LATE. bootStatusLabel returns the FURTHEST phase for which any log line matches,
// so a later generic line (e.g. a /health request) never drags the status backwards.
const PHASES: Phase[] = [
  {
    // First macOS launch: the shell downloads the engine runtime before anything else can start
    // (main.cjs logs "[provisioning] 43% …" lines). Earliest phase; a percent is appended when known.
    label: "Downloading the engine runtime (once, a few minutes)…",
    patterns: [/\[provisioning\]/i, /fetching the engine runtime/i, /runtime provisioned/i],
  },
  {
    label: "Starting engine…",
    patterns: [/started server process/i, /waiting for application startup/i, /application startup/i],
  },
  {
    label: "Loading AI models…",
    patterns: [/ollama/i, /gemini/i, /\blstm\b/i, /torch/i, /rl[_ ]?model/i, /model files/i, /startup check/i, /loading model/i, /warm(?:up|ing)/i],
  },
  {
    label: "Connecting to your broker…",
    patterns: [/alpaca/i, /\bbroker\b/i, /fetch account/i, /portfolio.?history/i, /get_account/i],
  },
  {
    label: "Warming up the strategy…",
    patterns: [/registry/i, /specialist/i, /current strategy/i, /\bagent\b/i],
  },
  {
    label: "Almost ready…",
    patterns: [/uvicorn running/i, /application startup complete/i, /strateg(?:y|ies) started/i, /monitor threads started/i, /engine ready/i, /live strategy started/i],
  },
];

const DEFAULT_LABEL = "Starting engine…";
const DOWNLOAD_LABEL = PHASES[0].label;

/**
 * First-launch runtime provisioning state (mac, B4), folded from the shell's log lines. main.cjs logs
 * every progress tick as "[provisioning] N% stage" (" — error" appended when that tick failed), the
 * announcement "First launch: fetching the engine runtime …", a release-lookup failure as
 * "Provisioning failed: …" and the success as "Runtime provisioned — restarting …". The shell also
 * sends "provision:progress" over IPC, but preload.cjs does not bridge it; the log stream is the one
 * channel the renderer has, and it is replayed on mount (getEngineLogs) so a late subscriber catches up.
 */
export interface ProvisionStatus {
  /** The shell is fetching the engine runtime right now: no boot timeout, no Retry. */
  active: boolean;
  percent: number | null;
  /** The shell's stage label ("Python-Laufzeit", "Prüfen", "Entpacken", "done", …). */
  stage: string | null;
  /** Provisioning ended with this error: the splash shows it and offers Retry. */
  error: string | null;
}

export const PROVISION_IDLE: ProvisionStatus = { active: false, percent: null, stage: null, error: null };

const PROVISION_TICK = /\[provisioning\]\s+(\d{1,3})%\s*([^—]*?)\s*(?:—\s*(.*))?$/;
const PROVISION_START = /fetching the engine runtime/i;
const PROVISION_FAILED = /^Provisioning failed:\s*(.*)$/;
const PROVISION_DONE = /^Runtime provisioned\b/;

/** Fold one log line into the provisioning state. Returns `prev` itself (same reference) for lines
 *  that say nothing about provisioning, so subscribers can skip re-renders. */
export function applyProvisionLine(prev: ProvisionStatus, line: string): ProvisionStatus {
  const tick = PROVISION_TICK.exec(line);
  if (tick) {
    const error = tick[3] ? tick[3].trim() : "";
    return {
      active: !error,
      percent: Math.min(100, Number(tick[1])),
      stage: tick[2].trim() || null,
      error: error || null,
    };
  }
  const failed = PROVISION_FAILED.exec(line);
  if (failed) return { ...prev, active: false, error: failed[1].trim() || "provisioning failed" };
  if (PROVISION_DONE.test(line)) return { active: true, percent: 100, stage: "done", error: null };
  if (PROVISION_START.test(line)) return { active: true, percent: null, stage: null, error: null };
  return prev;
}

/** True when two states are field-equal (subscribers publish only on a change). */
export function sameProvisionStatus(a: ProvisionStatus, b: ProvisionStatus): boolean {
  return a === b || (a.active === b.active && a.percent === b.percent && a.stage === b.stage && a.error === b.error);
}

/** The provisioning state after all `logs` (oldest first). */
export function provisionStatus(logs: readonly string[]): ProvisionStatus {
  return logs.reduce(applyProvisionLine, PROVISION_IDLE);
}

const VERIFY_STAGE = /pr(?:ü|ue)f|verif/i;
const UNPACK_STAGE = /entpack|unpack|extract/i;

/** Friendly status text for a provisioning phase: the download carries its percent; the SHA check and
 *  the extraction (which report no byte progress) get a clear text instead of a frozen number. */
export function provisionLabel(p: ProvisionStatus): string {
  if (p.stage && VERIFY_STAGE.test(p.stage)) return "Verifying the engine runtime…";
  if (p.stage && UNPACK_STAGE.test(p.stage)) return "Unpacking the engine runtime…";
  if (p.percent === 100) return "Engine runtime ready, restarting once…";
  return p.percent === null ? DOWNLOAD_LABEL : `${DOWNLOAD_LABEL} ${p.percent}%`;
}

/** Friendly, human boot-status text derived from the raw engine log stream. Returns the furthest
 *  recognised phase, or the default "Starting engine…" when nothing matches yet. */
export function bootStatusLabel(logs: readonly string[]): string {
  if (!logs || logs.length === 0) return DEFAULT_LABEL;
  for (let i = PHASES.length - 1; i >= 0; i--) {
    const { label, patterns } = PHASES[i];
    if (!logs.some((line) => patterns.some((p) => p.test(line)))) continue;
    if (label !== DOWNLOAD_LABEL) return label;
    // Download phase: stage + latest percent, still as a friendly phrase.
    return provisionLabel(provisionStatus(logs));
  }
  return DEFAULT_LABEL;
}
