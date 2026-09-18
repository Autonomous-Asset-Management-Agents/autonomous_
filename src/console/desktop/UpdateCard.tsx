import { useEffect, useState } from "react";
import {
  isDesktop,
  getAppVersion,
  getUpdateStatus,
  checkForUpdatesNow,
  type UpdateStatus,
} from "@/lib/desktopBridge";

/**
 * #2077 Increment 3 — Settings → System "Update" card (notify-only).
 *
 * Shows the installed version and, when the shell has found a newer one (version.json vs
 * app.getVersion()), the latest version + a Download link that opens the download page in the
 * OS browser (main.cjs setWindowOpenHandler → shell.openExternal). It NEVER downloads/installs.
 * In the up-to-date state it offers "Check for updates"; when an update is already surfaced the
 * check just ran, so that button is intentionally omitted. Desktop-only (null in the browser).
 */
export function UpdateCard() {
  const [version, setVersion] = useState<string | null>(null);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    void getAppVersion().then(setVersion);
    void getUpdateStatus().then(setStatus);
  }, []);

  if (!isDesktop()) return null;

  async function handleCheck() {
    setChecking(true);
    try {
      setStatus(await checkForUpdatesNow());
    } finally {
      setChecking(false);
    }
  }

  const available = status?.updateAvailable === true;

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-4">Update</div>

      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-white/35">Installed version</div>
          <div className="num text-[12px] text-white/80 mt-0.5">{version ?? "—"}</div>
        </div>
        {available ? (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-white/60">
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: "#00c27a" }} aria-hidden />
            Update available
          </span>
        ) : (
          <span className="text-[11px] text-white/55">
            <span style={{ color: "#00c27a" }}>✓</span> Up to date
          </span>
        )}
      </div>

      <div className="mt-3.5 pt-3.5 border-t border-white/[0.08]">
        {available ? (
          <>
            <div className="text-[10px] uppercase tracking-wider text-white/35">Latest version</div>
            <div className="num text-[12px] mt-0.5" style={{ color: "#00c27a" }}>
              {status?.latestVersion}
            </div>
            {status?.changelog && (
              <div className="text-[11.5px] text-white/55 mt-2 leading-relaxed">{status.changelog}</div>
            )}
            {status?.downloadUrl && (
              <div className="mt-3">
                <a
                  href={status.downloadUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-block text-[11.5px] font-semibold rounded-lg px-3.5 py-1.5"
                  style={{ background: "#00c27a", color: "#04140d" }}
                >
                  Download
                </a>
              </div>
            )}
          </>
        ) : (
          <>
            <div className="text-[11.5px] text-white/55">You&apos;re on the latest version.</div>
            <div className="mt-3">
              <button className="btn" onClick={() => void handleCheck()} disabled={checking}>
                {checking ? "Checking…" : "Check for updates"}
              </button>
            </div>
          </>
        )}
      </div>

      <div className="text-[10px] text-white/30 mt-2.5">
        Checked automatically on start and every 6 hours.
      </div>
    </div>
  );
}
