/**
 * Appliance referral waitlist — pure ranking + copy core (spec §3, First Edition = 100).
 *
 * Ranking: confirmed referrals DESC, then signup time ASC — inviting people is the only
 * way to move up, seniority breaks ties. Mirrored by the (future) `appliancePosition`
 * Cloud Function; this module is the single place the rule lives on the client.
 */
export interface WaitlistEntry {
  code: string;
  referrals: number;
  createdAt: number;
}

export function rankPosition(entries: WaitlistEntry[], code: string): number {
  const sorted = [...entries].sort(
    (a, b) => b.referrals - a.referrals || a.createdAt - b.createdAt
  );
  const i = sorted.findIndex((x) => x.code === code);
  return i === -1 ? -1 : i + 1;
}

/** 3-digit zero-pad below 1000 (the terminal counter reads `#042`), plain beyond. */
export function padPosition(n: number): string {
  return n < 1000 ? String(n).padStart(3, "0") : String(n);
}

/** The terminal-counter line. Beyond the edition the copy flips into the referral driver. */
export function positionCopy(position: number, editionSize: number): string {
  if (position <= editionSize) {
    return `you are #${padPosition(position)} of ${editionSize} — First Edition secured`;
  }
  return `#${position} — ${position - editionSize} spots from the First Edition`;
}

export function buildInviteLink(origin: string, code: string): string {
  return `${origin}/appliance?ref=${code}`;
}
