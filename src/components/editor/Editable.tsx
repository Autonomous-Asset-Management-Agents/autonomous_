import { type CSSProperties, type ReactNode, type MouseEvent, createElement, useMemo } from "react";
import { useOptionalEditor } from "@/lib/editor/EditorContext";

type Tag = "h1" | "h2" | "h3" | "h4" | "p" | "span" | "div" | "button" | "a" | "li";

interface Props {
    /** Stable id, e.g. "landing-b.hero.headline". MUST be unique within the page. */
    id: string;
    /** HTML tag to render. */
    as: Tag;
    className?: string;
    children: ReactNode;
    /** Forwarded for non-editable behaviour (e.g. anchor href). */
    href?: string;
}

/**
 * Wraps a piece of marketing content with a stable id so the editor can
 * select, override, and persist changes to it. When no EditorProvider is
 * mounted, falls back to rendering children with the requested tag — so
 * the component is safe to use in any tree.
 */
export function Editable({ id, as, className, children, href }: Props) {
    const editor = useOptionalEditor();
    const override = editor?.overrides[id];
    const selected = editor?.selectedId === id;
    const editorActive = !!editor && editor.editorEmail !== "";

    const style: CSSProperties = useMemo(() => {
        const s: CSSProperties = {};
        if (override?.style?.width) s.width = override.style.width;
        if (override?.style?.height) s.height = override.style.height;
        if (override?.style?.transform) s.transform = override.style.transform;
        if (override?.style?.fontSize) s.fontSize = override.style.fontSize;
        if (override?.style?.color) s.color = override.style.color;
        if (override?.style?.fontWeight) s.fontWeight = override.style.fontWeight;
        return s;
    }, [override]);

    const text = override?.text;
    const content = text !== undefined ? text : children;

    const onClick = editorActive
        ? (e: MouseEvent) => {
              e.preventDefault();
              e.stopPropagation();
              editor!.select(id);
          }
        : undefined;

    const dataAttrs: Record<string, string | undefined> = {
        "data-editable-id": id,
    };
    if (editorActive && selected) {
        dataAttrs["data-editor-selected"] = "true";
    }

    return createElement(
        as,
        {
            className,
            style,
            href,
            onClick,
            ...dataAttrs,
        },
        content,
    );
}
