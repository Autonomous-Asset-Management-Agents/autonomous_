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
import PricingPreview from "./pages/PricingPreview";
import PricingMaster from "./pages/PricingMaster";

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
import {
    getEnginePort,
    getEngineKey,
    onEngineStatus,
    getEngineStatus,
    type EngineStatusPayload,
} from "@/lib/desktopBridge";
import { toast } from "sonner";

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

    // Desktop Engine Status Monitor (Progressive Loading — NOT a boot gate).
    //
    // The old health check fired checkHealth() immediately on render — 5-15s
    // before the engine had bound its port → Connection Refused → /offline.
    // Once on /offline there was no recovery path, so users were stuck.
    //
    // This replacement subscribes to the engine's status events (IPC push from
    // native-engine-manager via onEngineStatus) and only starts the /health
    // liveness poll AFTER the engine reports "running". During startup the
    // console stays usable; engine progress is shown via non-blocking toasts.
    // /offline is reserved for actual engine failures, never for "starting".
    useEffect(() => {
        const isElectron = navigator.userAgent.toLowerCase().includes("electron");
        if (!(isOssHost() && isElectron)) return;

        let engineReady = false;
        let healthInterval: ReturnType<typeof setInterval> | null = null;
        let disposed = false;

        // --- Liveness monitor (runs ONLY after engine reached "running") ---

        const startHealthPolling = () => {
            if (healthInterval || disposed) return;
            doHealthCheck();
            healthInterval = setInterval(doHealthCheck, 10_000);
        };

        const stopHealthPolling = () => {
            if (healthInterval) {
                clearInterval(healthInterval);
                healthInterval = null;
            }
        };

        const doHealthCheck = () => {
            const port = getEnginePort();
            if (!port) return;
            const key = getEngineKey();
            fetch(
                `http://127.0.0.1:${port}/health`,
                key ? { headers: { "X-Engine-Key": key } } : undefined,
            )
                .then((res) => {
                    // Recovery: engine is back while stuck on /offline → leave
                    if (res.ok && window.location.pathname === "/offline") {
                        window.location.href = "/console";
                    }
                    if (!res.ok && window.location.pathname !== "/offline") {
                        window.location.href = "/offline";
                    }
                })
                .catch(() => {
                    // Connection refused AFTER engine was running = crash
                    if (engineReady && window.location.pathname !== "/offline") {
                        window.location.href = "/offline";
                    }
                    // During startup (engineReady=false): swallow — expected
                });
        };

        // --- Engine status handler (IPC push from Electron main process) ---

        const handleStatus = (payload: EngineStatusPayload) => {
            if (disposed) return;

            switch (payload.status) {
                case "starting":
                    toast.loading("Engine starting…", {
                        id: "engine-status",
                        duration: Infinity,
                    });
                    break;

                case "running":
                    engineReady = true;
                    toast.success("Engine running", {
                        id: "engine-status",
                        duration: 2000,
                    });
                    startHealthPolling();
                    break;

                case "error":
                    engineReady = false;
                    stopHealthPolling();
                    toast.error(payload.detail || "Engine error", {
                        id: "engine-status",
                        duration: 5000,
                    });
                    if (window.location.pathname !== "/offline") {
                        window.location.href = "/offline";
                    }
                    break;

                case "stopped":
                    engineReady = false;
                    stopHealthPolling();
                    toast.warning("Engine stopped", {
                        id: "engine-status",
                        duration: 3000,
                    });
                    break;
            }
        };

        // 1. Subscribe to future engine status changes (IPC push)
        const unsubscribe = onEngineStatus(handleStatus);

        // 2. Check CURRENT status (engine might already be running)
        getEngineStatus().then((status) => {
            if (!disposed) handleStatus({ status });
        });

        return () => {
            disposed = true;
            unsubscribe();
            stopHealthPolling();
            toast.dismiss("engine-status");
        };
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
                <Route
                    path="/"
                    element={
                        isOssHost()
                            ? <Navigate to="/dashboard" replace />
                            : <IndexLandingB />
                    }
                />
                <Route path="/preview" element={<LandingViewE />} />
                <Route path="/pricing-preview" element={<PricingPreview />} />
                <Route path="/test-pricing" element={<PricingMaster />} />
                <Route path="/dashboard" element={<PrivateRoute><Dashboard /></PrivateRoute>} />
                <Route path="/console" element={<PrivateRoute><ConsoleApp /></PrivateRoute>} />
                <Route path="/console/app" element={<Navigate to="/console" replace />} />
                <Route path="/legal/imprint" element={<Legal kind="imprint" />} />
                <Route path="/legal/privacy" element={<Legal kind="privacy" />} />
                <Route path="/legal/risk-disclosure" element={<Legal kind="risk-disclosure" />} />
                <Route path="/legal/notice" element={<Legal kind="notice" />} />
                <Route path="/legal/terms" element={<Legal kind="terms" />} />
                <Route path="/legal/inducements" element={<Legal kind="inducements" />} />
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
