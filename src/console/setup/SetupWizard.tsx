import { useState, useEffect } from "react";
import {
  validateAlpaca,
  saveSecret,
  saveSetupState,
  startEngine,
  getSetupState,
} from "@/lib/desktopBridge";
import { LlmProviderCard } from "@/console/desktop/LlmProviderCard";

/**
 * First-run setup wizard (G4, #1050). G4-2 ships the flow: welcome → Alpaca
 * (live-validated, stored in the OS keychain) → LLM choice → launch. The full
 * Ollama provisioning (download + health) lands in G4-3; here "local" just
 * records the provider. Rendered by ConsoleApp (desktop only) when the keychain
 * has no secrets. The step machine is the gate: Finish is only reachable after
 * Alpaca validates AND an LLM is chosen.
 */
type Step = "requirements" | "welcome" | "alpaca" | "llm" | "finish";

const INPUT =
  "mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30";
const PRIMARY =
  "w-full text-[13px] font-semibold px-4 py-2.5 rounded-lg border border-[#00c27a]/30 bg-[#00c27a]/12 text-[#00c27a] disabled:opacity-40 disabled:cursor-not-allowed hover:bg-[#00c27a]/20 transition-colors";

export function SetupWizard({
  onComplete,
  onSkip,
}: {
  onComplete: () => void;
  onSkip?: () => void;
}) {
  const [step, setStep] = useState<Step>("requirements");
  const [name, setName] = useState("");
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Flips once LlmProviderCard reports a provider was applied (key saved / model pulled) — gates Continue.
  const [llmReady, setLlmReady] = useState(false);

  // #1977 resume: a returning user (name already saved to setup.json, but no
  // Alpaca keys yet — else the wizard wouldn't be shown) lands back on the Alpaca
  // step with the name prefilled, instead of restarting from the requirements
  // screen. No secrets are read here; only the non-secret setup.json name.
  useEffect(() => {
    let cancelled = false;
    void getSetupState().then((s) => {
      const savedName = typeof s?.name === "string" ? s.name.trim() : "";
      if (!cancelled && savedName) {
        setName(savedName);
        setStep("alpaca");
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

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
    <div className="aaa-console flex min-h-screen w-screen items-center justify-center bg-black px-6 py-8 overflow-y-auto">
      <div className="w-full max-w-md surface p-7 space-y-5">
        {error && (
          <div role="alert" className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        {step === "requirements" && (
          <>
            <div>
              <div className="eyebrow mb-1">Before you start</div>
              <h1 className="text-[22px] font-bold tracking-tight2 text-white/92">What you'll need</h1>
              <p className="text-white/55 text-[12.5px] mt-2">
                Two quick things — grab them now so setup takes a couple of minutes, not a scavenger hunt.
              </p>
            </div>
            <div className="space-y-3">
              <div className="rounded-lg border border-white/12 px-3.5 py-3">
                <div className="text-[13px] font-semibold text-white/90">1 · A free Alpaca paper account</div>
                <div className="text-[11.5px] text-white/45 mt-0.5">
                  ~3 minutes · no deposit, instant. Paper trading uses fake money — free and available right away.
                </div>
                <a
                  href="https://alpaca.markets/"
                  target="_blank"
                  rel="noreferrer"
                  className="inline-block mt-2 text-[12px] text-[#00c27a] underline"
                >
                  Create a free account ↗
                </a>
              </div>
              <div className="rounded-lg border border-white/12 px-3.5 py-3">
                <div className="text-[13px] font-semibold text-white/90">2 · An AI model</div>
                <div className="text-[11.5px] text-white/45 mt-0.5">
                  Local (Ollama — we set it up, no key) or a Google Gemini API key. You'll choose in a moment.
                </div>
              </div>
            </div>
            <p className="text-[11.5px] text-white/45">
              Everything stays on your machine. Desktop runs in paper-trading mode.
            </p>
            <button onClick={() => { setError(null); setStep("welcome"); }} className={PRIMARY}>
              Get started
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
            <button onClick={() => { setError(null); setStep("requirements"); }} disabled={busy} className="w-full text-[12px] text-white/45 hover:text-white/70 transition-colors">
              Back
            </button>
          </>
        )}

        {step === "alpaca" && (
          <>
            <div>
              <div className="eyebrow mb-1">Step 1 of 2 · Alpaca</div>
              <h1 className="text-[20px] font-bold tracking-tight2 text-white/92">Connect your broker</h1>
              <p className="text-white/55 text-[12.5px] mt-2">
                Paste your Alpaca <span className="text-white/70">paper-trading</span> API keys — free, no
                deposit required. Stored in your OS keychain, never on disk.
              </p>
              <ol className="text-[11.5px] text-white/45 mt-2 space-y-0.5 list-decimal list-inside">
                <li>
                  <a href="https://alpaca.markets/" target="_blank" rel="noreferrer" className="text-[#00c27a] underline">Create a free paper account ↗</a>
                </li>
                <li>
                  Open the{" "}
                  <a href="https://app.alpaca.markets/paper/dashboard/overview" target="_blank" rel="noreferrer" className="text-[#00c27a] underline">Paper dashboard</a>
                  {" "}→ Generate API keys
                </li>
                <li>Paste the key ID + secret below</li>
              </ol>
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
              <h1 className="text-[20px] font-bold tracking-tight2 text-white/92">Choose your AI model</h1>
            </div>
            {/* Vendor-independent picker — same component as Settings (bare, no engine restart;
                the wizard starts the engine at the end). Continue unlocks once a provider applies. */}
            <LlmProviderCard bare showRestart={false} onApplied={() => setLlmReady(true)} />
            <button
              onClick={() => { setError(null); setStep("finish"); }}
              disabled={busy || !llmReady}
              className={PRIMARY}
            >
              Continue
            </button>
            <button onClick={() => { setError(null); setStep("alpaca"); }} disabled={busy} className="w-full text-[12px] text-white/45 hover:text-white/70 transition-colors">
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
