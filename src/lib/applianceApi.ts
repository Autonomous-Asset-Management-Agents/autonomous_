/**
 * Appliance waitlist API seam — talks to the `appliance` Cloud Function behind the
 * /api/appliance/* hosting rewrite (tower launch backend). The page consumes ONLY
 * this seam; shapes are the contract with functions/src/index.mjs.
 */
export interface JoinResult {
  status: "pending" | "already";
}

export interface PositionResult {
  position: number;
  total: number;
  referrals: number;
  editionSize: number;
}

export async function joinWaitlist(email: string, ref?: string): Promise<JoinResult> {
  const res = await fetch("/api/appliance/join", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, ref: ref ?? null }),
  });
  if (!res.ok) throw new Error(`join failed: ${res.status}`);
  return (await res.json()) as JoinResult;
}

export async function fetchPosition(code: string): Promise<PositionResult> {
  const res = await fetch(`/api/appliance/position?code=${encodeURIComponent(code)}`);
  if (!res.ok) throw new Error(`position failed: ${res.status}`);
  return (await res.json()) as PositionResult;
}
