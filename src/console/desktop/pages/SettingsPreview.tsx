import { useEffect } from "react";
import { Settings } from "./Settings";
import { useStore } from "@/console/store/useStore";

/**
 * Dev-only visual preview of the console Settings page (mirrors OverviewPreview).
 * Renders the REAL <Settings/> — in dev mode the desktop-gated cards render too, and
 * engine-backed cards fail soft (no engine needed), so layout and copy can be reviewed.
 *
 *     npm run dev  →  http://localhost:8082/settings-preview   (or the port Vite prints)
 *
 * #3071: seeds inceptionDate so the "Performance baseline" row (Settings → Trading tab)
 * shows its inception hint + date-input lower bound. Not shipped in the desktop app.
 */
export default function SettingsPreview() {
  useEffect(() => {
    useStore.setState({
      desktopPage: "settings",
      paperTrading: false,
      brokerLabel: "Alpaca · Live",
      inceptionDate: "2026-07-24",
      baselineDate: null,
    });
  }, []);

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <Settings />
    </div>
  );
}
