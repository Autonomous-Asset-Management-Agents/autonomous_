import { useEffect } from "react";
import { useStore } from "@/console/store/useStore";
import { LiveConsentModal } from "@/console/desktop/LiveConsentModal";

/**
 * Dev-only visual preview of the redesigned LiveConsentModal (#2794 — two-column, plain
 * standard surface, no scroll). The real modal is gated on desktop/engine state; here we seed
 * the store (paper + allowLive + the one-shot requestLiveConsent) so it opens in a plain browser:
 *
 *     npm run dev  →  http://localhost:8080/live-consent-preview
 *
 * No Electron shell and no engine are needed — the desktop-bridge probes no-op in the browser,
 * so the keys show the "stored" placeholder. This route is not shipped in the desktop app.
 */
export default function LiveConsentPreview() {
  useEffect(() => {
    // Senior on paper, live allowed → the modal's requestLiveConsent effect opens the confirm.
    useStore.setState({ paperTrading: true, allowLive: true });
    useStore.getState().setRequestLiveConsent(true);
  }, []);

  return (
    <div className="aaa-console" style={{ minHeight: "100vh", background: "#0b0b0c" }}>
      <LiveConsentModal />
    </div>
  );
}
