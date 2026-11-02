// STW-7 (#4633): poll /benchmark-equity of every engine of the profile fleet (#4632).
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchBenchmarkEquityAt } from "@/lib/api";
import type { FleetEngine } from "@/lib/desktopBridge";
import { adaptEquity, type EquityView } from "@/console/live/equity";

const POLL_MS = 60_000;
const DEGRADED_MESSAGES = new Set(["internal_error", "fetch_failed"]);

export interface ComparedAccount {
  id: string;
  kind: "live" | "paper";
  port: number;
  label: string;
  state: "loading" | "ok" | "unavailable";
  view: EquityView | null;
}

/** Live first, then the paper accounts in the shell's slot order — the fixed order of Ablauf 6.
 *  Labels come from the i18n catalogue (#4892); the slot number stands beside the text. It comes
 *  from an id `paper<n>` (#4953: same name as on the slot card, also with a gap between the slots),
 *  otherwise from the position. */
function slotOrder(engines: readonly FleetEngine[], t: (key: string) => string): Omit<ComparedAccount, "state" | "view">[] {
  const liveLabel = t("console.strategyWorkshop.extra.liveAccount");
  const paperLabel = t("console.strategyWorkshop.extra.paperAccount");
  const live = engines.filter((e) => e.kind === "live").map((e) => ({ ...e, label: liveLabel }));
  const paper = engines.filter((e) => e.kind === "paper").map((e, i) => ({ ...e, label: `${paperLabel} ${/^paper(\d+)$/.exec(e.id)?.[1] ?? i + 1}` }));
  return [...live, ...paper].map(({ id, kind, port, label }) => ({ id, kind, port, label }));
}

type Reading = Pick<ComparedAccount, "state" | "view">;
const slotKey = (e: Pick<FleetEngine, "kind" | "id" | "port">) => `${e.kind}:${e.id}:${e.port}`;

export function useAccountComparison(engines: readonly FleetEngine[] | null): ComparedAccount[] {
  const { t, i18n } = useTranslation();
  const key = (engines ?? []).map(slotKey).join("|");
  // Keyed on the language, not on `t`: a new `t` per render would restart every poll.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const accounts = useMemo(() => slotOrder(engines ?? [], t), [key, i18n.resolvedLanguage]);
  const [readings, setReadings] = useState<Record<string, Reading>>({});

  useEffect(() => {
    let alive = true;
    const timers = new Map<string, ReturnType<typeof setTimeout>>();

    const poll = (acc: (typeof accounts)[number]) => {
      const loop = async () => {
        if (!alive) return;
        try {
          const resp = await fetchBenchmarkEquityAt(acc.port);
          if (!alive) return;
          if (resp?.message && DEGRADED_MESSAGES.has(resp.message)) {
            console.warn(`account comparison: ${acc.label} (port ${acc.port}) degraded (${resp.message}) — keeping the last good curve (#2588)`);
            setReadings((prev) => prev[slotKey(acc)]?.view ? prev : { ...prev, [slotKey(acc)]: { state: "unavailable", view: null } });
          } else {
            setReadings((prev) => ({ ...prev, [slotKey(acc)]: { state: "ok", view: adaptEquity(resp) } }));
          }
        } finally {
          if (alive) timers.set(slotKey(acc), setTimeout(loop, POLL_MS));
        }
      };
      void loop();
    };

    accounts.forEach(poll);
    return () => { alive = false; timers.forEach((t) => clearTimeout(t)); };
  }, [accounts]);

  return accounts.map((a) => ({ ...a, ...(readings[slotKey(a)] ?? { state: "loading", view: null }) }));
}
