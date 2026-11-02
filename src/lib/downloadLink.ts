/**
 * Public download link (#4870, #4581 S2, Plan §3c, ADR-026 Weg D).
 *
 * On the public landing the `edge` function serves the page and always writes
 * <meta name="aaa-experiments">. There the buttons go to /download/<os>[?v=<variant>]: edge counts
 * the download with the variant and redirects (302) to the GitHub release. Without that element the
 * page did not come through edge (console host, OSS install, dev server, tests) and /download/<os>
 * does not exist, so the direct GitHub URL stays. The direct URLs remain the redirect target and the
 * base of the release-liveness guard #3306 (scripts/check-download-links.mjs).
 */
import { MAC_DOWNLOAD_URL, WINDOWS_DOWNLOAD_URL, publicDownloadPath } from "@/lib/appVersion";
import { parseExperimentsMeta } from "@/lib/experiments";

const edgeMeta = (doc: Document | undefined): Element | null =>
    doc?.querySelector('meta[name="aaa-experiments"]') ?? null;

/** The variant the server assigned on this page; with several experiments, the first one. */
const assignedVariant = (meta: Element): string | undefined =>
    Object.values(parseExperimentsMeta(meta.getAttribute("content")))[0];

export const downloadHref = (
    os: "mac" | "windows",
    doc: Document | undefined = typeof document !== "undefined" ? document : undefined,
): string => {
    const meta = edgeMeta(doc);
    if (!meta) return os === "mac" ? MAC_DOWNLOAD_URL : WINDOWS_DOWNLOAD_URL;
    return publicDownloadPath(os, assignedVariant(meta));
};
