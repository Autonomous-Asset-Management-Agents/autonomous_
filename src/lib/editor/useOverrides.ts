import { useEffect, useState } from "react";
import { fetchOverrides } from "./overridesStore";
import type { OverrideMap, PageKey } from "./types";

export interface UseOverridesResult {
    overrides: OverrideMap;
    loading: boolean;
    error: Error | null;
    refresh: () => Promise<void>;
}

/**
 * Loads every override for the given page once on mount.
 * All visitors call this, not just editors — the read path is public.
 */
export function useOverrides(pageKey: PageKey): UseOverridesResult {
    const [overrides, setOverrides] = useState<OverrideMap>({});
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);

    const load = async () => {
        setLoading(true);
        try {
            const data = await fetchOverrides(pageKey);
            setOverrides(data);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e : new Error(String(e)));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        const timer = setTimeout(() => {
            void load();
        }, 0);
        return () => clearTimeout(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pageKey]);

    return { overrides, loading, error, refresh: load };
}
