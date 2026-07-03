import { motion } from "framer-motion";
import { useLoginData } from "@/hooks/useLoginData";

// Inline Google "G" logo — no extra dependency
const GoogleIcon = () => (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
);

const LoginV1 = () => {
    const {
        email, setEmail,
        password, setPassword,
        loading,
        googleLoading,
        error,
        handleGoogle,
        handleSubmit,
        user
    } = useLoginData();

    // Already logged in — redirect logic is handled inside the hook (useEffect)
    if (user) {
        return null;
    }

    return (
        <div className="min-h-screen flex items-center justify-center px-4" style={{ background: "#000" }}>
            <div className="grain" />

            <motion.div
                initial={{ opacity: 0, y: 24 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
                className="relative w-full max-w-sm"
            >
                {/* Logo */}
                <motion.div
                    className="text-center mb-10"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 0.1 }}
                >
                    <h1 style={{ fontSize: 28, fontWeight: 700, letterSpacing: "-0.02em", marginBottom: 4 }}>
                        <strong style={{ color: "rgba(255,255,255,0.85)" }}>AAA</strong>
                        <span style={{ color: "rgba(255,255,255,0.55)" }}>gents</span>
                    </h1>
                    <p style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", color: "rgba(255,255,255,0.3)" }}>
                        Operator Console
                    </p>
                </motion.div>

                {/* Card */}
                <div className="surface-card p-8">
                    <h2 style={{ fontSize: 17, fontWeight: 600, letterSpacing: "-0.01em", marginBottom: 24, textAlign: "center", color: "rgba(255,255,255,0.85)" }}>Sign In</h2>

                    {/* Google Sign-In — primary method */}
                    <button
                        id="google-signin-btn"
                        type="button"
                        onClick={handleGoogle}
                        disabled={googleLoading}
                        className="w-full flex items-center justify-center gap-3 mb-5"
                        style={{
                            padding: "11px 0", borderRadius: 10, fontSize: 13, fontWeight: 500,
                            background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)",
                            color: "rgba(255,255,255,0.85)", cursor: googleLoading ? "not-allowed" : "pointer",
                            transition: "all 0.2s", opacity: googleLoading ? 0.5 : 1,
                        }}
                    >
                        {googleLoading ? (
                            <span className="w-4 h-4 border-2 border-foreground border-t-transparent rounded-full animate-spin" />
                        ) : (
                            <GoogleIcon />
                        )}
                        {googleLoading ? "Signing in…" : "Sign in with Google"}
                    </button>

                    {/* Divider */}
                    <div className="flex items-center gap-3 mb-5">
                        <div className="flex-1 h-px bg-border/40" />
                        <span className="text-xs text-muted-foreground">or</span>
                        <div className="flex-1 h-px bg-border/40" />
                    </div>

                    {/* Email / Password — secondary (requires Email/Password provider enabled) */}
                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div>
                            <label
                                htmlFor="email"
                                className="block text-xs uppercase tracking-wider text-muted-foreground mb-2"
                            >
                                Email
                            </label>
                            <input
                                id="email"
                                type="email"
                                autoComplete="email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                style={{ width: "100%", padding: "10px 14px", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 10, color: "rgba(255,255,255,0.85)", fontSize: 14, outline: "none" }}
                                placeholder="operator@aaagents.de"
                            />
                        </div>

                        <div>
                            <label
                                htmlFor="password"
                                className="block text-xs uppercase tracking-wider text-muted-foreground mb-2"
                            >
                                Password
                            </label>
                            <input
                                id="password"
                                type="password"
                                autoComplete="current-password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                style={{ width: "100%", padding: "10px 14px", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 10, color: "rgba(255,255,255,0.85)", fontSize: 14, outline: "none" }}
                                placeholder="••••••••"
                            />
                        </div>

                        {error && (
                            <motion.p
                                initial={{ opacity: 0, y: -4 }}
                                animate={{ opacity: 1, y: 0 }}
                                className="text-destructive text-xs text-center pt-1"
                            >
                                {error}
                            </motion.p>
                        )}

                        <button
                            type="submit"
                            disabled={loading || !email || !password}
                            className="w-full mt-2"
                            style={{
                                padding: "11px 0", borderRadius: 10, fontSize: 13, fontWeight: 600,
                                background: "rgba(212,168,83,0.12)", border: "1px solid rgba(212,168,83,0.3)",
                                color: "#d4a853", cursor: loading || !email || !password ? "not-allowed" : "pointer",
                                transition: "all 0.2s", opacity: loading || !email || !password ? 0.4 : 1,
                            }}
                        >
                            {loading ? (
                                <span className="flex items-center justify-center gap-2">
                                    <span className="w-4 h-4 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin" />
                                    Signing in…
                                </span>
                            ) : (
                                "Sign In with Email"
                            )}
                        </button>
                    </form>

                    <p className="text-center text-xs text-muted-foreground mt-6">
                        Nur für autorisierte Operatoren
                    </p>
                </div>
            </motion.div>
        </div>
    );
};

export default LoginV1;
