import { fmtQty } from "@/console/lib/format";
import { useMoney } from "@/console/lib/useMoney";
import { useStore } from "@/console/store/useStore";
import { useActivitiesPolling } from "@/console/live/useActivitiesPolling";
import { useOpenOrdersPolling } from "@/console/live/useOpenOrdersPolling";
import { IconLightbulb } from "@/console/shared/Icons";
import { cancelOrder as realCancelOrder } from "@/lib/api";
import { CancelButton, cancelOpenOrder } from "@/console/desktop/orderCancel";

/**
 * Console "Orders" page (#2137). Two sections that share the console's standard table look: the
 * operator's currently OPEN / working orders (the "Order book", polled fast from /open-orders) and
 * the executed fill history below (polled from /activities). Each open order carries a HITL Cancel
 * control (EU AI Act Art. 14) — ALWAYS available, independent of any autonomous mode. The nav id
 * stays "activities" for deep-link/back-compat; the visible label + heading are "Orders".
 */
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

const thBase = "px-4 py-3 font-semibold text-[10px] tracking-[0.12em] uppercase";

export function Activities({ cancelOrder = realCancelOrder }: { cancelOrder?: typeof realCancelOrder } = {}) {
  useActivitiesPolling();
  useOpenOrdersPolling();
  const activities = useStore((s) => s.activities);
  const truncated = useStore((s) => s.activitiesTruncated);
  const openOrders = useStore((s) => s.openOrders);
  const { money } = useMoney();
  const buys = activities.filter((a) => a.side === "buy").length;
  const sells = activities.length - buys;

  const limitOf = (limitPrice: number | null, stopPrice: number | null) =>
    limitPrice != null ? money(limitPrice) : stopPrice != null ? `stop ${money(stopPrice)}` : "—";

  return (
    <div className="px-8 py-7 space-y-6 max-w-[1100px]">
      <div>
        <div className="eyebrow mb-2">Orders</div>
        <div className="flex items-baseline gap-6 flex-wrap">
          <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">Orders</h1>
          <span className="text-[13px] text-white/55">
            <span className="num text-white/80">{openOrders.length}</span> open ·{" "}
            <span className="num text-white/80">{activities.length}</span> filled
          </span>
        </div>
        <p className="text-white/45 text-[13px] mt-1.5">
          Your live working orders and the executed fill history. Read-only, except the Cancel control.
        </p>
      </div>

      {/* Explanation — directly under the heading, consistent with the other console pages. */}
      <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
        <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
        <div className="text-[13.5px] text-white/70 leading-relaxed">
          <p>
            <span className="font-semibold text-white/90">Orders.</span> The Order book lists your live
            working orders (submitted, not yet filled or cancelled); each one can be cancelled by hand at
            any time. The Order history below is the executed fills from the connected broker (paper
            account).
          </p>
        </div>
      </div>

      {/* Order book — the operator's live working orders. */}
      <div>
        <div className="flex items-baseline gap-4 mb-2.5">
          <h2 className="text-[15px] font-semibold text-white/85">Order book</h2>
          <span className="text-[12px] text-white/45">
            {openOrders.length} working {openOrders.length === 1 ? "order" : "orders"}
          </span>
        </div>
        {openOrders.length === 0 ? (
          <div className="surface px-8 py-14 text-center text-white/35 text-[13px]">
            No open orders — nothing working at the broker right now.
          </div>
        ) : (
          <div className="surface overflow-hidden">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-white/16">
                  <th className={`${thBase} text-left`}>Symbol</th>
                  <th className={`${thBase} text-left`}>Side</th>
                  <th className={`${thBase} text-right`}>Qty</th>
                  <th className={`${thBase} text-left`}>Type</th>
                  <th className={`${thBase} text-right`}>Limit</th>
                  <th className={`${thBase} text-left`}>Status</th>
                  <th className={`${thBase} text-right`}>Submitted</th>
                  <th className={`${thBase} text-right`}></th>
                </tr>
              </thead>
              <tbody>
                {openOrders.map((o) => (
                  <tr key={o.id} className="border-t border-white/5 hover:bg-white/[0.025] transition-colors">
                    <td className="px-4 py-3.5 font-bold tracking-tight2 text-white/92">{o.symbol}</td>
                    <td className="px-4 py-3.5">
                      <span
                        className={`text-[11px] font-semibold uppercase tracking-wider ${o.side === "buy" ? "text-bull" : "text-bear"}`}
                      >
                        {o.side}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-right num text-white/92">{fmtQty(o.qty)}</td>
                    <td className="px-4 py-3.5 text-white/55">{o.type || "—"}</td>
                    <td className="px-4 py-3.5 text-right num text-white/92">{limitOf(o.limitPrice, o.stopPrice)}</td>
                    <td className="px-4 py-3.5 text-white/55">{o.status || "open"}</td>
                    <td className="px-4 py-3.5 text-right num text-white/45">{fmtWhen(o.submittedAt)}</td>
                    <td className="px-4 py-3.5 text-right">
                      <CancelButton onCancel={() => cancelOpenOrder(o.id, cancelOrder)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Order history (fills). */}
      <div>
        <div className="flex items-baseline gap-4 mb-2.5">
          <h2 className="text-[15px] font-semibold text-white/85">Order history</h2>
          <span className="text-[12px] text-white/45">
            <span className="text-bull num">{buys}</span> buys ·{" "}
            <span className="text-bear num">{sells}</span> sells
          </span>
        </div>
        {truncated && (
          <p className="text-[12px] text-white/70 mb-2">
            Showing the most recent {activities.length} fills — history truncated at the page cap.
          </p>
        )}
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
                    <th key={h} className={`${thBase} ${i >= 2 ? "text-right" : "text-left"}`}>
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
                    <td className="px-4 py-3.5 text-right num text-white/92">{fmtQty(a.qty)}</td>
                    <td className="px-4 py-3.5 text-right num text-white/92">{money(a.price)}</td>
                    <td className="px-4 py-3.5 text-right num text-white/92">{money(a.qty * a.price)}</td>
                    <td className="px-4 py-3.5 text-right num text-white/45">{fmtWhen(a.filledAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
