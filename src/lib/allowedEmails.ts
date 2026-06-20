/**
 * Single source of truth for the operator allowlist.
 * Used by Login.tsx, PrivateRoute.tsx, and the dev-unlock flag on the public site.
 */
export const ALLOWED_EMAILS: readonly string[] = [
    "andreas@aaagents.de",
    "georg@aaagents.de",
];

export function isEmailAllowed(email: string | null | undefined): boolean {
    if (!email) return false;
    return ALLOWED_EMAILS.includes(email);
}
