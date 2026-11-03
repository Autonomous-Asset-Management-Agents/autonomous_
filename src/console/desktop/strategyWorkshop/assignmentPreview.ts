import type { TradingSettingMeta } from "@/lib/api";
import { ADVANCED_GROUPS } from "@/console/desktop/advancedTradingFields";
import type { SettingsApplyChange } from "@/console/desktop/SettingsApplyModal";

const fieldLabel = (key: string): string => {
  for (const g of ADVANCED_GROUPS) {
    const f = g.fields.find((x) => x.key === key);
    if (f) return f.label;
  }
  return key;
};

/** Preview diff of an assignment (#4630, #4953): the strategy values over the delivery state,
 *  against `current`, the state of the account being assigned. */
export function assignmentPreview(
  meta: readonly TradingSettingMeta[],
  current: Record<string, string>,
  werte: Record<string, string>,
): SettingsApplyChange[] {
  const rows: SettingsApplyChange[] = [];
  for (const m of meta) {
    const from = current[m.key];
    const to = werte[m.key] ?? m.default;
    if (from !== undefined && from !== to) {
      rows.push({ key: m.key, label: fieldLabel(m.key), from, to });
    }
  }
  return rows;
}
