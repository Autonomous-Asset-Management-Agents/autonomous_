import { useState } from "react";
import { useEditor } from "@/lib/editor/EditorContext";

export function EditorToolbar() {
    const { canUndo, canRedo, dirty, undo, redo, publish, resetAll, editorEmail } = useEditor();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const exitEditor = () => {
        const url = new URL(window.location.href);
        url.searchParams.delete("edit");
        window.location.assign(url.toString());
    };

    const wrap = async (fn: () => Promise<void>) => {
        setBusy(true);
        setError(null);
        try {
            await fn();
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <header className="editor-toolbar">
            <span className="editor-toolbar__brand">Editor</span>
            <button type="button" disabled={!canUndo || busy} onClick={undo}>↶ Undo</button>
            <button type="button" disabled={!canRedo || busy} onClick={redo}>↷ Redo</button>
            <span className="editor-toolbar__spacer" />
            <button
                type="button"
                disabled={!dirty || busy}
                onClick={() => wrap(publish)}
                className="editor-toolbar__publish"
            >
                {busy ? "Publishing…" : dirty ? "Publish" : "Up to date"}
            </button>
            <button
                type="button"
                disabled={busy}
                onClick={() => {
                    if (window.confirm("Delete every override on this page?")) void wrap(resetAll);
                }}
            >
                Reset page
            </button>
            <span className="editor-toolbar__user">{editorEmail}</span>
            <button type="button" onClick={exitEditor}>Exit</button>
            {error && <span className="editor-toolbar__error">{error}</span>}
        </header>
    );
}
