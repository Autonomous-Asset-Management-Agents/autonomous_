import { useEffect, useRef, useState, type ReactNode, type SVGProps } from "react";
import {
  isDesktop as realIsDesktop,
  getSetupState as realGetSetupState,
  saveSetupState as realSaveSetupState,
  saveSecret as realSaveSecret,
  provisionOllama as realProvisionOllama,
  startEngine as realStartEngine,
  stopEngine as realStopEngine,
  type OllamaProgress,
  type OllamaProvisionResult,
  type SaveSecretResult,
} from "@/lib/desktopBridge";
import { StatusDot } from "@/console/shared/StatusDot";

/**
 * Vendor-independent LLM provider card (supersedes the Gemini-only LlmKeysCard, #1705).
 * Lets the operator pick a LOCAL model (Ollama: Mistral / Llama, provisioned + run on-device,
 * no key) or any CLOUD provider (Gemini / OpenAI / Anthropic) with their own API key — and
 * switch between them any time (fixes the "no way back to local" gap). Keys go to the OS
 * keychain (per-vendor slot); provider + local model land in setup.json; the engine reads
 * LLM_PROVIDER via the ADR-014 seam (core/llm/provider.py).
 *
 * Design language is unified with the Settings → Trading "Execution mode" selector:
 * `p-4 rounded-xl border` selectable cards (active = green + `pill pill-bull`), the same
 * info-box idiom, a lightbulb explanation. Solid "plain colour" CTA.
 *
 * Side effects are injected (`deps`) so the same component powers the browser preview and
 * unit tests without a live Electron bridge. Desktop-only in production (the cloud edition
 * manages credentials via GCP Secret Manager).
 */

export interface LlmDeps {
  isDesktop: () => boolean;
  getSetupState: () => Promise<Record<string, unknown>>;
  saveSetupState: (partial: Record<string, unknown>) => Promise<void>;
  saveSecret: (key: string, value: string) => Promise<SaveSecretResult>;
  provisionOllama: (onProgress: (p: OllamaProgress) => void, model?: string) => Promise<OllamaProvisionResult>;
  startEngine: () => Promise<void>;
  stopEngine: () => Promise<void>;
}

const REAL_DEPS: LlmDeps = {
  isDesktop: realIsDesktop,
  getSetupState: realGetSetupState,
  saveSetupState: realSaveSetupState,
  saveSecret: realSaveSecret,
  provisionOllama: realProvisionOllama,
  startEngine: realStartEngine,
  stopEngine: realStopEngine,
};

// Lightbulb (lucide-style, console stroke look) — used in the explanation box.
function IconLightbulb(p: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...p}
    >
      <path d="M15 14c.2-1 .7-1.7 1.5-2.5A5.5 5.5 0 1 0 6.5 8c0 1 .3 2 1.5 3.5.8.8 1.3 1.5 1.5 2.5" />
      <path d="M9 18h6" />
      <path d="M10 22h4" />
    </svg>
  );
}

interface LocalOpt {
  id: string;
  name: string;
  /** Ollama tag to pull; undefined → the vetted default (Mistral) resolved by the main process. */
  model?: string;
  blurb: string;
}

interface CloudOpt {
  id: "gemini" | "openai" | "anthropic";
  name: string;
  slot: string; // OS keychain slot
  keyHint: string;
  blurb: string;
}

const LOCAL: LocalOpt[] = [
  {
    id: "mistral",
    name: "Mistral 7B",
    blurb: "Runs fully on your machine — private, offline, free. Best local quality. ~4.4 GB one-time download.",
  },
  {
    id: "llama",
    name: "Llama 3.2",
    model: "llama3.2",
    blurb: "Lighter local model for modest hardware or no GPU. Private, offline, free. ~2 GB one-time download.",
  },
];

const CLOUD: CloudOpt[] = [
  {
    id: "gemini",
    name: "Google Gemini",
    slot: "GEMINI_API_KEY",
    keyHint: "AIza…",
    blurb: "Strong reasoning via Google. Needs a Google API key; prompts are sent to Google.",
  },
  {
    id: "openai",
    name: "ChatGPT · OpenAI",
    slot: "OPENAI_API_KEY",
    keyHint: "sk-…",
    blurb: "GPT-4-class models via OpenAI. Needs an OpenAI API key; prompts are sent to OpenAI.",
  },
  {
    id: "anthropic",
    name: "Claude · Anthropic",
    slot: "ANTHROPIC_API_KEY",
    keyHint: "sk-ant-…",
    blurb: "Claude models via Anthropic. Needs an Anthropic API key; prompts are sent to Anthropic.",
  },
];

const TILE_NAME: Record<string, string> = Object.fromEntries(
  [...LOCAL, ...CLOUD].map((o) => [o.id, o.name]),
);

