/**
 * #2732 Strand B — resolve a deep-linked ticker (from a SymbolLink click) to the ACTUAL report
 * symbol present in the Reports list. A caller may pass a base ticker (e.g. the Overview strips the
 * exchange suffix: "BRK.B" -> "BRK"), which would otherwise match no row and open nothing. We try an
 * exact match first, then the same base symbol (before the "."), then fall back to the raw target.
 */
export function resolveDeepLinkSymbol(symbols: string[], target: string): string {
  const base = (s: string) => s.split(".")[0];
  return (
    symbols.find((s) => s === target) ??
    symbols.find((s) => base(s) === base(target)) ??
    target
  );
}
