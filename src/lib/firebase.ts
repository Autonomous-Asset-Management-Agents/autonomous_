/**
 * Firebase SDK initialization for AAA Console.
 *
 * Telemetry-triggering features (App Check / Remote Config fetch / Analytics)
 * are gated behind VITE_ENABLE_FIREBASE. With the flag off — the OSS default —
 * no network requests reach Firebase on page load, so OSS installs do not leak
 * IP / user-agent / page-event data into our Firebase project (DSGVO).
 *
 * The Auth / Firestore / RemoteConfig SDK objects are still created so consumer
 * components (Header, PrivateRoute, useAuthState, etc.) keep type-checking and
 * compile cleanly. None of those constructors make network calls on their own.
 *
 * Config values are public Firebase identifiers (safe to commit). Auth is
 * enforced by Firebase Auth rules, not by hiding these keys.
 */
import { initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider } from "firebase/auth";
import { initializeAppCheck, ReCaptchaV3Provider } from "firebase/app-check";
import { getFirestore } from "firebase/firestore";
import { getRemoteConfig, fetchAndActivate } from "firebase/remote-config";
import { getAnalytics, isSupported, logEvent, Analytics } from "firebase/analytics";

// OSS default: undefined → false. Private/aaagents.de build sets
// VITE_ENABLE_FIREBASE=true at `npm run build` time to opt back in.
const FIREBASE_ENABLED =
    (import.meta.env.VITE_ENABLE_FIREBASE as string | undefined) === "true";

const firebaseConfig = {
    apiKey: "AIzaSyC_MCEqwuOgeIvqgJiDfXJT8o-oQDXwfdw",
    authDomain: "aaagents.firebaseapp.com",
    projectId: "aaagents",
    storageBucket: "aaagents.firebasestorage.app",
    messagingSenderId: "212931142298",
    appId: "1:212931142298:web:accdd3227c0211934d3523",
};

// initializeApp is local-only (no network). Always call so the SDK objects
// below have a real FirebaseApp to bind to and consumer code never sees nulls.
const app = initializeApp(firebaseConfig);

// ─── Telemetry-gated initialization ─────────────────────────────────────────
// Each block below initiates outbound HTTP/beacon traffic to Google. They run
// only when the operator opts in via VITE_ENABLE_FIREBASE=true.

if (FIREBASE_ENABLED) {
    // App Check — reCAPTCHA v3 attestation for Firestore + Storage writes.
    // The site key is public (like the Firebase apiKey above) and is injected
    // at build time via VITE_RECAPTCHA_SITE_KEY. When the env var is absent
    // (local dev without the key, or workflows that don't inject it yet) we
    // skip initialization so the bundle still works; Firebase App Check
    // enforcement in the console can then be toggled to "unenforced" for
    // those origins without breaking anything.
    const recaptchaSiteKey = import.meta.env.VITE_RECAPTCHA_SITE_KEY as string | undefined;
    if (recaptchaSiteKey && typeof window !== "undefined") {
        // Debug token for localhost — set `self.FIREBASE_APPCHECK_DEBUG_TOKEN = true`
        // in browser devtools before load to see the debug token in the console log,
        // then register it in Firebase → App Check → Apps → debug tokens.
        initializeAppCheck(app, {
            provider: new ReCaptchaV3Provider(recaptchaSiteKey),
            isTokenAutoRefreshEnabled: true,
        });
    }
} else if (typeof console !== "undefined") {
    console.info(
        "[firebase] telemetry disabled (VITE_ENABLE_FIREBASE != 'true') — " +
        "no App Check, no Remote Config fetch, no Analytics. SDK objects exported as-is."
    );
}

export const auth = getAuth(app);
export const googleProvider = new GoogleAuthProvider();

// Firestore — used by the landing-b waitlist capture (write-only for anon users).
export const db = getFirestore(app);

export const remoteConfig = getRemoteConfig(app);
// For development/testing we might want lower intervals, but for prod 1h is standard
remoteConfig.settings.minimumFetchIntervalMillis = 3600000;
// No baked-in default for design_version — useDesignVariant falls back to a
// hostname-based selection (console.* → v1, public → landing-b) when Remote
// Config returns an empty value, which lets the same bundle serve both
// console and marketing hosts without a per-site Remote Config condition.
remoteConfig.defaultConfig = {};

if (FIREBASE_ENABLED) {
    // Start fetching the remote config immediately (network: Remote Config endpoint)
    fetchAndActivate(remoteConfig).catch(console.error);
}

// Analytics Initialization (network: GA4 beacons + cookies). Gated.
let analyticsInstance: Analytics | null = null;
if (FIREBASE_ENABLED) {
    isSupported().then((supported) => {
        if (supported) {
            analyticsInstance = getAnalytics(app);
        }
    });
}

/**
 * Tracks the shown UI variant to GA4 once the Consent Mode grants analytics_storage.
 * No-op when Firebase telemetry is disabled (analyticsInstance stays null).
 */
export const trackVariantImpression = (variantName: string) => {
    if (analyticsInstance) {
        logEvent(analyticsInstance, "experience_impression", {
            exp_variant: variantName
        });
    }
};

