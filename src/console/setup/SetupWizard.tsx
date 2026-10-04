import { useState, useEffect } from "react";
import {
  validateAlpaca,
  saveSecret,
  saveSetupState,
  startEngine,
  getSetupState,
} from "@/lib/desktopBridge";
import { LlmProviderCard } from "@/console/desktop/LlmProviderCard";
import { SETUP_COMPLETED_KEY } from "./setupCompletion";

/**
 * First-run setup wizard (G4, #1050). G4-2 ships the flow: welcome → Alpaca
 * (live-validated, stored in the OS keychain) → LLM choice → launch. The full
 * Ollama provisioning (download + health) lands in G4-3; here "local" just
 * records the provider. Rendered by ConsoleApp (desktop only) when the keychain
 * has no secrets. The step machine is the gate: Finish is only reachable after
 * Alpaca validates AND an LLM is chosen.
 *
 * B9: the wizard is done only when it says so. Finish writes `setup_completed` into the non-secret
 * setup.json (setupCompletion.ts); ConsoleApp reads it on every start. When ConsoleApp re-shows the
 * wizard with `keysPresent` (keys saved, model step abandoned), it resumes on the model step.
 */
type Step = "requirements" | "welcome" | "alpaca" | "llm" | "finish";

// G5-3b: one label for every place the demo skip appears (requirements and, since B9, the model step).
const SKIP_LABEL = "Skip for now — explore in demo mode";
const SKIP_CLASS = "w-full text-[12px] text-white/45 hover:text-white/70 transition-colors";

// UXC-1 S8 (#3177): no per-element green focus — the global white focus-visible ring applies.
const INPUT =
  "mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none transition-all duration-200";
const PRIMARY =
  "w-full text-[13px] font-bold tracking-wide px-5 py-2.5 rounded-full bg-[#00c27a] hover:bg-[#00d687] text-white disabled:opacity-40 disabled:cursor-not-allowed transition-all transform active:scale-[0.98]";

const ALL_STEPS: Step[] = ["requirements", "welcome", "alpaca", "llm", "finish"];

