// src/console/live/baselineLabel.ts — pure helper: the honest KPI-card hint (#3071).
// A user-chosen performance baseline must ALWAYS be visible next to the number it
// re-anchors (fair presentation): with a baseline the hint names the date; only
// WITHOUT one may the card say "since inception". English is the console's language.

export function baselineHint(baselineDate: string | null | undefined): string {
  if (!baselineDate) return "since inception";
  const d = new Date(`${String(baselineDate).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return "since inception";
  return `since ${d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  })}`;
}
