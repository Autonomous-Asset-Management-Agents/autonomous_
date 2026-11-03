/**
 * #4999 (SIZE-UX 6a) — a stored value in the unit a person reads and types.
 *
 * Display only: the engine keeps its registry value, and every apply still goes through
 * the server clamp (core/trading_settings.py). The conversion is a decimal shift on the
 * text, not a float product: 0.07 * 100 is 7.000000000000001, and a wrong factor of 100
 * here would be a wrong setting inside the server's bounds.
 *
 * `fraction` is stored as a share (0.05 → 5%), `percent` already in percent (-6.0 → -6%),
 * `lossPercent` as a distance without its minus (7.0 → -7%).
 */

export type FieldUnit =
  | "fraction"
  | "percent"
  | "lossPercent"
  | "usd"
  | "minutes"
  | "tradingDays"
  | "days"
  | "hours"
  | "percentile";

const PLAIN_NUMBER = /^-?(\d+\.?\d*|\.\d+)$/;
const USD_GROUPS = /^\d{1,3}(,\d{3})+$/;

const isPercent = (unit: FieldUnit | undefined) =>
  unit === "fraction" || unit === "percent" || unit === "lossPercent";

function splitNumber(text: string) {
  const negative = text.startsWith("-");
  const [int, frac = ""] = text.replace("-", "").split(".");
  return { sign: negative ? "-" : "", int, frac };
}

/** "07.50" → "7.5", "-0.0" → "0". */
function tidy(text: string): string {
  const { sign, int, frac } = splitNumber(text);
  const whole = int.replace(/^0+(?=\d)/, "") || "0";
  const rest = frac.replace(/0+$/, "");
  const body = rest ? `${whole}.${rest}` : whole;
  return body === "0" ? body : sign + body;
}

const negate = (text: string) => (text.startsWith("-") ? text.slice(1) : `-${text}`);

/** Moves the decimal point `places` to the right (left when negative). */
function shiftPoint(text: string, places: number): string {
  const { sign, int, frac } = splitNumber(text);
  const digits = int + frac;
  const point = int.length + places;
  if (point <= 0) return `${sign}0.${"0".repeat(-point)}${digits}`;
  if (point >= digits.length) return sign + digits.padEnd(point, "0");
  return `${sign}${digits.slice(0, point)}.${digits.slice(point)}`;
}

/** Half away from zero, on the digits. */
function roundTo(text: string, decimals: number): string {
  const { sign, int, frac } = splitNumber(text);
  if (frac.length <= decimals) return text;
  const kept = BigInt(int + frac.slice(0, decimals)) + (frac[decimals] >= "5" ? 1n : 0n);
  const digits = kept.toString().padStart(decimals + 1, "0");
  const point = digits.length - decimals;
  return `${sign}${digits.slice(0, point)}.${digits.slice(point)}`;
}

const ordinalSuffix = (text: string): string => {
  const n = Number(text);
  if (n % 100 >= 11 && n % 100 <= 13) return "th";
  return { 1: "st", 2: "nd", 3: "rd" }[n % 10] ?? "th";
};

const counted = (text: string, noun: string) => `${text} ${noun}${text === "1" ? "" : "s"}`;

/** The stored value as the number in the input, without its unit. Unreadable text stays as it is. */
export function toDisplay(unit: FieldUnit | undefined, raw: string): string {
  if (!unit || !PLAIN_NUMBER.test(raw)) return raw;
  if (unit === "fraction") return tidy(shiftPoint(raw, 2));
  if (unit === "lossPercent") return tidy(negate(raw));
  return tidy(raw);
}

/** The typed text as the value to store, rounded to the registry's `decimals`; null if it
 *  is no number. Bounds stay with the caller: a positive loss distance comes back negative
 *  and fails there, like a positive loss tier. */
export function fromDisplay(
  unit: FieldUnit | undefined,
  text: string,
  decimals?: number,
): string | null {
  let typed = text.trim();
  if (isPercent(unit)) typed = typed.replace(/\s*%$/, "");
  if (unit === "usd") typed = typed.replace(/^\$\s*/, "");
  // In USD a comma in whole groups of three separates thousands. Only there: dropping
  // it elsewhere would turn 1000,5 into 10005.
  typed = unit === "usd" && USD_GROUPS.test(typed) ? typed.replace(/,/g, "") : typed.replace(",", ".");
  if (!PLAIN_NUMBER.test(typed)) return null;
  let stored = typed;
  if (unit === "fraction") stored = shiftPoint(typed, -2);
  if (unit === "lossPercent") stored = negate(typed);
  return tidy(decimals === undefined ? stored : roundTo(stored, decimals));
}

/** The stored value as it reads, with its unit: 5%, $10,000, 30 min. */
export function formatValue(unit: FieldUnit | undefined, raw: string): string {
  if (!unit || !PLAIN_NUMBER.test(raw)) return raw;
  const shown = toDisplay(unit, raw);
  if (isPercent(unit)) return `${shown}%`;
  switch (unit) {
    case "usd":
      return `$${shown.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}`;
    case "minutes":
      return `${shown} min`;
    case "tradingDays":
      return counted(shown, "trading day");
    case "days":
      return counted(shown, "day");
    case "hours":
      return `${shown} h`;
    case "percentile":
      return `${shown}${ordinalSuffix(shown)} percentile`;
    default:
      return shown;
  }
}

/** The sign beside the input, which holds the bare number. */
export function unitSymbol(unit: FieldUnit | undefined): string {
  if (!unit) return "";
  if (isPercent(unit)) return "%";
  return {
    usd: "$",
    minutes: "min",
    tradingDays: "trading days",
    days: "days",
    hours: "h",
    percentile: "percentile",
  }[unit];
}
