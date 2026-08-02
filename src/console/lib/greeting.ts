// Time-aware dashboard greeting. Replaces the previously hardcoded
// "Good evening, Georg." on both the desktop Overview and the mobile Home.
export function timeGreeting(d: Date = new Date()): "Good morning" | "Good afternoon" | "Good evening" {
  const h = d.getHours();
  if (h >= 5 && h < 12) return "Good morning";
  if (h >= 12 && h < 18) return "Good afternoon";
  return "Good evening";
}

/** "Good morning, Alice." — gracefully OMITS the name when none is set
 *  ("Good morning.") rather than addressing the user as an impersonal "there". */
export function greeting(name?: string | null): string {
  const n = (name ?? "").trim();
  return n ? `${timeGreeting()}, ${n}.` : `${timeGreeting()}.`;
}
