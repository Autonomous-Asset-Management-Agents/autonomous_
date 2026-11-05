/**
 * Public view-only mode: when true, the app shows only insights (portfolio, dashboard, etc.)
 * and hides all controls (start/stop, strategy, simulation, learning, panic-sell, chat commands).
 *
 * Enabled when:
 * - VITE_PUBLIC_VIEW_ONLY=true at build time, or
 * - Hostname is one of the public website domains (so one build can serve all of them):
 *   autonomous-trading.de (primary since 13.10.2026) and aaagents.de, each with www.
 */
const PUBLIC_HOSTS = new Set([
  "autonomous-trading.de",
  "www.autonomous-trading.de",
  "aaagents.de",
  "www.aaagents.de",
]);

export function isPublicViewOnly(): boolean {
  if (typeof window === "undefined") {
    return import.meta.env?.VITE_PUBLIC_VIEW_ONLY === "true";
  }
  if (import.meta.env?.VITE_PUBLIC_VIEW_ONLY === "true") return true;
  return PUBLIC_HOSTS.has(window.location.hostname.toLowerCase());
}

/** Public API base URL when in public mode (e.g. https://api.aaagents.de). Set via VITE_PUBLIC_API_URL. */
export function getPublicApiBase(): string {
  const url = import.meta.env?.VITE_PUBLIC_API_URL;
  if (url && typeof url === "string" && url.trim()) return url.trim().replace(/\/$/, "");
  return "";
}
