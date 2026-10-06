import type { DesignVariant } from "@/hooks/useDesignVariant";
import type { PageKey } from "./types";

/**
 * Derive a stable Firestore document id from the current variant + pathname.
 * Root routes use just the variant name; sub-routes append the path with
 * slashes replaced by underscores so the key stays a single doc id.
 */
export function derivePageKey(variant: DesignVariant, pathname: string): PageKey {
    const cleaned = pathname.replace(/^\/+|\/+$/g, "");
    if (!cleaned) return variant;
    return `${variant}__${cleaned.replace(/\//g, "_")}`;
}
