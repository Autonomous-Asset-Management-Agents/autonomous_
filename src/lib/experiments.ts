/**
 * Landing A/B experiments, assigned on the server (#4869, #4581 S1, ADR-026 Weg D).
 *
 * The `edge` function (functions/src/edge-handler.mjs) picks the variant per request from the
 * GrowthBook payload and writes it into the HTML it serves:
 *   <meta name="aaa-experiments" content="hero_positioning=positioning">
 * The browser only reads that element, before React mounts, so the first paint already shows the
 * variant (no flicker). No hashing, no query string, no exposure report, no cookie and no Web
 * Storage happen here — the server counts the exposure from the request it served.
 *
 * Without the element (static hosting, OSS build) every experiment renders its control, and the
 * gap is reported once per experiment with console.warn.
 */

export type Assignment = {
    value: string;
    inExperiment: boolean;
};

export interface Experiments {
    evaluate: (key: string, control?: string) => Assignment;
}

const TOKEN = /^[A-Za-z0-9_.-]+$/;

/** `key=value;key2=value2` → { key: value, … }; malformed entries are skipped. */
export const parseExperimentsMeta = (content: string | null | undefined): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const entry of (content ?? "").split(";")) {
        const i = entry.indexOf("=");
        if (i <= 0) continue;
        const key = entry.slice(0, i).trim();
        const value = entry.slice(i + 1).trim();
        if (TOKEN.test(key) && TOKEN.test(value)) out[key] = value;
    }
    return out;
};

export const createExperiments = ({ content }: { content: string | null | undefined }): Experiments => {
    const assigned = parseExperimentsMeta(content);
    const warned = new Set<string>();
    return {
        evaluate: (key, control = "control") => {
            const value = assigned[key];
            if (value === undefined) {
                if (!warned.has(key)) {
                    warned.add(key);
                    console.warn(`[experiments] "${key}": no variant from the server (meta aaa-experiments) → control "${control}"`);
                }
                return { value: control, inExperiment: false };
            }
            return { value, inExperiment: true };
        },
    };
};

export const experimentsFromDocument = (doc: Document | undefined = typeof document !== "undefined" ? document : undefined): Experiments =>
    createExperiments({ content: doc?.querySelector('meta[name="aaa-experiments"]')?.getAttribute("content") });

export const landingExperiments = experimentsFromDocument();
