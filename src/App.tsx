import { lazy, Suspense, useEffect } from "react";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from "react-router-dom";
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
// #2794: dev-only visual preview of the redesigned LiveConsentModal (see the component doc).
const LiveConsentPreview = lazy(() => import("@/console/desktop/LiveConsentPreview"));
// #overview-ia: dev-only visual preview of the restructured Overview page (see the component doc).
const OverviewPreview = lazy(() => import("@/console/desktop/pages/OverviewPreview"));
const SettingsPreview = lazy(() => import("@/console/desktop/pages/SettingsPreview"));
// /appliance - the hardware product page (spec 2026-08-14). GO-LIVE: publicly routed
// and linked from the landing (ticker + editions card) since the tower launch PR.
const Appliance = lazy(() => import("@/pages/Appliance"));
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
import { engineStatusEffect, ENGINE_STATUS_TOAST_ID } from "@/lib/engineStatusEffect";
import { shouldRouteOffline } from "@/lib/offlineRouting";
import { useStore } from "@/console/store/useStore";
import { suspenseFallbackClass } from "@/lib/suspenseFallback";
import { ModeSwitchOverlay } from "@/console/desktop/ModeSwitchOverlay";

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
    const navigate = useNavigate();
    const edit = useEditMode();
    const pageKey = derivePageKey(variant, location.pathname);

    // #8: a deliberate Paper↔Live switch in progress. Drives the calm
    // ModeSwitchOverlay (below) and — via useStore.getState() inside the health
    // poll — suppresses the /offline flash while the engine restarts.
    const modeSwitch = useStore((s) => s.modeSwitch);

    // Tracking Event absenden, sobald die Variante feststeht
    useEffect(() => {
        trackVariantImpression(variant);
    }, [variant]);

    // Desktop: the legacy marketing/dashboard "design" screens (landing, old /dashboard, variants,
    // pricing previews) must be UNREACHABLE in the electron app. Landing on one (e.g. via a stale
    // link) trapped the operator off the real console with no way back — even a restart kept the old
    // screen. In the desktop, bounce every legacy-design route to the real console (/console).
    useEffect(() => {
        const isElectron = navigator.userAgent.toLowerCase().includes("electron");
        if (!isElectron) return;
        const LEGACY = new Set([
            "/", "/dashboard", "/preview", "/pricing-preview", "/test-pricing", "/login",
        ]);
        if (LEGACY.has(location.pathname)) {
            navigate("/console", { replace: true });
        }
    }, [location.pathname, navigate]);

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
        // Debounce the /offline decision. A deliberate restart (live/paper switch) briefly refuses
        // connections, so the FIRST failed poll must NOT flash a "Backend offline" error — that raced
        // R8's "restarting" poll-stop and read an EXPECTED restart as a crash. Only sustained,
        // consecutive failures mean a genuinely dead engine; a single transient refusal is swallowed.
        let consecutiveFail = 0;
        const OFFLINE_AFTER = 3; // ×10s poll ≈ 30s unreachable before declaring the engine offline

        // #8: read live (not a stale closure) — true while a deliberate Paper↔Live switch
        // is in progress. During that expected engine restart we NEVER route to /offline;
        // the ModeSwitchOverlay owns the screen and clears itself once the engine serves
        // again (or its safety timeout fires), after which normal offline routing resumes.
        const inModeSwitch = () => useStore.getState().modeSwitch != null;

        // --- Liveness monitor (runs ONLY after engine reached "running") ---

        const startHealthPolling = () => {
            if (healthInterval || disposed) return;
            consecutiveFail = 0; // fresh start after "running" — never carry stale failures over
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
                    if (res.ok) {
                        consecutiveFail = 0;
                        // Recovery: engine is back while stuck on /offline → leave.
                        if (window.location.pathname === "/offline") window.location.href = "/console";
                        return;
                    }
                    // A non-OK response only declares offline after several consecutive polls —
                    // and never during a deliberate mode switch (#8, shouldRouteOffline).
                    consecutiveFail++;
                    if (
                        shouldRouteOffline({
                            consecutiveFail,
                            threshold: OFFLINE_AFTER,
                            alreadyOffline: window.location.pathname === "/offline",
                            modeSwitchActive: inModeSwitch(),
                        })
                    ) {
                        window.location.href = "/offline";
                    }
                })
                .catch(() => {
                    // Connection refused. A deliberate restart refuses briefly, so only a RUNNING
                    // engine that stays unreachable for OFFLINE_AFTER consecutive polls is a real crash
                    // — and, per #8, never while a deliberate mode switch is in progress.
                    consecutiveFail++;
                    if (
                        engineReady &&
                        shouldRouteOffline({
                            consecutiveFail,
                            threshold: OFFLINE_AFTER,
                            alreadyOffline: window.location.pathname === "/offline",
                            modeSwitchActive: inModeSwitch(),
                        })
                    ) {
                        window.location.href = "/offline";
                    }
                    // During startup / a brief restart / a deliberate switch: swallow — expected.
                });
        };

        // --- Engine status handler (IPC push from Electron main process) ---
        //
        // The status→effect policy lives in the pure engineStatusEffect helper
        // (unit-tested). This applier is a thin, generic bridge to the real side
        // effects. Crucially, a benign "restarting" (LSR R8 #2261) resolves to a
        // loading toast with navigateOffline=false — an EXPECTED restart never
        // reads as a Backend-Offline error; only a genuine "error" navigates.

        const handleStatus = (payload: EngineStatusPayload) => {
            if (disposed) return;

            const effect = engineStatusEffect(payload.status, payload.detail);

            if (effect.engineReady !== undefined) engineReady = effect.engineReady;

            if (effect.polling === "start") startHealthPolling();
            else if (effect.polling === "stop") stopHealthPolling();

            // Engine-status ERROR/WARNING toasts (bottom-right) were misleading — a benign or
            // transient engine state surfaced as a red "Engine error" that meant nothing to the
            // operator — so they are suppressed. The /offline navigation below still handles a
            // GENUINE crash; only the noisy toast is removed. Neutral loading/success stay.
            if (effect.toast && (effect.toast.kind === "loading" || effect.toast.kind === "success")) {
                const opts = { id: ENGINE_STATUS_TOAST_ID, duration: effect.toast.duration };
                if (effect.toast.kind === "loading") toast.loading(effect.toast.message, opts);
                else toast.success(effect.toast.message, opts);
            }

            // #8: even a hard-error status is suppressed while a deliberate switch is in
            // progress — a full-reload navigation would tear down the calm overlay. The
            // overlay's safety timeout is the honest fall-through; once it clears, the next
            // poll / status routes to /offline normally.
            if (effect.navigateOffline && !inModeSwitch() && window.location.pathname !== "/offline") {
                window.location.href = "/offline";
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
            toast.dismiss(ENGINE_STATUS_TOAST_ID);
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

    // #2804: console routes get a BLACK fallback regardless of the landing A/B variant — the
    // desktop shell loads from 127.0.0.1, which defaults the variant to "landing-b", and its
    // white fallback WAS the white startup flash while the lazy /console chunk loaded.
    const fallbackBg = suspenseFallbackClass(variant, location.pathname);

    return (
        <Suspense fallback={<div className={`min-h-screen ${fallbackBg}`} />}>
            {/* #8: calm full-screen switch experience (autonomous boot heartbeat → wordmark)
                during a deliberate Paper↔Live restart. Rendered above the router as a fixed
                overlay; while it's up, the health poll above suppresses /offline. */}
            {modeSwitch && <ModeSwitchOverlay />}
            <Routes>
                <Route path="/appliance" element={<Appliance />} />
                <Route path="/login" element={<VariantRouter variant={variant} v1Element={LoginV1} stitchElement={LoginStitch} />} />
                <Route path="/auth/alpaca/callback" element={<OAuthCallback />} />
                <Route
                    path="/"
                    element={
                        isOssHost()
                            ? <Navigate to="/console" replace />
                            : <IndexLandingB />
                    }
                />
                <Route path="/preview" element={<LandingViewE />} />
                <Route path="/pricing-preview" element={<PricingPreview />} />
                <Route path="/live-consent-preview" element={<LiveConsentPreview />} />
                <Route path="/overview-preview" element={<OverviewPreview />} />
                <Route path="/settings-preview" element={<SettingsPreview />} />
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