/** Which tile matches the persisted setup.json state. "" = nothing configured yet. */
function currentTileId(s: Record<string, unknown>): string {
  const provider = String(s.LLM_PROVIDER ?? "");
  if (provider === "ollama") {
    return String(s.LOCAL_LLM_MODEL ?? "").toLowerCase().includes("llama") ? "llama" : "mistral";
  }
  if (provider === "gemini" || provider === "openai" || provider === "anthropic") return provider;
  return "";
}

// Solid "plain colour" button (Upgrade green) — console-sized.
function SolidButton({ children, onClick, disabled = false }: { children: ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="rounded-full px-5 py-2 text-[12px] font-bold tracking-wide text-white bg-[#00c27a] hover:bg-[#00d687] border border-transparent transition-all transform active:scale-[0.98] disabled:opacity-40"
    >
      {children}
    </button>
  );
}

// Selection tile — identical pattern to the Execution-mode selector.
function ProviderCard({
  id,
  name,
  blurb,
  selected,
  current,
  onSelect,
}: {
  id: string;
  name: string;
  blurb: string;
  selected: boolean;
  current: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={`p-4 rounded-xl border text-left transition-all ${
        selected ? "border-white/20 bg-white/[0.06]" : "border-white/10 hover:border-white/20 bg-white/[0.03]"
      }`}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`text-[13px] font-semibold ${selected ? "text-white" : "text-white/70"}`}>{name}</span>
        {selected && <StatusDot tone="on" className="ml-auto !text-[11px]">{current ? "in use" : "selected"}</StatusDot>}
      </div>
      <div className="text-[11px] text-white/30 leading-relaxed">{blurb}</div>
    </button>
  );
}

