/**
 * Override applied on top of an Editable element's source-code defaults.
 * Stored at editor_overrides/{pageKey}/elements/{elementId} in Firestore.
 */
export interface ElementOverride {
    /** Stable identifier hardcoded in source (e.g. "landing-b.hero.headline"). */
    id: string;
    /** Replaces the element's default text content when present. */
    text?: string;
    /** Inline style overrides — only the keys we expose in the inspector. */
    style?: {
        width?: string;
        height?: string;
        /** translate(Xpx, Ypx) for repositioning without breaking layout flow. */
        transform?: string;
        fontSize?: string;
        color?: string;
        fontWeight?: string;
    };
    /** epoch ms; used for last-write-wins resolution. */
    updatedAt: number;
    /** Email of the editor who made the change. */
    updatedBy: string;
}

/** All overrides for one page, keyed by element id. */
export type OverrideMap = Record<string, ElementOverride>;

/** A page key is the Firestore doc id under editor_overrides/. */
export type PageKey = string;
