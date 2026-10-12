import { useState, useEffect } from "react";
import { onAuthStateChanged, User } from "firebase/auth";
import { auth } from "@/lib/firebase";

interface AuthState {
    user: User | null;
    loading: boolean;
}

/**
 * Subscribes to Firebase Auth state changes.
 * `loading` is true while Firebase resolves the initial session.
 */
export function useAuthState(): AuthState {
    const [user, setUser] = useState<User | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const unsubscribe = onAuthStateChanged(auth, (firebaseUser) => {
            setUser(firebaseUser);
            setLoading(false);
        });
        return () => unsubscribe();
    }, []);

    return { user, loading };
}
