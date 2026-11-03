import { useEffect } from "react";
import { Navigate } from "react-router-dom";
import { signOut } from "firebase/auth";
import { useAuthState } from "@/components/useAuthState";
import { auth } from "@/lib/firebase";
import { isDesktop } from "@/lib/desktopBridge";

interface PrivateRouteProps {
    children: React.ReactNode;
}

// ── Operator Allowlist ──────────────────────────────────────────────────────
// Only these exact email addresses may access the console.
// Add or remove entries here + redeploy to change access.
const ALLOWED_DOMAIN = ""; // disabled — use explicit list below
const EXTRA_ALLOWED_EMAILS: string[] = [
    "andreas@aaagents.de",
    "georg@aaagents.de",
];

function isEmailAllowed(email: string | null | undefined): boolean {
    if (!email) return false;
    if (email.endsWith(`@${ALLOWED_DOMAIN}`)) return true;
    return EXTRA_ALLOWED_EMAILS.includes(email);
}
// ───────────────────────────────────────────────────────────────────────────

/**
 * Guards a route — redirects to /login if not authenticated.
 * Also enforces the operator email allowlist: unauthorised Google accounts
 * are signed out immediately and redirected with an error query param.
 *
 * Desktop edition (#1050): the Electron build has no Firebase auth — secrets
 * live in the OS keychain and the engine runs locally — so the guard is a
 * no-op there and children render unconditionally. Without this bypass the
 * packaged app shows the Firebase login wall instead of the operator console.
 * The cloud build keeps the full auth + allowlist gate below.
 */
export const PrivateRoute = ({ children }: PrivateRouteProps) => {
    const { user, loading } = useAuthState();

    // Sign out accounts that are authenticated but not on the allowlist
    useEffect(() => {
        if (!isDesktop() && !loading && user && !isEmailAllowed(user.email)) {
            signOut(auth);
        }
    }, [user, loading]);

    // Desktop or Local Dev: no Firebase gate — render the console directly.
    const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
    if (isDesktop() || (!!import.meta.env.DEV && !isTest)) {
        return <>{children}</>;
    }

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            </div>
        );
    }

    if (!user) {
        return <Navigate to="/login" replace />;
    }

    // Still signed in but email not allowed → signed out above, show error
    if (!isEmailAllowed(user.email)) {
        return <Navigate to="/login?error=unauthorized" replace />;
    }

    return <>{children}</>;
};
