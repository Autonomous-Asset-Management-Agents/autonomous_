import { useStore } from "@/console/store/useStore";

/**
 * SymbolLink — a clickable stock ticker that opens that symbol's Report.
 * RPT-GOLD Wave 2, Strand B (#2732).
 *
 * Rendered as a `role="link"` span (NOT a <button>) because several symbol sites
 * sit inside a parent <button> (Overview decision queue, Decisions card) — a
 * nested <button> is invalid HTML. It stops propagation so it never triggers the
 * parent row's onClick, and is keyboard-operable (Enter / Space). It inherits the
 * caller's ticker styling via `className` — no new colour, just a hover underline.
 */
export function SymbolLink({
  symbol,
  className = "",
  title,
}: {
  symbol: string;
  className?: string;
  title?: string;
}) {
  const setReportSymbol = useStore((s) => s.setReportSymbol);
  const setDesktopPage = useStore((s) => s.setDesktopPage);

  const go = (e: { stopPropagation: () => void }) => {
    e.stopPropagation();
    setReportSymbol(symbol);
    setDesktopPage("reports");
  };

  return (
    <span
      role="link"
      tabIndex={0}
      onClick={go}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          go(e);
        }
      }}
      title={title ?? `Open ${symbol} report`}
      className={`cursor-pointer rounded-sm underline-offset-2 decoration-white/30 hover:underline focus:outline-none focus-visible:underline ${className}`}
    >
      {symbol}
    </span>
  );
}