export function SetupWizard({
  onComplete,
  onSkip,
  keysPresent = false,
}: {
  onComplete: () => void;
  onSkip?: () => void;
  /** B9: the OS keychain already holds secrets (ConsoleApp's probe). The broker step is done. */
  keysPresent?: boolean;
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
  // Alpaca keys yet) lands back on the Alpaca step with the name prefilled,
  // instead of restarting from the requirements screen. No secrets are read
  // here; only the non-secret setup.json name.
  // B9 resume: keys already in the keychain (ConsoleApp probed them) means the broker step is done,
  // so the wizard lands on the first unfinished step, the model choice. Only the probe's boolean is
  // used; no secret is read.
  useEffect(() => {
    if (typeof window !== "undefined") {
      const h = window.location.hash;
      if (h === "#setup-llm") {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setStep("llm");
        setLlmReady(true);
        return;
      }
      if (h === "#setup-finish") {
        setStep("finish");
        return;
      }
    }
    if (keysPresent) {
      setStep("llm");
      return;
    }
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
  }, [keysPresent]);

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
    // B9: the done-mark goes first. Keys and model are in place at this point; a failing engine
    // start is a runtime problem the dashboard shows and lets the user retry, not a reason to run
    // the wizard again on the next start.
    try {
      await saveSetupState({ [SETUP_COMPLETED_KEY]: true });
    } catch {
      setError("Couldn't save — please try again.");
      setBusy(false);
      return;
    }
    try {
      await startEngine();
      onComplete();
    } catch {
      setError("Couldn't start the engine — please try again.");
    } finally {
      setBusy(false);
    }
  }

  const progress = (ALL_STEPS.indexOf(step) / (ALL_STEPS.length - 1)) * 100;

  return (
    <div className="aaa-console flex min-h-screen max-h-screen w-screen bg-black px-6 py-8 overflow-y-auto justify-center [align-items:safe_center]">
      {/* `safe center` instead of m-auto: a flex-centered child taller than the
          viewport gets its TOP clipped beyond scroll reach — live on the first
          customer install (RDP, small viewport) the wizard could not scroll. */}
      <div className="w-full max-w-md bg-white/[0.04] border border-white/10 backdrop-blur-md rounded-2xl shadow-2xl p-8 space-y-6 transition-all duration-300 ease-out">
        {import.meta.env.DEV && (
          <div className="flex flex-wrap items-center justify-center gap-1.5 pb-2 text-[10px] text-white/50 border-b border-white/10">
            <span className="font-semibold text-white/70 uppercase tracking-wider mr-1">Preview Step:</span>
            <button onClick={() => setStep("requirements")} className={`px-2 py-0.5 rounded ${step === "requirements" ? "bg-white/12 text-white font-semibold" : "bg-white/5 hover:bg-white/10"}`}>1. Start</button>
            <button onClick={() => setStep("welcome")} className={`px-2 py-0.5 rounded ${step === "welcome" ? "bg-white/12 text-white font-semibold" : "bg-white/5 hover:bg-white/10"}`}>2. Name</button>
            <button onClick={() => setStep("alpaca")} className={`px-2 py-0.5 rounded ${step === "alpaca" ? "bg-white/12 text-white font-semibold" : "bg-white/5 hover:bg-white/10"}`}>3. Alpaca Keys</button>
            <button onClick={() => { setStep("llm"); setLlmReady(true); }} className={`px-2 py-0.5 rounded ${step === "llm" ? "bg-white/12 text-white font-semibold" : "bg-white/5 hover:bg-white/10"}`}>4. AI Model (After Keys)</button>
            <button onClick={() => setStep("finish")} className={`px-2 py-0.5 rounded ${step === "finish" ? "bg-white/12 text-white font-semibold" : "bg-white/5 hover:bg-white/10"}`}>5. Finish</button>
            {onSkip && <button onClick={onSkip} className="btn-link ml-auto">Exit Wizard ↗</button>}
          </div>
        )}
        <div className="w-full bg-white/10 h-1.5 rounded-full overflow-hidden">
          <div className="bg-[#00c27a] h-full transition-all duration-500 ease-out" style={{ width: `${progress}%` }} />
        </div>

        {error && (
          <div role="alert" className="text-[12px] text-white/80 bg-white/[0.05] border border-white/10 rounded-xl px-4 py-3 flex items-center gap-2">
            <span className="text-[#ff5a52]">⚠</span>
            <span>{error}</span>
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
                  Local (i.e. Mistral, Gemma, via Ollama — we set it up, no key) or a Provider API Key (i.e. ChatGPT, Google Gemini or Anthropic). You'll choose in a moment.
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
              <button onClick={onSkip} className={SKIP_CLASS}>
                {SKIP_LABEL}
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
              <ol className="text-[11.5px] text-white/45 mt-2 space-y-1 list-decimal list-inside">
                <li>
                  <a href="https://alpaca.markets/" target="_blank" rel="noreferrer" className="text-[#00c27a] underline">Create a free paper account ↗</a>
                </li>
                <li>
                  Open{" "}
                  <a href="https://app.alpaca.markets/paper/dashboard/overview" target="_blank" rel="noreferrer" className="text-[#00c27a] underline">Alpaca ↗</a>{" "}
                  and switch to the <span className="text-white/70">Paper</span> account (toggle, top-left)
                </li>
                <li>
                  On <span className="text-white/70">Home</span>, find the{" "}
                  <span className="text-white/70">API Keys</span> panel (right side) → <span className="text-white/70">Generate</span>
                </li>
                <li>Paste the Key ID + Secret below</li>
              </ol>
              <p className="text-[10.5px] text-white/30 mt-1.5">
                Stuck on a “Welcome” page? Toggle to <span className="text-white/50">Paper</span> first — the keys only appear in the paper account.
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
            {/* B9 + G5-3b: the resumed wizard lands here; without a skip the only way to the demo
                dashboard would be three times Back. Demo mode is still "setup not done" (banner). */}
            {onSkip ? (
              <button onClick={onSkip} disabled={busy} className={SKIP_CLASS}>
                {SKIP_LABEL}
              </button>
            ) : null}
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
