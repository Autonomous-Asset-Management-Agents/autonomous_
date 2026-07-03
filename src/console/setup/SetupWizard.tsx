import { useState } from "react";
import {
  validateAlpaca,
  saveSecret,
  saveSetupState,
  startEngine,
  provisionOllama,
} from "@/lib/desktopBridge";

/**
 * First-run setup wizard (G4, #1050). G4-2 ships the flow: welcome → Alpaca
 * (live-validated, stored in the OS keychain) → LLM choice → launch. The full
 * Ollama provisioning (download + health) lands in G4-3; here "local" just
 * records the provider. Rendered by ConsoleApp (desktop only) when the keychain
 * has no secrets. The step machine is the gate: Finish is only reachable after
 * Alpaca validates AND an LLM is chosen.
 */
type Step = "welcome" | "alpaca" | "llm" | "finish";
type Provider = "ollama" | "gemini";

const INPUT =
  "mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30";
const PRIMARY =
  "w-full text-[13px] font-semibold px-4 py-2.5 rounded-lg border border-[#00c27a]/30 bg-[#00c27a]/12 text-[#7ce7b3] disabled:opacity-40 disabled:cursor-not-allowed hover:bg-[#00c27a]/20 transition-colors";

export function SetupWizard({
  onComplete,
  onSkip,
}: {
  onComplete: () => void;
  onSkip?: () => void;
}) {
  const [step, setStep] = useState<Step>("welcome");
  const [name, setName] = useState("");
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  const [provider, setProvider] = useState<Provider | null>(null);
  const [geminiKey, setGeminiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ollamaMsg, setOllamaMsg] = useState<string | null>(null);
  // C-B2: when auto-install of the local AI runtime fails, surface a clickable download link.
  const [needsOllamaInstall, setNeedsOllamaInstall] = useState(false);

  async function submitWelcome() {
    setBusy(true);
    setError(null);
    try {
      await saveSetupState({ name: name.trim() });
      setStep("alpaca");
    } catch {
      setError("Couldn't save — please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function submitAlpaca() {
    setBusy(true);
    setError(null);
    try {
      const res = await validateAlpaca(keyId.trim(), secret.trim());
      if (!res.ok) {
        setError(
          res.status === 0
            ? "Couldn't reach Alpaca — check your connection."
            : `Alpaca rejected these keys (HTTP ${res.status}).`,
        );
        return;
      }
      const a = await saveSecret("ALPACA_API_KEY", keyId.trim());
      const b = await saveSecret("ALPACA_SECRET_KEY", secret.trim());
      if (!a.ok || !b.ok) {
        setError("Validated, but saving to the keychain failed.");
        return;
      }
      setStep("llm");
    } finally {
      setBusy(false);
    }
  }

  async function submitLlm() {
    if (!provider) return;
    setBusy(true);
    setError(null);
    setNeedsOllamaInstall(false);
    try {
      if (provider === "ollama") {
        setOllamaMsg("Setting up local AI — the first run downloads a model and can take a few minutes…");
        const res = await provisionOllama((p) =>
          setOllamaMsg(p.percent != null ? `Downloading model… ${p.percent}%` : p.status || "Working…"),
        );
        if (res.needsManual) {
          setError(res.error || "Couldn't auto-install the local AI runtime.");
          setNeedsOllamaInstall(true);
          return;
        }
        if (!res.ok) {
          setError(res.error || "Local AI setup failed.");
          return;
        }
        await saveSetupState({
          LLM_PROVIDER: "ollama",
          LOCAL_LLM_MODEL: res.model,
          OLLAMA_BASE_URL: res.baseUrl,
        });
      } else {
        const r = await saveSecret("GEMINI_API_KEY", geminiKey.trim());
        if (!r.ok) {
          setError("Saving the Gemini key failed.");
          return;
        }
        await saveSetupState({ LLM_PROVIDER: "gemini" });
      }
      setStep("finish");
    } finally {
      setBusy(false);
      setOllamaMsg(null);
    }
  }

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      await startEngine();
      onComplete();
    } catch {
      setError("Couldn't start the engine — please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="aaa-console flex h-screen w-screen items-center justify-center bg-black px-6">
      <div className="w-full max-w-md surface p-7 space-y-5">
        {error && (
          <div role="alert" className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2">
            {error}
          </div>
        )}
        {needsOllamaInstall && (
          <p className="text-[11.5px] text-white/55 -mt-1">
            Install it from{" "}
            <a href="https://ollama.com/download" target="_blank" rel="noreferrer" className="text-[#7ce7b3] underline">
              ollama.com/download
            </a>
            , then click <strong className="text-white/80">Continue</strong> to retry.
          </p>
        )}

        {step === "welcome" && (
          <>
            <div>
              <div className="eyebrow mb-1">Welcome</div>
              <h1 className="text-[22px] font-bold tracking-tight2 text-white/92">Set up autonomous_</h1>
              <p className="text-white/55 text-[12.5px] mt-2">
                A few steps to connect your account. Desktop runs in paper-trading mode.
              </p>
            </div>
            <label className="block text-[12px] text-white/55">
              Your name
              <input aria-label="name" value={name} onChange={(e) => setName(e.target.value)} className={INPUT} placeholder="e.g. Georg" />
            </label>
            <button onClick={() => void submitWelcome()} disabled={busy || !name.trim()} className={PRIMARY}>
              {busy ? "Saving…" : "Continue"}
            </button>
            {onSkip ? (
              <button
                onClick={onSkip}
                className="w-full text-[12px] text-white/45 hover:text-white/70 transition-colors"
              >
                Skip for now — explore in demo mode
              </button>
            ) : null}
          </>
        )}

        {step === "alpaca" && (
          <>
            <div>
              <div className="eyebrow mb-1">Step 1 of 2 · Alpaca</div>
              <h1 className="text-[20px] font-bold tracking-tight2 text-white/92">Connect your broker</h1>
              <p className="text-white/55 text-[12.5px] mt-2">
                Paper-trading API keys from your{" "}
                <a href="https://app.alpaca.markets/paper/dashboard/overview" target="_blank" rel="noreferrer" className="text-[#7ce7b3] underline">
                  Alpaca dashboard
                </a>
                . Stored in your OS keychain — never on disk.
              </p>
            </div>
            <label className="block text-[12px] text-white/55">
              API key ID
              <input aria-label="alpaca-key-id" value={keyId} onChange={(e) => setKeyId(e.target.value)} className={INPUT} />
            </label>
            <label className="block text-[12px] text-white/55">
              API secret key
              <input aria-label="alpaca-secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} className={INPUT} />
            </label>
            <button onClick={() => void submitAlpaca()} disabled={busy || !keyId.trim() || !secret.trim()} className={PRIMARY}>
              {busy ? "Validating…" : "Validate & continue"}
            </button>
            <button onClick={() => { setError(null); setStep("welcome"); }} disabled={busy} className="w-full text-[12px] text-white/45 hover:text-white/70 transition-colors">
              Back
            </button>
          </>
        )}

        {step === "llm" && (
          <>
            <div>
              <div className="eyebrow mb-1">Step 2 of 2 · AI model</div>
              <h1 className="text-[20px] font-bold tracking-tight2 text-white/92">Choose your LLM</h1>
            </div>
            <div className="space-y-2">
              <button
                onClick={() => setProvider("ollama")}
                className={`w-full text-left px-3.5 py-3 rounded-lg border ${provider === "ollama" ? "border-[#00c27a]/50 bg-[#00c27a]/10" : "border-white/12 hover:bg-white/5"} transition-colors`}
              >
                <div className="text-[13px] font-semibold text-white/90">Local (Ollama)</div>
                <div className="text-[11.5px] text-white/45">Runs on your machine — private, no API key. Set up on first launch.</div>
              </button>
              <button
                onClick={() => setProvider("gemini")}
                className={`w-full text-left px-3.5 py-3 rounded-lg border ${provider === "gemini" ? "border-[#00c27a]/50 bg-[#00c27a]/10" : "border-white/12 hover:bg-white/5"} transition-colors`}
              >
                <div className="text-[13px] font-semibold text-white/90">Cloud (Gemini)</div>
                <div className="text-[11.5px] text-white/45">Uses a Google Gemini API key — no local install.</div>
              </button>
            </div>
            {provider === "gemini" && (
              <label className="block text-[12px] text-white/55">
                Gemini API key
                <input aria-label="gemini-key" type="password" value={geminiKey} onChange={(e) => setGeminiKey(e.target.value)} className={INPUT} />
              </label>
            )}
            {busy && ollamaMsg && (
              <div className="flex items-center gap-2.5 text-[12px] text-white/55">
                <span
                  aria-hidden
                  className="inline-block w-3.5 h-3.5 shrink-0 rounded-full border-2 border-white/15 border-t-[#7ce7b3] animate-spin"
                />
                <span>{ollamaMsg}</span>
              </div>
            )}
            <button
              onClick={() => void submitLlm()}
              disabled={busy || !provider || (provider === "gemini" && !geminiKey.trim())}
              className={PRIMARY}
            >
              {busy ? (provider === "ollama" ? "Setting up…" : "Saving…") : "Continue"}
            </button>
            <button onClick={() => { setError(null); setNeedsOllamaInstall(false); setStep("alpaca"); }} disabled={busy} className="w-full text-[12px] text-white/45 hover:text-white/70 transition-colors">
              Back
            </button>
          </>
        )}

        {step === "finish" && (
          <>
            <div>
              <div className="eyebrow mb-1">All set</div>
              <h1 className="text-[22px] font-bold tracking-tight2 text-white/92">You're ready</h1>
              <p className="text-white/55 text-[12.5px] mt-2">Launch the engine to start the operator console.</p>
            </div>
            <button onClick={() => void finish()} disabled={busy} className={PRIMARY}>
              {busy ? "Launching…" : "Launch autonomous_"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
