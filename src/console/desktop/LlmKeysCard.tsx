import { useState, useEffect } from "react";
import {
  saveSecret,
  saveSetupState,
  getSetupState,
  startEngine,
  stopEngine,
  isDesktop,
} from "@/lib/desktopBridge";
import { IconBolt } from "@/console/shared/Icons";

/**
 * #1705 — in-app LLM provider + Gemini API key management. The Gemini key was previously only
 * settable in the first-run SetupWizard; this card lets the operator (re-)enter it any time,
 * stored in the SAME keychain slot (GEMINI_API_KEY) + LLM_PROVIDER in setup.json. Mirrors
 * BrokerKeysCard: save to the keychain → offer an engine restart so the change applies.
 * Desktop-only (the cloud edition manages credentials via GCP Secret Manager).
 */
const INPUT =
  "mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30 disabled:opacity-40";

export function LlmKeysCard() {
  const [provider, setProvider] = useState<string | null>(null);
  const [geminiKey, setGeminiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    getSetupState()
      .then((s) => setProvider((s.LLM_PROVIDER as string | undefined) ?? null))
      .catch(() => {
        /* browser / no bridge — stays null */
      });
  }, []);

  // Desktop-only: the cloud edition manages credentials via GCP Secret Manager.
  if (!isDesktop()) return null;

  async function saveGemini() {
    if (!geminiKey.trim()) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const r = await saveSecret("GEMINI_API_KEY", geminiKey.trim());
      if (!r.ok) {
        setError("Validated key, but saving to the keychain failed.");
        return;
      }
      await saveSetupState({ LLM_PROVIDER: "gemini" });
      setProvider("gemini");
      setSaved(true);
      setGeminiKey("");
    } catch {
      setError("Something went wrong — please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function restart() {
    setRestarting(true);
    try {
      // Stop-then-start so a running engine re-reads the keychain; on an OFFLINE engine the stop
      // is a no-op and start brings it up with the new provider/key.
      await stopEngine();
      await startEngine();
      setSaved(false);
    } catch {
      setError("Couldn't restart the engine — use the engine controls above.");
    } finally {
      setRestarting(false);
    }
  }

  const providerLabel =
    provider === "gemini" ? "Gemini" : provider === "ollama" ? "Ollama (local)" : "—";

  return (
    <div className="surface p-6">
      <div className="flex items-start gap-4">
        <div
          className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0"
          style={{ background: "rgba(0,194,122,0.12)", border: "1px solid rgba(0,194,122,0.28)" }}
        >
          <IconBolt width={20} height={20} className="text-bull" />
        </div>
        <div className="flex-1">
          <div className="text-[14px] font-semibold text-white/92">LLM · Gemini API key</div>
          <div className="text-[12px] text-white/55 mt-1 max-w-md">
            Current provider: <span className="text-white/80">{providerLabel}</span>. Enter a Google
            Gemini API key to use Gemini for the analysis — stored in the OS keychain. Restart the
            engine to apply.
          </div>
          <label className="block mt-3 text-[11px] text-white/45">Gemini API key</label>
          <input
            type="password"
            className={INPUT}
            value={geminiKey}
            onChange={(e) => setGeminiKey(e.target.value)}
            placeholder="AIza…"
            disabled={busy}
          />
          {error && <div className="text-[11px] text-bear mt-2">{error}</div>}
          {saved && (
            <div className="text-[11px] text-white/55 mt-2">
              Saved.{" "}
              <button
                className="underline hover:text-white/80"
                onClick={() => void restart()}
                disabled={restarting}
              >
                {restarting ? "Restarting…" : "Restart engine to apply"}
              </button>
            </div>
          )}
          <div className="mt-3">
            <button
              className="btn"
              onClick={() => void saveGemini()}
              disabled={busy || !geminiKey.trim()}
            >
              {busy ? "Saving…" : "Save Gemini key"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
