import { useEffect } from "react";
import { EditorProvider } from "@/lib/editor/EditorContext";
import { useOverrides } from "@/lib/editor/useOverrides";
import { publishOverrides } from "@/lib/editor/overridesStore";
import { EditorToolbar } from "./EditorToolbar";
import { InspectorPanel } from "./InspectorPanel";
import { SelectionFrame } from "./SelectionFrame";
import "@/styles/editor.css";

interface Props {
    pageKey: string;
    editorEmail: string;
    children: React.ReactNode;
}

/**
 * Mounted only when useEditMode().active is true. Loads existing overrides,
 * provides edit context to descendants, and renders the editor chrome.
 */
export function EditorRoot({ pageKey, editorEmail, children }: Props) {
    const { overrides, loading, error } = useOverrides(pageKey);

    useEffect(() => {
        document.body.classList.add("editor-mode");
        return () => {
            document.body.classList.remove("editor-mode");
        };
    }, []);

    if (loading) {
        return (
            <div className="editor-loading">Loading overrides…{children}</div>
        );
    }
    if (error) {
        return (
            <div className="editor-error">
                Failed to load overrides: {error.message}
                {children}
            </div>
        );
    }

    return (
        <EditorProvider
            pageKey={pageKey}
            editorEmail={editorEmail}
            initial={overrides}
            onPublish={(map, deletions) => publishOverrides(pageKey, map, deletions)}
        >
            <div className="editor-ui-root">
                <EditorToolbar />
                <InspectorPanel />
                <SelectionFrame />
                <div className="editor-canvas">{children}</div>
            </div>
        </EditorProvider>
    );
}
