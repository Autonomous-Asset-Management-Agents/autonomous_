/**
 * OAuthCallback page
 *
 * This page handles the redirect from localhost:8081/auth/alpaca/callback.
 * The ACTUAL OAuth callback logic (code exchange, token storage) runs server-side
 * in serve_public_api.py which immediately redirects the browser to /?success=true
 * or /?error=<reason>. This page only shows a brief loading state while that
 * server-side redirect is in flight, preventing the React NotFound page from
 * flashing if the /auth/alpaca/callback URL is ever rendered by the browser.
 */
import { useEffect } from "react";
import { motion } from "framer-motion";
import { RefreshCw } from "lucide-react";

const OAuthCallback = () => {
    useEffect(() => {
        // If somehow this page ever persists without a redirect (e.g. in dev),
        // automatically send the user to the dashboard root after 5 seconds.
        const timeout = setTimeout(() => {
            window.location.href = "/";
        }, 5000);
        return () => clearTimeout(timeout);
    }, []);

    return (
        <div className="min-h-screen bg-background flex flex-col items-center justify-center gap-4">
            <motion.div
                animate={{ rotate: 360 }}
                transition={{ repeat: Infinity, duration: 1.2, ease: "linear" }}
            >
                <RefreshCw className="w-8 h-8 text-primary" />
            </motion.div>
            <p className="text-muted-foreground text-sm">
                Verbindung wird hergestellt…
            </p>
        </div>
    );
};

export default OAuthCallback;
