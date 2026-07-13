import { fmtEUR, fmtNum } from "@/console/lib/format";
import { useStore } from "@/console/store/useStore";
import { useActivitiesPolling } from "@/console/live/useActivitiesPolling";
import { IconLightbulb } from "@/console/shared/Icons";

/**
 * Console Activities page (DESK-1): the broker fill history. Reads the store
 * (polled from /recent-trades — the last N *filled* Alpaca orders, newest
 * first) and renders one row per executed trade. These fills are the audit
 * trail behind the positions and the equity curve. Read-only — no order entry.
 */
export function Activities() {
  useActivitiesPolling();
  const activities = useStore((s) => s.activities);
  const truncated = useStore((s) => s.activitiesTruncated);
  const buys = activities.filter((a) => a.side === "buy").length;
  const sells = activities.length - buys;

  const fmtWhen = (d: Date | null) =>
    d
      ? d.toLocaleString("de-DE", {
          day: "2-digit",
          month: "2-digit",
          year: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "—";

  return (
    <div className="px-8 py-7 space-y-6 max-w-[1100px]">
      <div>
        <div className="eyebrow mb-2">Trade Activity</div>
        <div className="flex items-baseline gap-6 flex-wrap">
          <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">
            {activities.length} Executed Trades
          </h1>
          <span className="text-[13px] text-white/55">
            <span className="text-bull num">{buys}</span> buys ·{" "}
            <span className="text-bear num">{sells}</span> sells
          </span>
        </div>
        <p className="text-white/45 text-[13px] mt-1.5">
          Filled broker orders (newest first) — the executed fills behind the portfolio. Read-only.
        </p>
        {truncated && (
          <p className="text-[12px] text-amber-400/80 mt-1.5">
            Showing the most recent {activities.length} fills — history truncated at the page cap.
          </p>
        )}
      </div>

      <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
        <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
        <div className="text-[13.5px] text-white/70 leading-relaxed">
          <p>
            <span className="font-semibold text-white/90">Execution history.</span>{" "}
            Each row is a filled order from the connected broker (paper account): symbol, side,
            quantity, average fill price and time. This is the audit trail that produced the current
            positions and the equity curve.
          </p>
        </div>
      </div>

      {activities.length === 0 ? (
        <div className="surface px-8 py-14 text-center text-white/35 text-[13px]">
          No filled trades yet — or the engine is still warming up.
        </div>
      ) : (
        <div className="surface overflow-hidden">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-white/16">
                {["Symbol", "Side", "Qty", "Price", "Value", "Time"].map((h, i) => (
                  <th
                    key={h}
                    className={`px-4 py-3 font-semibold text-[10px] tracking-[0.12em] uppercase ${i >= 2 ? "text-right" : "text-left"}`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {activities.map((a) => (
                <tr key={a.id} className="border-t border-white/5 hover:bg-white/[0.025] transition-colors">
                  <td className="px-4 py-3.5 font-bold tracking-tight2 text-white/92">{a.symbol}</td>
                  <td className="px-4 py-3.5">
                    <span
                      className={`text-[11px] font-semibold uppercase tracking-wider ${a.side === "buy" ? "text-bull" : "text-bear"}`}
                    >
                      {a.side}
                    </span>
                  </td>
                  <td className="px-4 py-3.5 text-right num text-white/92">{fmtNum(a.qty, 0)}</td>
                  <td className="px-4 py-3.5 text-right num text-white/92">€{fmtNum(a.price)}</td>
                  <td className="px-4 py-3.5 text-right num text-white/92">{fmtEUR(a.qty * a.price)}</td>
                  <td className="px-4 py-3.5 text-right num text-white/45">{fmtWhen(a.filledAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
