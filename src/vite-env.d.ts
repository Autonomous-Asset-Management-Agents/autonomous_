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
}

interface ImportMeta {
    readonly env: ImportMetaEnv;
}
