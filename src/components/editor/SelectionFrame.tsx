import { useEffect, useRef, useState } from "react";
import { Rnd } from "react-rnd";
import { useEditor } from "@/lib/editor/EditorContext";

/**
 * Floating overlay that mirrors the bounding box of the currently selected
 * editable element. Provides drag and resize via react-rnd; on drag/resize
 * end, writes a transform / width+height override into the editor context.
 *
 * The actual element stays in the DOM at its natural position — we only
 * apply CSS transform offsets, so reflow stays predictable.
 */
export function SelectionFrame() {
    const { selectedId, overrides, updateOverride } = useEditor();
    const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
    const baselineRef = useRef<{ x: number; y: number } | null>(null);

    useEffect(() => {
        if (!selectedId) {
            requestAnimationFrame(() => setBox(null));
            baselineRef.current = null;
            return;
        }
        const el = document.querySelector<HTMLElement>(`[data-editable-id="${selectedId}"]`);
        if (!el) return;
        const rect = el.getBoundingClientRect();
        
        requestAnimationFrame(() => {
            setBox({
                x: rect.left + window.scrollX,
                y: rect.top + window.scrollY,
                w: rect.width,
                h: rect.height,
            });
        });
        
        baselineRef.current = { x: rect.left + window.scrollX, y: rect.top + window.scrollY };
    }, [selectedId, overrides]);

    if (!selectedId || !box) return null;

    const parseTranslate = (transform: string | undefined): { x: number; y: number } => {
        if (!transform) return { x: 0, y: 0 };
        const m = transform.match(/translate\(\s*(-?\d+(?:\.\d+)?)px\s*,\s*(-?\d+(?:\.\d+)?)px\s*\)/);
        return m ? { x: Number(m[1]), y: Number(m[2]) } : { x: 0, y: 0 };
    };

    const currentTranslate = parseTranslate(overrides[selectedId]?.style?.transform);

    return (
        <Rnd
            className="editor-selection-frame"
            bounds="window"
            size={{ width: box.w, height: box.h }}
            position={{ x: box.x, y: box.y }}
            onDragStop={(_, d) => {
                if (!baselineRef.current) return;
                const dx = d.x - baselineRef.current.x + currentTranslate.x;
                const dy = d.y - baselineRef.current.y + currentTranslate.y;
                updateOverride(selectedId, {
                    style: { transform: `translate(${dx}px, ${dy}px)` },
                });
            }}
            onResizeStop={(_e, _dir, ref) => {
                updateOverride(selectedId, {
                    style: { width: `${ref.offsetWidth}px`, height: `${ref.offsetHeight}px` },
                });
            }}
        />
    );
}
