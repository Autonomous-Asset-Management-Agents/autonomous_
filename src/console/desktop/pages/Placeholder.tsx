/**
 * Page placeholder for console pages not yet ported (G3, #1050). Each data
 * page lands in its own slice; until then the nav entry renders this honest
 * "coming next" panel instead of an empty or broken view.
 *
 * Historical note: the Decisions page was long stubbed here because the HITL
 * approve/reject endpoints didn't exist (GAP2). They shipped with the PR-0a-ii
 * series (/api/hitl/pending|approve|reject) and the Orders page now surfaces the
 * approval queue (#2660) — this placeholder no longer implies a missing gate.
 */
export function Placeholder({ title, note }: { title: string; note?: string }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center px-8">
      <div className="eyebrow mb-2">{title}</div>
      <div className="text-[14px] text-white/70">Coming in the next console slice.</div>
      {note ? (
        <div className="text-[12px] text-white/35 mt-2 max-w-md leading-relaxed">{note}</div>
      ) : null}
    </div>
  );
}
