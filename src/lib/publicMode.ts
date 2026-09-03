/**
 * Public view-only mode: when true, the app shows only insights (portfolio, dashboard, etc.)
 * and hides all controls (start/stop, strategy, simulation, learning, panic-sell, chat commands).
 *
 * Enabled when:
 * - VITE_PUBLIC_VIEW_ONLY=true at build time, or
 * - Hostname is aaagents.de / www.aaagents.de (so one build can serve both)
 */
export function isPublicViewOnly(): boolean {
  if (typeof window === "undefined") {
    return import.meta.env?.VITE_PUBLIC_VIEW_ONLY === "true";
  }
  if (import.meta.env?.VITE_PUBLIC_VIEW_ONLY === "true") return true;
  const host = window.location.hostname.toLowerCase();
  return host === "aaagents.de" || host === "www.aaagents.de";
}

/** Public API base URL when in public mode (e.g. https://api.aaagents.de). Set via VITE_PUBLIC_API_URL. */
export function getPublicApiBase(): string {
  const url = import.meta.env?.VITE_PUBLIC_API_URL;
  if (url && typeof url === "string" && url.trim()) return url.trim().replace(/\/$/, "");
  return "";
}
