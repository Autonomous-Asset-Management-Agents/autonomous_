import { describe, it, expect, vi, beforeEach } from "vitest";

const mockGetDocs = vi.fn();
const mockWriteBatch = vi.fn();
const mockBatchSet = vi.fn();
const mockBatchDelete = vi.fn();
const mockBatchCommit = vi.fn();
const mockCollection = vi.fn();
const mockDoc = vi.fn();

vi.mock("firebase/firestore", () => ({
    collection: (...args: unknown[]) => mockCollection(...args),
    doc: (...args: unknown[]) => mockDoc(...args),
    getDocs: (...args: unknown[]) => mockGetDocs(...args),
    writeBatch: (...args: unknown[]) => mockWriteBatch(...args),
}));

vi.mock("@/lib/firebase", () => ({ db: { __mock: "db" } }));

import { fetchOverrides, publishOverrides } from "../overridesStore";
import type { OverrideMap } from "../types";

beforeEach(() => {
    mockGetDocs.mockReset();
    mockWriteBatch.mockReset();
    mockBatchSet.mockReset();
    mockBatchDelete.mockReset();
    mockBatchCommit.mockReset();
    mockCollection.mockReset();
    mockDoc.mockReset();
    mockWriteBatch.mockReturnValue({
        set: mockBatchSet,
        delete: mockBatchDelete,
        commit: mockBatchCommit,
    });
    mockBatchCommit.mockResolvedValue(undefined);
});

describe("fetchOverrides", () => {
    it("returns an empty map when the page has no overrides", async () => {
        mockGetDocs.mockResolvedValue({ docs: [] });
        const result = await fetchOverrides("landing-b");
        expect(result).toEqual({});
    });

    it("maps Firestore docs into the OverrideMap shape", async () => {
        mockGetDocs.mockResolvedValue({
            docs: [
                {
                    id: "hero.headline",
                    data: () => ({
                        id: "hero.headline",
                        text: "New headline",
                        updatedAt: 123,
                        updatedBy: "georg@aaagents.de",
                    }),
                },
            ],
        });
        const result = await fetchOverrides("landing-b");
        expect(result["hero.headline"].text).toBe("New headline");
    });
});

describe("publishOverrides", () => {
    it("writes each override and commits the batch", async () => {
        const overrides: OverrideMap = {
            "hero.headline": {
                id: "hero.headline",
                text: "Hi",
                updatedAt: 1,
                updatedBy: "georg@aaagents.de",
            },
        };
        await publishOverrides("landing-b", overrides, []);
        expect(mockBatchSet).toHaveBeenCalledTimes(1);
        expect(mockBatchCommit).toHaveBeenCalledTimes(1);
    });

    it("deletes ids listed in the deletions array", async () => {
        await publishOverrides("landing-b", {}, ["hero.cta"]);
        expect(mockBatchDelete).toHaveBeenCalledTimes(1);
        expect(mockBatchCommit).toHaveBeenCalledTimes(1);
    });
});
