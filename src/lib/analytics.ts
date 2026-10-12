/**
 * Cookieless tracking client for the public landing (#4467, ADR-026).
 *
 * Talks to the self-hosted Umami ingest endpoint (`POST <host>/api/send`, the vendor's documented
 * collection API) directly instead of loading Umami's tracker script: the script reads
 * `localStorage` ("umami.disabled"), and ADR-026 §3 rules out any storage access. Nothing is
 * written to or read from the device — no cookie, no localStorage/sessionStorage, no screen size —
 * and requests go out with `credentials: "omit"`.
 *
 * Enterprise-only (ADR-026 §7): without VITE_ANALYTICS_HOST and VITE_ANALYTICS_WEBSITE_ID at build
 * time every call is a no-op, so the OSS build sends nothing. The gap is reported once.
 */

type AnalyticsEnv = {
    VITE_ANALYTICS_HOST?: string;
    VITE_ANALYTICS_WEBSITE_ID?: string;
};

export type DownloadOs = "mac" | "windows";

export interface Analytics {
    trackPageview: () => void;
    trackCta: (cta: string) => void;
    trackDownload: (os: DownloadOs) => void;
    /** #4469: experiment assignment, named like GrowthBook's exposure event. */
    trackExposure: (experimentId: string, variationId: string) => void;
}

const ingestUrl = (host: string | undefined): string | null => {
    if (!host) return null;
    try {
        const url = new URL(host);
        return `${url.origin}${url.pathname.replace(/\/+$/, "")}/api/send`;
    } catch {
        return null;
    }
};

export const createAnalytics = (env: AnalyticsEnv): Analytics => {
    const endpoint = ingestUrl(env.VITE_ANALYTICS_HOST);
    const website = env.VITE_ANALYTICS_WEBSITE_ID;
    let warned = false;

    const send = (name?: string, data?: Record<string, string>) => {
        if (!endpoint || !website) {
            if (!warned) {
                warned = true;
                console.warn(
                    "[analytics] VITE_ANALYTICS_HOST / VITE_ANALYTICS_WEBSITE_ID missing or invalid " +
                    "→ tracking disabled (no-op)",
                );
            }
            return;
        }
        const payload = {
            website,
            hostname: window.location.hostname,
            language: navigator.language,
            referrer: document.referrer,
            title: document.title,
            url: window.location.pathname + window.location.search,
            ...(name ? { name, data } : {}),
        };
        fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ type: "event", payload }),
            credentials: "omit",
            keepalive: true,
        }).catch((err) => {
            console.warn(`[analytics] send failed → event "${name ?? "pageview"}" dropped`, err);
        });
    };

    return {
        trackPageview: () => send(),
        trackCta: (cta) => send("cta_click", { cta }),
        trackDownload: (os) => send("download", { os }),
        trackExposure: (experimentId, variationId) =>
            send("experiment_viewed", { experiment_id: experimentId, variation_id: variationId }),
    };
};

export const { trackPageview, trackCta, trackDownload, trackExposure } = createAnalytics(import.meta.env);
