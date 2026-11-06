/**
 * Build switch the privacy policy (Legal.tsx, #4581) discloses on.
 *
 * #4870 (#4581 S2, ADR-026 Weg D): the `edge` function counts on the server; nothing measures in
 * the browser. The policy shows the Weg-D section 3 (c) only when VITE_ANALYTICS_ENABLED is
 * exactly "true". The landing deploy (.github/workflows/deploy-demo.yml) sets it from the same
 * repository variable it hands the function as ANALYTICS_ENABLED, so text and counting switch
 * together. Unset (every build today) → the policy says that nothing is measured.
 */
type AnalyticsEnv = {
    VITE_ANALYTICS_ENABLED?: string;
};

export const measurementEnabled = (env: AnalyticsEnv): boolean => env.VITE_ANALYTICS_ENABLED === "true";
