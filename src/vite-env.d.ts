/// <reference types="vite/client" />

interface ImportMetaEnv {
    /**
     * "true" → enable telemetry-emitting Firebase features (App Check,
     * Remote Config fetch, Analytics). Default (undefined / any other value)
     * disables them so OSS installs leak no data on page load.
     * Only the private/aaagents.de build sets this to "true" at build time.
     */
    readonly VITE_ENABLE_FIREBASE?: string;
    readonly VITE_RECAPTCHA_SITE_KEY?: string;
    /**
     * #4467 (ADR-026): origin of the self-hosted Umami instance, e.g.
     * "https://analytics.aaagents.de", plus its website id. Both unset (OSS default)
     * → the landing's tracking client is a no-op.
     */
    readonly VITE_ANALYTICS_HOST?: string;
    readonly VITE_ANALYTICS_WEBSITE_ID?: string;
    /**
     * #4469 (ADR-026): GrowthBook features payload (JSON) embedded at build time, so landing
     * experiments are assigned before the first render. Unset (OSS default) → control everywhere.
     */
    readonly VITE_EXPERIMENTS?: string;
}

interface ImportMeta {
    readonly env: ImportMetaEnv;
}
