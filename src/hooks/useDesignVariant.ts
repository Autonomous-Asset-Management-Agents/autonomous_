import { useState, useEffect } from "react";
import { getValue, fetchAndActivate } from "firebase/remote-config";
import { remoteConfig } from "@/lib/firebase";

export type DesignVariant = "v1" | "stitch-v1" | "landing-b";

const VALID_VARIANTS: DesignVariant[] = ["v1", "stitch-v1", "landing-b"];

/**
 * Hostname-based default: console.* domains serve the authenticated console
 * (v1), everything else (aaagents.de, demo.aaagents.de, *.web.app previews)
 * serves the public marketing page (landing-b). Overridable by URL param,
 * sessionStorage, or Firebase Remote Config — those always win.
 */
const hostnameDefault = (): DesignVariant => {
    if (typeof window === "undefined") return "landing-b";
    const host = window.location.hostname.toLowerCase();
    if (host.startsWith("console.") || host === "localhost" && window.location.port === "8081") {
        return "v1";
    }
    return "landing-b";
};

/**
 * Liest den aktiven Design-Variant aus folgenden Quellen (Priorität absteigend):
 *  1. URL-Parameter  ?variant=stitch-v1  (Dev/QA Override — kein Deploy nötig)
 *  2. Firebase Remote Config (A/B Testing in Production)
 *  3. Hostname-based default (console.* → v1, public → landing-b)
 */
export const useDesignVariant = () => {
    const readVariant = (): DesignVariant => {
        // 1. URL-Parameter hat höchste Priorität (Dev & QA Override)
        if (typeof window !== "undefined") {
            const search = window.location.search;
            const params = new URLSearchParams(search);
            let urlVariant = params.get("variant");

            // Robustness check: if params.get fails, check for malformed keys (e.g., variant%3Dstitch-v1)
            if (!urlVariant) {
                for (const key of params.keys()) {
                    if (key.startsWith("variant=")) {
                        const parts = key.split("=");
                        if (parts.length > 1) {
                            urlVariant = parts[1];
                            break;
                        }
                    }
                }
            }

            if (urlVariant && VALID_VARIANTS.includes(urlVariant as DesignVariant)) {
                sessionStorage.setItem("design_variant_override", urlVariant);
                return urlVariant as DesignVariant;
            }
            // Check if we previously saved an override in this session
            const sessionVariant = sessionStorage.getItem("design_variant_override");
            if (sessionVariant && VALID_VARIANTS.includes(sessionVariant as DesignVariant)) {
                return sessionVariant as DesignVariant;
            }
        }
        // 2. Firebase Remote Config (synchron aus defaultConfig oder gecachtem Wert)
        const val = getValue(remoteConfig, "design_version").asString();
        if (VALID_VARIANTS.includes(val as DesignVariant)) return val as DesignVariant;
        // 3. Hostname-based fallback — console.* keeps v1, public domains get landing-b
        return hostnameDefault();
    };

    const [variant, setVariant] = useState<DesignVariant>(readVariant);

    useEffect(() => {
        // override gesetzt → Remote Config nicht überschreiben
        const params = new URLSearchParams(window.location.search);
        if (params.get("variant") || sessionStorage.getItem("design_variant_override")) return;

        // Remote Config im Hintergrund laden und Variante ggf. aktualisieren
        fetchAndActivate(remoteConfig)
            .then(() => {
                const val = getValue(remoteConfig, "design_version").asString();
                if (VALID_VARIANTS.includes(val as DesignVariant)) {
                    setVariant(val as DesignVariant);
                }
            })
            .catch((err) => console.warn("[VariantRouter] Remote Config fetch failed (using default):", err));
    }, []);

    return { variant };
};