export function LlmProviderCard({
  deps = REAL_DEPS,
  bare = false,
  showRestart = true,
  onApplied,
}: {
  deps?: LlmDeps;
  /** Render without the `surface p-6` wrapper (for embedding inside the setup wizard's own card). */
  bare?: boolean;
  /** Show the "restart engine to apply" affordance (Settings). The wizard starts the engine at the end, so it passes false. */
  showRestart?: boolean;
  /** Fired after a provider is successfully applied (key saved / model provisioned) — lets the wizard gate its Continue. */
  onApplied?: (providerId: string) => void;
} = {}) {
  const [current, setCurrent] = useState<string>("");
  const [selected, setSelected] = useState<string>("mistral");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Manual-install recovery: when auto-install of the local runtime fails, surface a clickable link.
  const [needsOllamaInstall, setNeedsOllamaInstall] = useState(false);
  const loaded = useRef(false);

  useEffect(() => {
    deps
      .getSetupState()
      .then((s) => {
        const cur = currentTileId(s);
        setCurrent(cur);
        if (!loaded.current) {
          setSelected(cur || "mistral");
          loaded.current = true;
        }
      })
      .catch(() => {
        /* browser / no bridge — stays default */
      });
  }, [deps]);

  // Desktop-only in production; the DEV browser preview + injected test deps also render it,
  // mirroring BrokerKeysCard/EngineCard so all desktop cards preview consistently.
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!deps.isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  const localOpt = LOCAL.find((o) => o.id === selected);
  const cloudOpt = CLOUD.find((o) => o.id === selected);
  const selectedName = TILE_NAME[selected] ?? "—";
  const currentName = current ? TILE_NAME[current] : "—";
  const currentIsLocal = current === "mistral" || current === "llama";

  function pick(id: string) {
    setSelected(id);
    setKey("");
    setSaved(false);
    setProgress(null);
    setError(null);
    setNeedsOllamaInstall(false);
  }

  async function applyLocal(o: LocalOpt) {
    setBusy(true);
    setError(null);
    setSaved(false);
    setNeedsOllamaInstall(false);
    setProgress(0);
    try {
      const res = await deps.provisionOllama((p) => setProgress(p.percent ?? 0), o.model);
      if (res.needsManual) {
        setError(res.error || "Couldn't auto-install the local AI runtime. Install Ollama, then retry.");
        setNeedsOllamaInstall(true);
        return;
      }
      if (!res.ok) {
        setError(res.error || "Local AI setup failed.");
        return;
      }
      await deps.saveSetupState({
        LLM_PROVIDER: "ollama",
        LOCAL_LLM_MODEL: res.model,
        OLLAMA_BASE_URL: res.baseUrl,
      });
      setCurrent(o.id);
      setProgress(100);
      setSaved(true);
      onApplied?.(o.id);
    } catch {
      setError("Something went wrong — please try again.");
      setProgress(null);
    } finally {
      setBusy(false);
    }
  }

  async function applyCloud(o: CloudOpt) {
    if (!key.trim()) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const r = await deps.saveSecret(o.slot, key.trim());
      if (!r.ok) {
        setError("Validated the key, but saving to the keychain failed.");
        return;
      }
      await deps.saveSetupState({ LLM_PROVIDER: o.id });
      setCurrent(o.id);
      setSaved(true);
      setKey("");
      onApplied?.(o.id);
    } catch {
      setError("Something went wrong — please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function restart() {
    setRestarting(true);
    try {
      // Stop-then-start so a running engine re-reads the provider/keychain; on an OFFLINE engine
      // the stop is a no-op and start brings it up with the new provider.
      await deps.stopEngine();
      await deps.startEngine();
      setSaved(false);
    } catch {
      setError("Couldn't restart the engine — use the engine controls above.");
    } finally {
      setRestarting(false);
    }
  }

  return (
    <div className={bare ? "" : "surface p-6"}>
      {/* Header — same idiom as the Execution-mode card */}
      <div className="flex items-center justify-between mb-3">
        <div className="eyebrow">AI model</div>
        <span className={`pill ${currentIsLocal ? "pill-bull" : "pill-strong"}`}>
          Current: {currentName}
          {current ? (currentIsLocal ? " · local" : " · cloud") : ""}
        </span>
      </div>
      <p className="text-[12px] text-white/45 mb-5 max-w-lg leading-relaxed">
        Keep it fully local for privacy, or connect any cloud provider with your own key —
        vendor-independent, switch anytime.
      </p>

      {/* On your machine */}
      <div className="text-[10px] text-white/30 uppercase tracking-wider mb-2">On your machine — private</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-5">
        {LOCAL.map((o) => (
          <ProviderCard
            key={o.id}
            id={o.id}
            name={o.name}
            blurb={o.blurb}
            selected={selected === o.id}
            current={current === o.id}
            onSelect={() => pick(o.id)}
          />
        ))}
      </div>

      {/* Cloud providers */}
      <div className="text-[10px] text-white/30 uppercase tracking-wider mb-2">Cloud providers — bring your own key</div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {CLOUD.map((o) => (
          <ProviderCard
            key={o.id}
            id={o.id}
            name={o.name}
            blurb={o.blurb}
            selected={selected === o.id}
            current={current === o.id}
            onSelect={() => pick(o.id)}
          />
        ))}
      </div>

      {/* Action panel (depends on selection) */}
      <div className="mt-5">
        {localOpt ? (
          <>
            {progress !== null && (
              <div className="mb-3">
                <div className="h-1.5 rounded-full overflow-hidden bg-white/[0.06]">
                  <div className="h-full rounded-full transition-all" style={{ width: `${progress}%`, background: "#00c27a" }} />
                </div>
                <div className="text-[10.5px] text-white/45 num mt-1.5">
                  {saved && progress === 100 ? "Ready — running locally." : `Downloading… ${progress}% (first run only)`}
                </div>
              </div>
            )}
            <SolidButton onClick={() => void applyLocal(localOpt)} disabled={busy}>
              {busy ? "Working…" : current === localOpt.id ? `Re-download ${localOpt.name}` : `Download & use ${localOpt.name}`}
            </SolidButton>
          </>
        ) : cloudOpt ? (
          <>
            <label className="block text-[11px] text-white/45 mb-1">{cloudOpt.name} API key</label>
            <input
              type="password"
              aria-label={`${cloudOpt.id}-key`}
              className="w-full max-w-md rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30 disabled:opacity-40"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={cloudOpt.keyHint}
              disabled={busy}
            />
            <div className="mt-3 flex items-center gap-3">
              <SolidButton onClick={() => void applyCloud(cloudOpt)} disabled={busy || !key.trim()}>
                {busy ? "Saving…" : `Save & use ${cloudOpt.name}`}
              </SolidButton>
            </div>
          </>
        ) : null}

        {error && <div className="text-[11px] text-bear mt-2">{error}</div>}
        {needsOllamaInstall && (
          <p className="text-[11px] text-white/55 mt-1">
            Install it from{" "}
            <a href="https://ollama.com/download" target="_blank" rel="noreferrer" className="text-bull underline">
              ollama.com/download
            </a>
            , then retry.
          </p>
        )}
        {saved &&
          (showRestart ? (
            <div className="text-[11px] text-white/55 mt-2">
              Saved.{" "}
              <button className="underline hover:text-white/80" onClick={() => void restart()} disabled={restarting}>
                {restarting ? "Restarting…" : "Restart engine to apply"}
              </button>
            </div>
          ) : (
            <div className="text-[11px] text-white/55 mt-2">Saved — you can continue.</div>
          ))}
      </div>

      {/* How it works — same info-box idiom as the Execution-mode confirmation, with the lightbulb */}
      <div className="mt-5 flex items-start gap-2 px-4 py-3 rounded-lg bg-white/[0.05] border border-white/10">
        <IconLightbulb width={14} height={14} className="text-bull shrink-0 mt-0.5" />
        <span className="text-[12px] text-white/70 leading-relaxed">
          <span className="text-white/90 font-medium">How it works.</span> Your chosen model only drafts the
          analysis. Every trade still passes the deterministic risk gates and waits for your approval — the model
          never trades on its own. Paper-trading is the default. {selectedName !== "—" ? "" : ""}
        </span>
      </div>
    </div>
  );
}
