import titles from "./companyTitles.json";

/**
 * Company-name resolution for the console. Maps a bare ticker (e.g. "AAPL") to the full
 * company name (e.g. "Apple Inc.") using the bundled SEC EDGAR snapshot (`companyTitles.json`,
 * regenerated quarterly via `scripts/gen-company-names.mjs`). SEC titles are stored raw and
 * are often ALL-CAPS, so `formatCompanyName` prettifies them at lookup time. No network — fully
 * offline. Unknown tickers return null so callers fall back to showing the symbol.
 */

const TITLES = titles as Record<string, string>;

// Corporate-form tokens → canonical display form (checked before generic title-casing).
const SUFFIX: Record<string, string> = {
  inc: "Inc.", "inc.": "Inc.", incorporated: "Inc.",
  corp: "Corp.", "corp.": "Corp.", corporation: "Corp.",
  co: "Co.", "co.": "Co.", company: "Co.",
  ltd: "Ltd.", "ltd.": "Ltd.", limited: "Ltd.",
  llc: "LLC", lp: "LP", plc: "PLC", sa: "SA", nv: "NV", ag: "AG", se: "SE",
  "&": "&",
};

/** Prettify a raw SEC title: Title-Case ALL-CAPS words, keep already-mixed-case tokens,
 *  normalize corporate suffixes. Pure + deterministic (unit-tested). */
export function formatCompanyName(raw: string): string {
  const s = (raw ?? "").trim();
  if (!s) return s;
  return s
    .split(/\s+/)
    .map((word) => {
      const lower = word.toLowerCase();
      if (SUFFIX[lower]) return SUFFIX[lower];
      // Already deliberately mixed-case (e.g. "Apple", "Tesla,", "McDonald's") → leave as authored.
      if (/[a-z]/.test(word) && /[A-Z]/.test(word)) return word;
      // ALL-CAPS or all-lower token → Title Case.
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(" ");
}

const cache = new Map<string, string | null>();

/** Full company name for a ticker, or null if unknown (caller falls back to the symbol). */
export function getCompanyName(symbol: string | null | undefined): string | null {
  if (!symbol) return null;
  const key = symbol.trim().toUpperCase();
  if (!key) return null;
  if (cache.has(key)) return cache.get(key) ?? null;
  // Exact, then class-share normalization (Alpaca "BRK.B" ↔ SEC "BRK-B").
  const rawTitle = TITLES[key] ?? TITLES[key.replace(/\./g, "-")] ?? null;
  const name = rawTitle ? formatCompanyName(rawTitle) : null;
  cache.set(key, name);
  return name;
}
