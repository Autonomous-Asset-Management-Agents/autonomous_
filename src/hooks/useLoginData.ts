import { useState, useEffect } from "react";
import { signInWithEmailAndPassword, signInWithPopup } from "firebase/auth";
import { useNavigate, useSearchParams } from "react-router-dom";
import { auth, googleProvider } from "@/lib/firebase";
import { useAuthState } from "@/components/useAuthState";

export const useLoginData = () => {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [loading, setLoading] = useState(false);
    const [googleLoading, setGoogleLoading] = useState(false);
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const { user } = useAuthState();

    const urlError = searchParams.get("error");
    const [error, setError] = useState<string | null>(
        urlError === "unauthorized"
            ? "Dein Google-Konto ist nicht autorisiert. Bitte wende dich an den Administrator."
            : null
    );

    // Redirect if already logged in
    useEffect(() => {
        if (user) {
            navigate("/", { replace: true });
        }
    }, [user, navigate]);

    const handleGoogle = async () => {
        setError(null);
        setGoogleLoading(true);
        try {
            await signInWithPopup(auth, googleProvider);
            navigate("/", { replace: true });
        } catch (err: unknown) {
            const code = (err as { code?: string })?.code;
            if (code === "auth/popup-closed-by-user" || code === "auth/cancelled-popup-request") {
                // User closed popup — not an error
            } else if (code === "auth/popup-blocked") {
                setError("Popup wurde blockiert. Bitte Popups für diese Seite erlauben und erneut versuchen.");
            } else if (code === "auth/unauthorized-domain") {
                setError("Domain nicht autorisiert. Bitte Administrator kontaktieren.");
            } else {
                setError(`Google Sign-In fehlgeschlagen (${code ?? "unbekannt"}). Bitte erneut versuchen.`);
            }
        } finally {
            setGoogleLoading(false);
        }
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setLoading(true);
        try {
            await signInWithEmailAndPassword(auth, email, password);
            navigate("/", { replace: true });
        } catch {
            setError("Ungültige Anmeldedaten. Bitte erneut versuchen.");
        } finally {
            setLoading(false);
        }
    };

    return {
        email, setEmail,
        password, setPassword,
        loading,
        googleLoading,
        error,
        handleGoogle,
        handleSubmit,
        user
    };
};
