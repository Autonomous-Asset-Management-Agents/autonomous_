import { useCallback, useMemo, useState } from "react";

/**
 * UXC-1 S4 (#3157, Epic #3151) — shared pending-edit state for settings cards.
 *
 * Holds a STRING map (setup.json convention: every engine value travels as a string)
 * of pending values seeded from an engine-provided baseline. Nothing here ever
 * persists — applying pending values is the S3 WORM-audited flow's job (#3156).
 *
 * Strict boundary (Archon audit action item): values are `string`, never `unknown`;
 * the baseline is read-only; diffs are computed against an explicit base map so the
 * caller decides whether it compares against engine-current (apply payload) or
 * shipped defaults ("Changed" badge).
 */
export interface PendingSettings {
  /** Pending values (baseline ∪ edits); null until a baseline was provided. */
  values: Readonly<Record<string, string>> | null;
  /** Stage one pending value. No persistence side effects. */
  set: (key: string, value: string) => void;
  /** Replace ALL pending values (e.g. "Default settings"). No persistence side effects. */
  replaceAll: (next: Readonly<Record<string, string>>) => void;
  /** Keys whose pending value differs from `base`, with their pending values. */
  diffAgainst: (base: Readonly<Record<string, string>>) => Record<string, string>;
}

export function usePendingSettings(
  baseline: Readonly<Record<string, string>> | null,
): PendingSettings {
  const [edits, setEdits] = useState<Record<string, string>>({});

  // Discard stale edits when a FRESH baseline arrives (render-adjust pattern from
  // react.dev "You might not need an effect" — no setState inside an effect body).
  const [seenBaseline, setSeenBaseline] = useState(baseline);
  if (baseline !== seenBaseline) {
    setSeenBaseline(baseline);
    setEdits({});
  }

  const values = useMemo(
    () => (baseline ? { ...baseline, ...edits } : null),
    [baseline, edits],
  );

  const set = useCallback((key: string, value: string) => {
    setEdits((e) => ({ ...e, [key]: value }));
  }, []);

  const replaceAll = useCallback((next: Readonly<Record<string, string>>) => {
    setEdits({ ...next });
  }, []);

  const diffAgainst = useCallback(
    (base: Readonly<Record<string, string>>): Record<string, string> => {
      const out: Record<string, string> = {};
      if (!values) return out;
      for (const [key, value] of Object.entries(values)) {
        if (base[key] !== value) out[key] = value;
      }
      return out;
    },
    [values],
  );

  return { values, set, replaceAll, diffAgainst };
}
