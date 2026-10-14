import { collection, doc, getDocs, writeBatch } from "firebase/firestore";
import { db } from "@/lib/firebase";
import type { ElementOverride, OverrideMap, PageKey } from "./types";

const ROOT = "editor_overrides";
const ELEMENTS = "elements";

/** Read every override under editor_overrides/{pageKey}/elements/. */
export async function fetchOverrides(pageKey: PageKey): Promise<OverrideMap> {
    const snap = await getDocs(collection(db, ROOT, pageKey, ELEMENTS));
    const out: OverrideMap = {};
    for (const d of snap.docs) {
        out[d.id] = d.data() as ElementOverride;
    }
    return out;
}

/**
 * Atomically write the supplied overrides and delete any ids in `deletions`.
 * Last-write-wins — caller is responsible for sequencing concurrent edits.
 */
export async function publishOverrides(
    pageKey: PageKey,
    overrides: OverrideMap,
    deletions: string[],
): Promise<void> {
    const batch = writeBatch(db);
    for (const id of Object.keys(overrides)) {
        batch.set(doc(db, ROOT, pageKey, ELEMENTS, id), overrides[id]);
    }
    for (const id of deletions) {
        batch.delete(doc(db, ROOT, pageKey, ELEMENTS, id));
    }
    await batch.commit();
}
