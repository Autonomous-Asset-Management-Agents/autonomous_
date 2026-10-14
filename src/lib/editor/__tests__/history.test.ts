import { describe, it, expect } from "vitest";
import { createHistory } from "../EditorContext";
import type { OverrideMap } from "../types";

const empty: OverrideMap = {};
const withHero: OverrideMap = {
    "hero.headline": {
        id: "hero.headline",
        text: "Hi",
        updatedAt: 1,
        updatedBy: "georg@aaagents.de",
    },
};

describe("history stack", () => {
    it("starts at the initial snapshot", () => {
        const h = createHistory(empty);
        expect(h.current()).toEqual(empty);
        expect(h.canUndo()).toBe(false);
        expect(h.canRedo()).toBe(false);
    });

    it("pushes a snapshot and enables undo", () => {
        const h = createHistory(empty);
        h.push(withHero);
        expect(h.current()).toEqual(withHero);
        expect(h.canUndo()).toBe(true);
    });

    it("undo returns to the prior snapshot", () => {
        const h = createHistory(empty);
        h.push(withHero);
        h.undo();
        expect(h.current()).toEqual(empty);
        expect(h.canRedo()).toBe(true);
    });

    it("redo replays an undone snapshot", () => {
        const h = createHistory(empty);
        h.push(withHero);
        h.undo();
        h.redo();
        expect(h.current()).toEqual(withHero);
    });

    it("push after undo discards the redo stack", () => {
        const h = createHistory(empty);
        h.push(withHero);
        h.undo();
        h.push(withHero);
        expect(h.canRedo()).toBe(false);
    });
});
