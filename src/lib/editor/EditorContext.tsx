/* eslint-disable react-refresh/only-export-components */
import {
    createContext,
    useCallback,
    useContext,
    useMemo,
    useRef,
    useState,
    type ReactNode,
} from "react";
import type { ElementOverride, OverrideMap, PageKey } from "./types";

interface History {
    current(): OverrideMap;
    push(snapshot: OverrideMap): void;
    undo(): OverrideMap | null;
    redo(): OverrideMap | null;
    canUndo(): boolean;
    canRedo(): boolean;
    reset(snapshot: OverrideMap): void;
}

const HISTORY_LIMIT = 50;

/** In-memory undo/redo stack of override snapshots. */
export function createHistory(initial: OverrideMap): History {
    let past: OverrideMap[] = [];
    let present: OverrideMap = initial;
    let future: OverrideMap[] = [];

    return {
        current: () => present,
        push: (snapshot) => {
            past = [...past, present].slice(-HISTORY_LIMIT);
            present = snapshot;
            future = [];
        },
        undo: () => {
            if (past.length === 0) return null;
            future = [present, ...future];
            present = past[past.length - 1];
            past = past.slice(0, -1);
            return present;
        },
        redo: () => {
            if (future.length === 0) return null;
            past = [...past, present];
            present = future[0];
            future = future.slice(1);
            return present;
        },
        canUndo: () => past.length > 0,
        canRedo: () => future.length > 0,
        reset: (snapshot) => {
            past = [];
            present = snapshot;
            future = [];
        },
    };
}

export interface EditorContextValue {
    pageKey: PageKey;
    editorEmail: string;
    overrides: OverrideMap;
    selectedId: string | null;
    dirty: boolean;
    canUndo: boolean;
    canRedo: boolean;
    select: (id: string | null) => void;
    updateOverride: (
        id: string,
        patch: Partial<Omit<ElementOverride, "id" | "updatedAt" | "updatedBy">>,
    ) => void;
    deleteOverride: (id: string) => void;
    undo: () => void;
    redo: () => void;
    publish: () => Promise<void>;
    resetAll: () => Promise<void>;
}

const Ctx = createContext<EditorContextValue | null>(null);

export function useEditor(): EditorContextValue {
    const v = useContext(Ctx);
    if (!v) throw new Error("useEditor must be used inside <EditorProvider>");
    return v;
}

/** Same as useEditor but returns null instead of throwing when no provider is mounted. */
export function useOptionalEditor(): EditorContextValue | null {
    return useContext(Ctx);
}

interface ProviderProps {
    pageKey: PageKey;
    editorEmail: string;
    initial: OverrideMap;
    onPublish: (overrides: OverrideMap, deletions: string[]) => Promise<void>;
    children: ReactNode;
}

export function EditorProvider({
    pageKey,
    editorEmail,
    initial,
    onPublish,
    children,
}: ProviderProps) {
    const [history] = useState<History>(() => createHistory(initial));
    const [overrides, setOverrides] = useState<OverrideMap>(initial);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [publishedSnapshot, setPublishedSnapshot] = useState<OverrideMap>(initial);
    const [pendingDeletions, setPendingDeletions] = useState<Set<string>>(new Set());
    const [, forceVersion] = useState(0);

    const apply = useCallback((next: OverrideMap) => {
        history.push(next);
        setOverrides(next);
        forceVersion((v) => v + 1);
    }, [history]);

    const updateOverride = useCallback<EditorContextValue["updateOverride"]>(
        (id, patch) => {
            const prev = overrides[id];
            const next: ElementOverride = {
                id,
                text: patch.text !== undefined ? patch.text : prev?.text,
                style: { ...(prev?.style ?? {}), ...(patch.style ?? {}) },
                updatedAt: Date.now(),
                updatedBy: editorEmail,
            };
            const nextMap: OverrideMap = { ...overrides, [id]: next };
            apply(nextMap);
            setPendingDeletions((s) => {
                if (!s.has(id)) return s;
                const copy = new Set(s);
                copy.delete(id);
                return copy;
            });
        },
        [overrides, editorEmail, apply],
    );

    const deleteOverride = useCallback<EditorContextValue["deleteOverride"]>(
        (id) => {
            const { [id]: _omit, ...rest } = overrides;
            apply(rest);
            setPendingDeletions((s) => {
                const copy = new Set(s);
                copy.add(id);
                return copy;
            });
        },
        [overrides, apply],
    );

    const undo = useCallback(() => {
        const next = history.undo();
        if (next) {
            setOverrides(next);
            forceVersion((v) => v + 1);
        }
    }, [history]);

    const redo = useCallback(() => {
        const next = history.redo();
        if (next) {
            setOverrides(next);
            forceVersion((v) => v + 1);
        }
    }, [history]);

    const publish = useCallback(async () => {
        await onPublish(overrides, Array.from(pendingDeletions));
        setPublishedSnapshot(overrides);
        setPendingDeletions(new Set());
    }, [overrides, pendingDeletions, onPublish]);

    const resetAll = useCallback(async () => {
        const allIds = Object.keys(publishedSnapshot);
        await onPublish({}, allIds);
        history.reset({});
        setOverrides({});
        setPublishedSnapshot({});
        setPendingDeletions(new Set());
        forceVersion((v) => v + 1);
    }, [publishedSnapshot, onPublish, history]);

    const value = useMemo<EditorContextValue>(
        () => ({
            pageKey,
            editorEmail,
            overrides,
            selectedId,
            dirty:
                JSON.stringify(overrides) !== JSON.stringify(publishedSnapshot) ||
                pendingDeletions.size > 0,
            canUndo: history.canUndo(),
            canRedo: history.canRedo(),
            select: setSelectedId,
            updateOverride,
            deleteOverride,
            undo,
            redo,
            publish,
            resetAll,
        }),
        [
            pageKey,
            editorEmail,
            overrides,
            selectedId,
            publishedSnapshot,
            pendingDeletions,
            updateOverride,
            deleteOverride,
            undo,
            redo,
            publish,
            resetAll,
            history,
        ],
    );

    return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
