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
const PROVISION_PCT = /\[provisioning\]\s+(\d{1,3})%/i;

/** Friendly, human boot-status text derived from the raw engine log stream. Returns the furthest
 *  recognised phase, or the default "Starting engine…" when nothing matches yet. */
export function bootStatusLabel(logs: readonly string[]): string {
  if (!logs || logs.length === 0) return DEFAULT_LABEL;
  for (let i = PHASES.length - 1; i >= 0; i--) {
    const { label, patterns } = PHASES[i];
    if (!logs.some((line) => patterns.some((p) => p.test(line)))) continue;
    if (label !== DOWNLOAD_LABEL) return label;
    // Download phase: show the latest reported percent, still as a friendly phrase.
    for (let j = logs.length - 1; j >= 0; j--) {
      const m = PROVISION_PCT.exec(logs[j]);
      if (m) return `${DOWNLOAD_LABEL} ${m[1]}%`;
    }
    return label;
  }
  return DEFAULT_LABEL;
}
