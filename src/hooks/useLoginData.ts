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
            ? "Your Google account is not authorized. Please contact the administrator."
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
                setError("Popup was blocked. Please allow popups for this site and try again.");
            } else if (code === "auth/unauthorized-domain") {
                setError("Domain not authorized. Please contact the administrator.");
            } else {
                setError(`Google Sign-In failed (${code ?? "unknown"}). Please try again.`);
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
            setError("Invalid credentials. Please try again.");
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
