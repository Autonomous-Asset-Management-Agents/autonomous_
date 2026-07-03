import { lazy, Suspense, useEffect } from "react";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import NotFound from "./pages/NotFound";
import BackendOffline from "./pages/BackendOffline";
import OAuthCallback from "./pages/OAuthCallback";
import Legal from "./pages/Legal";
import Support from "./pages/Support";
import { PrivateRoute } from "@/components/PrivateRoute";

// /dashboard - Primary view for AAAgents.
const Dashboard = lazy(() => import("./pages/Dashboard"));

// /performance - public benchmark chart (portfolio vs S&P 500). Lazy so recharts
// doesn't ship with the landing hero bundle for visitors who never click in.
const Performance = lazy(() => import("./pages/Performance"));

// /live-demo - public live-demo of the agent on a fictitious PAPER demo depot (Epic #1582).
// Lazy so the snapshot page never ships with the landing hero bundle. Public (outside
// PrivateRoute), like /performance. Consumes the curated snapshot.json (#1587/#1588).
const LiveDemo = lazy(() =>
    import("./pages/LiveDemo").then((m) => ({ default: m.LiveDemo })),
);

// /console - the ported operator console (G3, #1050). Lazy so its dark console
// bundle never loads for landing/marketing visitors. G3-final: /console now
// renders this (the route swap is done); /console/app redirects here.
const ConsoleApp = lazy(() => import("@/console/ConsoleApp"));
import { ExplainabilityProvider } from "@/components/ExplainabilityProvider";
import { useDesignVariant, DesignVariant } from "./hooks/useDesignVariant";
import { useEditMode } from "@/lib/editor/useEditMode";
import { derivePageKey } from "@/lib/editor/pageKey";
import { EditorReadOnlyProvider } from "@/components/editor/EditorReadOnlyProvider";
import LandingViewE from "@/components/views/LandingViewE";

// --- Lazy Load Design Variants ---
const IndexV1 = lazy(() => import("@/components/variants/v1/pages/IndexV1"));
const LoginV1 = lazy(() => import("@/components/variants/v1/pages/LoginV1"));
const IndexStitch = lazy(() => import("@/components/variants/stitch-v1/pages/IndexStitch"));
const LoginStitch = lazy(() => import("@/components/variants/stitch-v1/pages/LoginStitch"));
const IndexLandingB = lazy(() => import("@/components/variants/landing-b/pages/IndexLandingB"));
const EditorRoot = lazy(() =>
    import("@/components/editor/EditorRoot").then((m) => ({ default: m.EditorRoot })),
);

const queryClient = new QueryClient();

// A/B Router Component
const VariantRouter = ({
    variant,
    v1Element: V1,
    stitchElement: Stitch
}: {
    variant: DesignVariant,
    v1Element: React.ElementType,
    stitchElement: React.ElementType
}) => {
    return variant === "stitch-v1" ? <Stitch /> : <V1 />;
};

import { trackVariantImpression } from "@/lib/firebase";

/** True when running on a self-hosted OSS install (localhost, 127.0.0.1, or
 *  any hostname that is not a known public AAAgents domain). */
const isOssHost = (): boolean => {
    if (typeof window === "undefined") return false;
    const h = window.location.hostname.toLowerCase();
    return h === "localhost" || h === "127.0.0.1" || h.startsWith("192.168.") || h.startsWith("10.");
};

export const AppContent = () => {
    const { variant } = useDesignVariant();
    const location = useLocation();
    const edit = useEditMode();
    const pageKey = derivePageKey(variant, location.pathname);

    // Tracking Event absenden, sobald die Variante feststeht
    useEffect(() => {
        trackVariantImpression(variant);
    }, [variant]);

    // Tauri Backend Health Check (runs independently of route changes)
    useEffect(() => {
        // @ts-expect-error Tauri global injection
        if (window.__TAURI__) {
            const apiUrl = import.meta.env.VITE_PUBLIC_API_URL || "http://127.0.0.1:8000";
            const checkHealth = () => {
                fetch(`${apiUrl}/api/v1/health`)
                    .then(res => {
                        if (!res.ok && window.location.pathname !== "/offline") {
                            window.location.href = "/offline";
                        }
                    })
                    .catch(() => {
                        if (window.location.pathname !== "/offline") {
                            window.location.href = "/offline";
                        }
                    });
            };

            // Initial check
            checkHealth();
            // Then every 10 seconds
            const interval = setInterval(checkHealth, 10000);
            return () => clearInterval(interval);
        }
    }, []);

    // landing-b is a public marketing variant - bypasses PrivateRoute
    const baseRoot = variant === "landing-b"
        ? <IndexLandingB />
        : <PrivateRoute><VariantRouter variant={variant} v1Element={IndexV1} stitchElement={IndexStitch} /></PrivateRoute>;

    // Only landing-b is editable in Phase 1. Wrap with editor chrome (active
    // mode) or with the read-only provider so published overrides apply for
    // every visitor.
    const rootElement = variant === "landing-b"
        ? (edit.active && edit.user
            ? <EditorRoot pageKey={pageKey} editorEmail={edit.user.email ?? ""}>{baseRoot}</EditorRoot>
            : <EditorReadOnlyProvider pageKey={pageKey}>{baseRoot}</EditorReadOnlyProvider>)
        : baseRoot;

    const fallbackBg = variant === "stitch-v1" ? "bg-[#F3F4F6]" : variant === "landing-b" ? "bg-white" : "bg-black";

    return (
        <Suspense fallback={<div className={`min-h-screen ${fallbackBg}`} />}>
            <Routes>
                <Route path="/login" element={<VariantRouter variant={variant} v1Element={LoginV1} stitchElement={LoginStitch} />} />
                <Route path="/auth/alpaca/callback" element={<OAuthCallback />} />
                {/* OSS self-hosted: 127.0.0.1 / localhost ÔåÆ go straight to the dashboard.
                    Public marketing domains (aaagents.de, *.web.app) ÔåÆ show landing page. */}
                <Route
                    path="/"
                    element={
                        isOssHost()
                            ? <Navigate to="/dashboard" replace />
                            : <IndexLandingB />
                    }
                />
                {/* Local dev preview: always shows the landing page regardless of host */}
                <Route path="/preview" element={<LandingViewE />} />
                <Route path="/dashboard" element={<PrivateRoute><Dashboard /></PrivateRoute>} />
                {/* G3-final: /console IS the ported operator console now (it was
                    a Dashboard alias during the page-by-page port). Internal
                    sidebar/?page= navigation; Decisions is the GAP2/HITL stub. */}
                <Route path="/console" element={<PrivateRoute><ConsoleApp /></PrivateRoute>} />
                {/* Legacy additive mount → redirect so any dev links survive. */}
                <Route path="/console/app" element={<Navigate to="/console" replace />} />
                <Route path="/legal/imprint" element={<Legal kind="imprint" />} />
                <Route path="/legal/privacy" element={<Legal kind="privacy" />} />
                <Route path="/legal/risk-disclosure" element={<Legal kind="risk-disclosure" />} />
                <Route path="/legal/notice" element={<Legal kind="notice" />} />
                <Route path="/support" element={<Support />} />
                <Route path="/performance" element={<Performance />} />
                <Route path="/live-demo" element={<LiveDemo />} />
                <Route path="/offline" element={<BackendOffline />} />
                <Route path="*" element={<NotFound />} />
            </Routes>
        </Suspense>
    );
};

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <ExplainabilityProvider>
        <BrowserRouter>
          <AppContent />
        </BrowserRouter>
      </ExplainabilityProvider>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
