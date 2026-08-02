import { useEditor } from "@/lib/editor/EditorContext";

export function InspectorPanel() {
    const { selectedId, overrides, updateOverride, deleteOverride, select } = useEditor();
    if (!selectedId) {
        return (
            <aside className="editor-inspector editor-inspector--empty">
                <p>Click any element to edit it.</p>
            </aside>
        );
    }
    const ov = overrides[selectedId];
    const style = ov?.style ?? {};

    return (
        <aside className="editor-inspector">
            <header className="editor-inspector__header">
                <span className="editor-inspector__id">{selectedId}</span>
                <button type="button" onClick={() => select(null)} aria-label="Close inspector">×</button>
            </header>

            <label className="editor-inspector__field">
                <span>Text</span>
                <textarea
                    rows={3}
                    value={ov?.text ?? ""}
                    placeholder="(uses source default)"
                    onChange={(e) => updateOverride(selectedId, { text: e.target.value })}
                />
            </label>

            <div className="editor-inspector__row">
                <label>
                    <span>Width</span>
                    <input
                        type="text"
                        placeholder="auto"
                        value={style.width ?? ""}
                        onChange={(e) => updateOverride(selectedId, { style: { width: e.target.value } })}
                    />
                </label>
                <label>
                    <span>Height</span>
                    <input
                        type="text"
                        placeholder="auto"
                        value={style.height ?? ""}
                        onChange={(e) => updateOverride(selectedId, { style: { height: e.target.value } })}
                    />
                </label>
            </div>

            <label className="editor-inspector__field">
                <span>Position (transform)</span>
                <input
                    type="text"
                    placeholder="translate(0px, 0px)"
                    value={style.transform ?? ""}
                    onChange={(e) =>
                        updateOverride(selectedId, { style: { transform: e.target.value } })
                    }
                />
            </label>

            <div className="editor-inspector__row">
                <label>
                    <span>Font size</span>
                    <input
                        type="text"
                        placeholder="inherit"
                        value={style.fontSize ?? ""}
                        onChange={(e) =>
                            updateOverride(selectedId, { style: { fontSize: e.target.value } })
                        }
                    />
                </label>
                <label>
                    <span>Color</span>
                    <input
                        type="text"
                        placeholder="inherit"
                        value={style.color ?? ""}
                        onChange={(e) =>
                            updateOverride(selectedId, { style: { color: e.target.value } })
                        }
                    />
                </label>
            </div>

            <button
                type="button"
                className="editor-inspector__reset"
                onClick={() => deleteOverride(selectedId)}
            >
                Reset this element
            </button>
        </aside>
    );
}
