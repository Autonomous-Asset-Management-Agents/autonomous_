import { useEffect, useReducer, useState } from "react";
import { isDesktop, getAppVersion, getUpdateStatus, type UpdateStatus } from "@/lib/desktopBridge";

/**
 * #2077 Increment 3 — non-blocking update banner at the top of the console (notify-only).
 *
 * Shows only when the shell found a newer version (status polled from the Main-Process, which
 * checks on start + every 6h) AND the user hasn't dismissed THAT version. Dismiss is per-version
 * (localStorage), so a later version surfaces again. The Download link opens the download page in
 * the OS browser; the banner itself never downloads/installs. Desktop-only. Neutral surface —
 * green is used only on the status dot, the version, and the Download button.
 */
const dismissKey = (v: string) => `aaa.updateDismissed.${v}`;

export function UpdateBanner() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [installed, setInstalled] = useState<string | null>(null);
  // Bumped on dismiss to force a re-render that re-reads the localStorage marker below.
  const [, forceRerender] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    if (!isDesktop()) return;
    void getUpdateStatus().then(setStatus);
    void getAppVersion().then(setInstalled);
    const id = setInterval(() => void getUpdateStatus().then(setStatus), 30 * 60 * 1000);
    return () => clearInterval(id);
  }, []);

  const latest = status?.latestVersion ?? null;
  // Derived in render (not an effect): dismiss is a per-version localStorage marker.
  const dismissed = latest != null && localStorage.getItem(dismissKey(latest)) === "1";

  if (!isDesktop() || !status?.updateAvailable || !latest || dismissed) return null;

  function dismiss() {
    if (latest) localStorage.setItem(dismissKey(latest), "1");
    forceRerender();
  }

  return (
    <div
      className="flex items-center gap-3.5 px-4 py-3 m-3 rounded-xl border border-white/[0.08]"
      style={{ background: "rgba(255,255,255,0.03)" }}
    >
      <span
        className="w-2 h-2 rounded-full shrink-0"
        style={{ background: "#00c27a", boxShadow: "0 0 0 4px rgba(0,194,122,0.18)" }}
        aria-hidden
      />
      <div className="min-w-0">
        <div className="text-[12.5px] font-semibold text-white/90">
          Version <span style={{ color: "#00c27a" }}>{latest}</span> available
          {installed && <span className="text-white/30 font-normal"> · installed {installed}</span>}
        </div>
        {status.changelog && (
          <div className="text-[11.5px] text-white/55 mt-0.5 truncate">{status.changelog}</div>
        )}
      </div>
      <div className="flex-1" />
      {status.downloadUrl && (
        <a
          href={status.downloadUrl}
          target="_blank"
          rel="noreferrer"
          className="shrink-0 text-[11.5px] font-semibold rounded-lg px-3.5 py-1.5"
          style={{ background: "#00c27a", color: "#04140d" }}
        >
          Download
        </a>
      )}
      <button
        onClick={dismiss}
        aria-label="Dismiss for this version"
        className="shrink-0 text-white/30 hover:text-white/80 text-[15px] leading-none px-1.5 py-0.5"
      >
        ✕
      </button>
    </div>
  );
}
