import { useEffect, useState } from "react";
import { onAuthStateChanged, type User } from "firebase/auth";
import { auth } from "@/lib/firebase";
import { isEmailAllowed } from "@/lib/allowedEmails";

export interface EditModeState {
    /** True only when ?edit=1 AND user is signed in AND email is allowlisted. */
    active: boolean;
    /** True when ?edit=1 is in the URL but the gate hasn't passed (e.g. not logged in). */
    requested: boolean;
    user: User | null;
}

function readEditFlag(): boolean {
    if (typeof window === "undefined") return false;
    return new URLSearchParams(window.location.search).get("edit") === "1";
}

/**
 * Gate for the visual editor. Active iff the URL flag is set, a Firebase user
 * is signed in, and that user's email is in ALLOWED_EMAILS.
 */
export function useEditMode(): EditModeState {
    const [user, setUser] = useState<User | null>(auth.currentUser);
    const [requested] = useState<boolean>(readEditFlag);

    useEffect(() => {
        if (!requested) return;
        return onAuthStateChanged(auth, setUser);
    }, [requested]);

    const active = requested && !!user && isEmailAllowed(user.email);
    return { active, requested, user };
}
