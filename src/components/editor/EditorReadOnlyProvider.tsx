import type { ReactNode } from "react";
import { EditorProvider } from "@/lib/editor/EditorContext";
import { useOverrides } from "@/lib/editor/useOverrides";

interface Props {
    pageKey: string;
    children: ReactNode;
}

const noopPublish = async () => {
    /* read-only — never called from non-editor visitors */
};

/**
 * For every visitor: loads overrides and exposes them through EditorContext
 * so <Editable> renders the override text/style. editorEmail is empty so
 * Editable's selection handler is a no-op.
 */
export function EditorReadOnlyProvider({ pageKey, children }: Props) {
    const { overrides, loading } = useOverrides(pageKey);
    if (loading) return <>{children}</>;
    return (
        <EditorProvider
            pageKey={pageKey}
            editorEmail=""
            initial={overrides}
            onPublish={noopPublish}
        >
            {children}
        </EditorProvider>
    );
}
