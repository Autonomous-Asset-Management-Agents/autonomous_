/**
 * Flicker-free A/B experiments for the public landing (#4469, ADR-026 §3).
 *
 * GrowthBook owns the experiment definitions; its features payload (the SDK endpoint's `features`
 * object) is embedded at build time as VITE_EXPERIMENTS, so the definitions are in the bundle
 * before React mounts and the assignment needs no network round trip. Assignment follows
 * GrowthBook's SDK semantics (FNV-1a hash v1/v2, bucket ranges, `?<key>=<index>` QA override) so
 * the stats engine sees the same split. No SDK dependency: only experiment rules without targeting
 * are evaluated; a rule with condition/namespace/filters/ranges renders control.
 *
 * The hash attribute is an id held in memory for this page load only — no cookie, no
 * localStorage/sessionStorage. A returning visitor can land in another variation; experiments are
 * therefore evaluated per session, not per person (ADR-026 §3).
 *
 * Enterprise-only (ADR-026 §7): without VITE_EXPERIMENTS every experiment renders its control.
 * Any error does the same and is reported with console.warn.
 */
import { trackExposure } from "@/lib/analytics";

export type ExperimentRule = {
    key?: string;
    variations: unknown[];
    weights?: number[];
    coverage?: number;
    hashAttribute?: string;
    seed?: string;
    hashVersion?: number;
    meta?: { key?: string }[];
    // Targeting this evaluator does not implement; a rule carrying it falls back to control.
    condition?: unknown;
    namespace?: unknown;
    filters?: unknown;
    ranges?: unknown;
};

const UNSUPPORTED: (keyof ExperimentRule)[] = ["condition", "namespace", "filters", "ranges"];

export type FeaturesPayload = {
    features?: Record<string, { defaultValue?: unknown; rules?: ExperimentRule[] }>;
};

export type Assignment = {
    value: string;
    inExperiment: boolean;
    experimentKey: string;
    variationId: string;
};

export interface Experiments {
    evaluate: (key: string, control?: string) => Assignment;
    reportExposure: (experimentKey: string, variationId: string) => void;
}

type ExperimentsOptions = {
    payload: string | undefined;
    id: string;
    search?: string;
    onExposure?: (experimentKey: string, variationId: string) => void;
};

const fnv1a32 = (str: string): number => {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) {
        h ^= str.charCodeAt(i);
        h += (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24);
    }
    return h >>> 0;
};

// GrowthBook hash: v2 is the current default of its rules, v1 the legacy one.
const hashToUnit = (seed: string, value: string, version: number): number =>
    version === 2
        ? (fnv1a32(String(fnv1a32(seed + value))) % 10000) / 10000
        : (fnv1a32(value + seed) % 1000) / 1000;

const chooseVariation = (n: number, rule: ExperimentRule): number => {
    const count = rule.variations.length;
    const coverage = Math.min(1, Math.max(0, rule.coverage ?? 1));
    const weights =
        rule.weights?.length === count && Math.abs(rule.weights.reduce((a, b) => a + b, 0) - 1) <= 0.01
            ? rule.weights
            : rule.variations.map(() => 1 / count);
    let start = 0;
    for (let i = 0; i < count; i++) {
        const end = start + coverage * weights[i];
        if (n >= start && n < end) return i;
        start += weights[i];
    }
    return -1;
};

const forcedVariation = (search: string, key: string, count: number): number | null => {
    const raw = new URLSearchParams(search).get(key);
    if (raw === null || !/^\d+$/.test(raw)) return null;
    const idx = Number(raw);
    return idx < count ? idx : null;
};

const parsePayload = (payload: string | undefined): FeaturesPayload | null => {
    if (!payload) return null;
    try {
        const parsed = JSON.parse(payload) as FeaturesPayload;
        return parsed && typeof parsed === "object" ? parsed : null;
    } catch (err) {
        console.warn("[experiments] VITE_EXPERIMENTS is not valid JSON → every experiment shows control", err);
        return null;
    }
};

export const createExperiments = ({ payload, id, search, onExposure }: ExperimentsOptions): Experiments => {
    const features = parsePayload(payload)?.features ?? {};
    const warned = new Set<string>();
    const reported = new Set<string>();

    const fallback = (key: string, control: string, reason: string): Assignment => {
        if (!warned.has(key)) {
            warned.add(key);
            console.warn(`[experiments] "${key}": ${reason} → control "${control}"`);
        }
        return { value: control, inExperiment: false, experimentKey: key, variationId: "" };
    };

    return {
        evaluate: (key, control = "control") => {
            const rule = features[key]?.rules?.find((r) => Array.isArray(r?.variations) && r.variations.length > 1);
            if (!rule) return fallback(key, control, "no experiment defined (VITE_EXPERIMENTS)");
            const unsupported = UNSUPPORTED.filter((f) => rule[f] !== undefined);
            if (unsupported.length) return fallback(key, control, `unsupported targeting (${unsupported.join(", ")})`);
            const experimentKey = rule.key ?? key;
            const count = rule.variations.length;

            let idx = forcedVariation(search ?? window.location.search, experimentKey, count);
            if (idx === null) {
                const seed = rule.seed ?? experimentKey;
                idx = chooseVariation(hashToUnit(seed, id, rule.hashVersion ?? 1), rule);
            }
            if (idx < 0) return { value: control, inExperiment: false, experimentKey, variationId: "" };

            const value = rule.variations[idx];
            if (typeof value !== "string") return fallback(key, control, `variation ${idx} is not a string`);
            return { value, inExperiment: true, experimentKey, variationId: rule.meta?.[idx]?.key ?? String(idx) };
        },
        reportExposure: (experimentKey, variationId) => {
            if (reported.has(experimentKey)) return;
            reported.add(experimentKey);
            onExposure?.(experimentKey, variationId);
        },
    };
};

// One in-memory id per page load (ADR-026 §3) — never persisted.
const pageLoadId = (): string =>
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

export const landingExperiments = createExperiments({
    payload: import.meta.env.VITE_EXPERIMENTS,
    id: pageLoadId(),
    onExposure: trackExposure,
});
