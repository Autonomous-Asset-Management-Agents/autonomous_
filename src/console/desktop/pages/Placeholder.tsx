/**
 * Page placeholder for console pages not yet ported (G3, #1050). Each data
 * page lands in its own slice; until then the nav entry renders this honest
 * "coming next" panel instead of an empty or broken view.
 *
 * The Decisions page is a special case: its HITL approve/reject workflow needs
 * engine endpoints that don't exist on main (the platform-level GAP2 — no HITL
 * gate yet). It stays a stub here and gets its own compliance-track plan, not a
 * UI-epic side effect.
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
