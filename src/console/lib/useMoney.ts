// src/console/lib/useMoney.ts — PR-B: reactive money formatting bound to the REAL
// account currency (store `accountCurrency`, from Alpaca account.currency), so the
// console shows the correct symbol/label instead of a hardcoded €.
import { useStore } from "@/console/store/useStore";
import { fmtMoney, currencySymbol } from "./format";

export function useMoney(): {
  /** Format a value in the account currency — drop-in for the old `fmtEUR`. */
  money: (n: number, opts?: { compact?: boolean; sign?: boolean }) => string;
  /** The currency symbol (e.g. "$"). */
  sym: string;
  /** The ISO code for labels (e.g. "USD"). */
  iso: string;
} {
  const iso = useStore((s) => s.accountCurrency);
  const sym = currencySymbol(iso);
  const money = (n: number, opts?: { compact?: boolean; sign?: boolean }) =>
    fmtMoney(n, sym, opts);
  return { money, sym, iso };
}
