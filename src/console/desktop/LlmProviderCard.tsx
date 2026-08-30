import { useCallback, useEffect, useRef, useState, type ReactNode, type SVGProps } from "react";
import {
  isDesktop as realIsDesktop,
  getSetupState as realGetSetupState,
  saveSetupState as realSaveSetupState,
  saveSecret as realSaveSecret,
  provisionOllama as realProvisionOllama,
  listOllamaModels as realListOllamaModels,
  getRecommendedModel as realGetRecommendedModel,
  startEngine as realStartEngine,
  stopEngine as realStopEngine,
  getKeychainStatus as realGetKeychainStatus,
  type OllamaProgress,
  type OllamaProvisionResult,
  type OllamaModelsResult,
  type RecommendedModel,
  type SaveSecretResult,
} from "@/lib/desktopBridge";
import { StatusDot } from "@/console/shared/StatusDot";
import { fetchHealth } from "@/lib/api";

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
  checkEngineHealth?: () => Promise<boolean>;
  /** #2130: which local models the Ollama daemon has actually pulled (GET /api/tags). Optional so
   *  the browser preview / older bridges degrade to "unknown" (no install badges) rather than crash. */
  listOllamaModels?: () => Promise<OllamaModelsResult>;
  /** #F5 (presence marker): per-slot OS-keychain presence (presence only, NEVER any secret value).
   *  Drives the "key already stored" masked placeholder on the cloud-provider key input. Optional so
   *  the browser preview / older bridges / tests degrade to no marker. Mirrors BrokerKeysCard's probe. */
  getKeychainStatus?: () => Promise<Record<string, boolean>>;
  /** #2706: the shell's RAM-aware recommendation `{ model, totalBytes, lowRam }`. Optional — with
   *  no bridge (browser preview / older shell) the card shows NO recommendation badge and behaves
   *  exactly as it did before. The RAM threshold lives ONLY in ollama-manager.cjs; this card
   *  renders the decision it is handed and never re-derives it. */
  getRecommendedModel?: () => Promise<RecommendedModel | null>;
}

const REAL_DEPS: LlmDeps = {
  isDesktop: realIsDesktop,
  getSetupState: realGetSetupState,
  saveSetupState: realSaveSetupState,
  saveSecret: realSaveSecret,
  provisionOllama: realProvisionOllama,
  listOllamaModels: realListOllamaModels,
  getRecommendedModel: realGetRecommendedModel,
  getKeychainStatus: realGetKeychainStatus,
  startEngine: realStartEngine,
  stopEngine: realStopEngine,
  checkEngineHealth: async () => {
    try {
      const h = await fetchHealth();
      return h !== null;
    } catch {
      return false;
    }
  },
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

// #2706: two changes here, both measured.
//   1. The Llama blurb used to claim "— recommended" for EVERY machine. Measured on a 16 GB box:
//      llama-server holds ~5-7 GB, on top of ~4 GB OS/browser, ~1.3-1.6 GB Electron main and
//      ~0.6-1.5 GB Python engine — no reserve. A static recommendation steered exactly the
//      operators who cannot afford it. Recommendation is now RAM-aware and rendered as a badge.
//   2. The Mistral tile carried NO `model`, so provisioning fell through to main's
//      `model || resolveDefaultModel()` and silently installed phi3.5 on a low-RAM machine — the
//      tile named one model and installed another. Every tile now carries its explicit vetted tag.
const LOCAL: LocalOpt[] = [
  {
    id: "llama",
    name: "Llama 3.1 8B",
    model: "llama3.1:8b",
    blurb:
      "The model our research cards are tuned and validated on. Runs fully on your machine, private, offline, free. Holds ~5 GB in RAM while running. ~4.6 GB one-time download.",
  },
  {
    id: "phi",
    name: "Phi-3.5 (light)",
    model: "phi3.5",
    blurb:
      "Lightweight local model — best for 16 GB machines (uses ~2.5 GB RAM vs ~7 GB for the larger models). Private, offline, free, MIT-licensed. ~2.2 GB one-time download.",
  },
  {
    id: "mistral",
    // Must match ollama-manager.cjs DEFAULT_MODEL — the vetted Apache-2.0 / Q4_K_M tag.
    name: "Mistral 7B",
    model: "mistral:7b-instruct-v0.3-q4_K_M",
    blurb:
      "Alternative local model. Private, offline, free. Holds ~5-7 GB in RAM while running. ~4.4 GB one-time download.",
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

// #F5 (presence marker): masked placeholder shown when the provider's key is ALREADY in the OS
// keychain. Purely visual — never the real value; a blank field on submit keeps the stored key and
// still applies the provider switch (see canApplyCloud / applyCloud).
//
// #2715 — CORRECTION. This comment used to read "an empty field on submit keeps the stored key
// (applyCloud early-returns on a blank key)". Both halves were wrong in a way that mattered: there
// was no submit to early-return from, because the Apply button was disabled on a blank field — so
// following this placeholder's own instruction left a dead button and no way to switch to a cloud
// provider whose key was already stored. And the early return did keep the key only by writing
// NOTHING, which also silently skipped the provider switch. Fixed at the predicate, not here.
const KEY_PRESENT_PLACEHOLDER = "•••••••••••••• — stored (leave blank to keep)";

/** Which tile matches the persisted setup.json state. "" = nothing configured yet. */
function currentTileId(s: Record<string, unknown>): string {
  const provider = String(s.LLM_PROVIDER ?? "");
  if (provider === "ollama") {
    const m = String(s.LOCAL_LLM_MODEL ?? "").toLowerCase();
    if (m.includes("phi")) return "phi";
    return m.includes("llama") ? "llama" : "mistral";
  }
  if (provider === "gemini" || provider === "openai" || provider === "anthropic") return provider;
  return "";
}

/** #2130: True when a local option's model is actually PULLED — its base name (before the `:tag`
 *  suffix) is present in the Ollama tag list. e.g. tile "mistral" matches "mistral:7b-instruct-…",
 *  tile "llama" (tag llama3.1:8b) matches "llama3.1:8b". */
function localModelInstalled(o: LocalOpt, installed: string[]): boolean {
  const base = String(o.model || o.id).split(":")[0].toLowerCase();
  return installed.some((m) => m.split(":")[0].toLowerCase() === base);
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

function ProviderCard({
  id,
  name,
  blurb,
  selected,
  current,
  installed,
  recommended,
  onSelect,
  children,
}: {
  id: string;
  name: string;
  blurb: string;
  selected: boolean;
  current: boolean;
  /** #2130: local tiles only — true = model pulled, false = not pulled. undefined = unknown/cloud (no badge). */
  installed?: boolean;
  /** #2706: this tile is the RAM-aware recommendation for THIS machine. Absent/false ⇒ no badge. */
  recommended?: boolean;
  onSelect: () => void;
  children?: ReactNode;
}) {
  return (
    <div
      data-testid={`tile-${id}`}
      role={!selected ? "button" : "region"}
      tabIndex={!selected ? 0 : undefined}
      onClick={!selected ? onSelect : undefined}
      className={`p-4 rounded-xl border text-left transition-all ${
        selected ? "border-white/20 bg-white/[0.06]" : "border-white/10 hover:border-white/20 bg-white/[0.03] cursor-pointer"
      }`}
    >
      {/* Title row: the name owns this line. The badges used to sit here too, which broke the
          layout in a one-third-width tile — "Recommended for your machine" alone wrapped onto two
          lines and pushed the title around. Badges now have their own row below, where they wrap
          predictably and the titles stay aligned across the three tiles. */}
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`text-[13px] font-semibold ${selected ? "text-white" : "text-white/70"}`}>{name}</span>
        {selected && installed !== false && (
          <StatusDot tone="on" className="ml-auto !text-[11px]">{current ? "in use" : "selected"}</StatusDot>
        )}
      </div>
      {/* Badge row. Only the sanctioned pill primitives from console.css are used — `pill` (neutral)
          and `pill-bull` (green). No hand-rolled hex tints, and no amber/gold anywhere: that is the
          operator's design decision, already recorded in HitlPolicyCard ("no gold/amber; red for the
          high-autonomy state"). "Not Installed" is a state, not a fault, so it stays neutral; the
          urgency for a selected-but-missing model lives in the notice above the grid, not in a
          colour. `Recommended` is short by design — the "for your machine" qualifier belongs to the
          section heading, which states it once with the measured RAM. */}
      {(recommended || installed !== undefined) && (
        <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
          {recommended && (
            <span className="pill" title="Best fit for this machine's memory">
              Recommended
            </span>
          )}
          {installed === true && <span className="pill pill-bull">{current ? "Installed · active" : "Installed"}</span>}
          {installed === false && <span className="pill">Not installed</span>}
        </div>
      )}
      <div className="text-[11px] text-white/30 leading-relaxed">{blurb}</div>
      {children}
    </div>
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
  // #2130: which local models Ollama has actually pulled + whether the availability check has run
  // once (so we never flash a false "not installed" before the async probe resolves).
  const [installedModels, setInstalledModels] = useState<string[]>([]);
  const [llmChecked, setLlmChecked] = useState(false);
  // #F5 (presence marker): per-slot OS-keychain presence — drives the "key already stored" masked
  // placeholder on the cloud key input. Presence only (never a value); {} until the probe resolves.
  const [keySlots, setKeySlots] = useState<Record<string, boolean>>({});
  // #2706: the shell's RAM-aware recommendation. null = unknown (no bridge / probe failed) ⇒ no
  // badge, no warning, card identical to before. The threshold rule is NOT re-derived here.
  const [ramRec, setRamRec] = useState<RecommendedModel | null>(null);
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

  // #2130: probe the actual local-model install status (Ollama GET /api/tags). Fail-safe: no bridge /
  // any error → empty list, but llmChecked flips true so the UI can render a definitive status.
  // Promise-chain (not sync async body) so every setState lands in a .then/.catch/.finally callback
  // — never synchronously inside the effect (react-hooks/set-state-in-effect), mirroring getSetupState.
  const refreshModels = useCallback(() => {
    const p = deps.listOllamaModels
      ? deps.listOllamaModels()
      : Promise.resolve<OllamaModelsResult>({ reachable: false, models: [] });
    return p
      .then((r) => setInstalledModels(Array.isArray(r?.models) ? r.models : []))
      .catch(() => setInstalledModels([]))
      .finally(() => setLlmChecked(true));
  }, [deps]);

  useEffect(() => {
    void refreshModels();
  }, [refreshModels]);

  // #F5 (presence marker): probe the OS keychain once on mount so a cloud provider whose key is
  // already stored shows a masked "stored" placeholder instead of the format hint. Presence only;
  // the bridge never returns secret values. Fail-safe: no bridge / any error → {} (no marker).
  // setState lands in a .then callback (never synchronously in the effect), mirroring refreshModels.
  useEffect(() => {
    let alive = true;
    const probe = deps.getKeychainStatus
      ? deps.getKeychainStatus()
      : Promise.resolve<Record<string, boolean>>({});
    probe
      .then((st) => {
        if (alive) setKeySlots(st && typeof st === "object" ? st : {});
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [deps]);

  // #2706: probe the RAM-aware recommendation once on mount. Same promise-chain shape as the two
  // probes above (setState only in .then/.catch — never synchronously in the effect). Fail-safe:
  // no bridge or any error leaves ramRec null, which renders no badge and no warning.
  useEffect(() => {
    let alive = true;
    const probe = deps.getRecommendedModel
      ? deps.getRecommendedModel()
      : Promise.resolve<RecommendedModel | null>(null);
    probe
      .then((r) => {
        if (alive) setRamRec(r && typeof r.model === "string" && r.model ? r : null);
      })
      .catch(() => {
        if (alive) setRamRec(null);
      });
    return () => {
      alive = false;
    };
  }, [deps]);

  // Desktop-only in production; the DEV browser preview + injected test deps also render it,
  // mirroring BrokerKeysCard/EngineCard so all desktop cards preview consistently.
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!deps.isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  const localOpt = LOCAL.find((o) => o.id === selected);
  const cloudOpt = CLOUD.find((o) => o.id === selected);
  const selectedName = TILE_NAME[selected] ?? "—";
  const currentName = current ? TILE_NAME[current] : "—";
  const currentIsLocal = current === "mistral" || current === "llama" || current === "phi";
  // #2130: is the CURRENTLY-configured local model actually pulled? (only meaningful once checked)
  const currentLocalOpt = LOCAL.find((o) => o.id === current);
  const currentLocalMissing =
    llmChecked && currentIsLocal && !!currentLocalOpt && !localModelInstalled(currentLocalOpt, installedModels);

  // #2706: which tile fits THIS machine, and whether the operator is about to overshoot it.
  // `ramRec.model` is an exact tag from ollama-manager, so the match is an equality — no parsing,
  // no second threshold. Unknown recommendation ⇒ recommendedTileId "" ⇒ no badge anywhere.
  const recommendedTileId = ramRec ? (LOCAL.find((o) => o.model === ramRec.model)?.id ?? "") : "";
  // Overshoot = a low-RAM machine with a local tile selected that is NOT the recommended one. Only
  // a warning: Art. 14 posture is to inform the operator, never to silently install something else
  // (that silent substitution was the very defect this change removes).
  const localSelected = selected === "mistral" || selected === "llama" || selected === "phi";
  const ramOvershoot = !!ramRec && ramRec.lowRam && localSelected && !!recommendedTileId && selected !== recommendedTileId;
  const ramGbLabel = ramRec?.totalBytes ? `${Math.round(ramRec.totalBytes / 1024 ** 3)} GB` : null;

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
      void refreshModels(); // the model is now pulled — refresh the install badges/warning
    } catch {
      setError("Something went wrong — please try again.");
      setProgress(null);
    } finally {
      setBusy(false);
    }
  }

  /** #2715: is there anything to apply for this cloud provider?
   *
   *  Either the operator typed a key, OR one is already in the OS keychain — in which case the
   *  provider switch alone is a complete, meaningful action and needs no key at all. `applyCloud`
   *  does TWO things (store the key, switch `LLM_PROVIDER`); tying both to a non-empty field made
   *  the second unreachable for a provider whose key was already stored. */
  function canApplyCloud(o: CloudOpt): boolean {
    return !!key.trim() || keySlots[o.slot] === true;
  }

  async function applyCloud(o: CloudOpt) {
    const typed = key.trim();
    // Nothing typed AND nothing stored ⇒ genuinely nothing to do. The button is disabled in this
    // state and an explanatory line is rendered, so this is a defensive guard, not the UX.
    if (!typed && keySlots[o.slot] !== true) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      // Blank field + stored key = "keep the stored key" (what the placeholder promises). Skipping
      // the write is the point: it must not be able to fail, and the audit trail must not show a
      // credential rewrite that never happened.
      if (typed) {
        const r = await deps.saveSecret(o.slot, typed);
        if (!r.ok) {
          setError("Validated the key, but saving to the keychain failed.");
          return;
        }
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
      // Brief pause to allow the OS to release the port
      await new Promise((r) => setTimeout(r, 1000));
      await deps.startEngine();

      if (deps.checkEngineHealth) {
        // Poll for up to 30 seconds to ensure the new engine is fully reachable
        for (let i = 0; i < 30; i++) {
          await new Promise((r) => setTimeout(r, 1000));
          if (await deps.checkEngineHealth()) {
            break;
          }
        }
      }
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

      {/* #2130: the selected LOCAL model is configured but NOT actually pulled — say so loudly, so a
          selected-but-missing model never reads as ready. Cloud providers carry their own key flow. */}
      {currentLocalMissing && (
        <div
          role="alert"
          className="mb-5 flex items-start gap-2 px-4 py-3 rounded-xl bg-white/[0.05] border border-white/10"
        >
          {/* De-amber'd: the glyph and the model name carried #ffcc66. The house palette has no
              gold/amber (HitlPolicyCard: "no gold/amber; red for the high-autonomy state"), so the
              emphasis is weight, not colour. The box was already neutral. */}
          <span aria-hidden className="shrink-0 text-[13px] leading-none mt-0.5 text-white/70">
            ⚠
          </span>
          <span className="text-[12px] text-white/80 leading-relaxed">
            <span className="font-semibold text-white">{currentName}</span> is selected but{" "}
            <span className="font-semibold">isn&rsquo;t installed yet</span> — download it below. Until then the
            AI analysis can&rsquo;t run.
          </span>
        </div>
      )}

      {/* On your machine. The measured RAM is stated ONCE here, so each tile's badge can be the
          short neutral "Recommended" instead of a sentence that wraps inside a third-width tile.
          Omitted entirely when the reading is unavailable — then no tile is badged either. */}
      <div className="text-[10px] text-white/30 uppercase tracking-wider mb-2">
        On-Device Local Models — private
        {ramGbLabel ? ` · recommendation for ${ramGbLabel} RAM` : ""}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
        {LOCAL.map((o) => (
          <ProviderCard
            key={o.id}
            id={o.id}
            name={o.name}
            blurb={o.blurb}
            selected={selected === o.id}
            current={current === o.id}
            installed={llmChecked ? localModelInstalled(o, installedModels) : undefined}
            recommended={recommendedTileId === o.id}
            onSelect={() => pick(o.id)}
          >
            {selected === o.id && (
              <div className="mt-4 pt-4 border-t border-white/10">
                {/* #2706: the operator picked a heavier model than this machine is measured to
                    carry. Inform, never override — a silent substitution is the defect we removed. */}
                {ramOvershoot && (
                  <div
                    data-testid="ram-mismatch-warning"
                    className="mb-3 flex gap-2 items-start p-2.5 rounded-lg border border-white/10 bg-white/[0.05]"
                  >
                    <span aria-hidden className="shrink-0 text-[12px] leading-none mt-0.5 text-white/70">
                      ⚠
                    </span>
                    <span className="text-[11px] text-white/75 leading-relaxed">
                      This machine reports{" "}
                      <span className="font-semibold text-white num">{ramGbLabel ?? "limited"}</span> of RAM.{" "}
                      {o.name} holds several GB while running, on top of the engine, the app and your
                      browser — which can exhaust memory and freeze the app. We recommend{" "}
                      <span className="font-semibold">{TILE_NAME[recommendedTileId] ?? "the light model"}</span> here.
                      You can still continue.
                    </span>
                  </div>
                )}
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
                <SolidButton onClick={() => void applyLocal(o)} disabled={busy}>
                  {busy ? "Working…" : current === o.id ? `Re-download ${o.name}` : `Download & Activate ${o.name}`}
                </SolidButton>
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
            )}
          </ProviderCard>
        ))}
      </div>

      {/* Cloud providers */}
      <div className="text-[10px] text-white/30 uppercase tracking-wider mb-2">Cloud Provider Models — bring your own key</div>
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
          >
            {selected === o.id && (
              <div className="mt-4 pt-4 border-t border-white/10">
                <label className="block text-[11px] text-white/45 mb-1">{o.name} API key</label>
                <input
                  type="password"
                  aria-label={`${o.id}-key`}
                  className="w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30 disabled:opacity-40"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder={keySlots[o.slot] ? KEY_PRESENT_PLACEHOLDER : o.keyHint}
                  disabled={busy}
                  onClick={(e) => e.stopPropagation()}
                />
                <div className="mt-3 flex items-center gap-3">
                  {/* #2715: a blank field is legitimate when the key is ALREADY in the keychain —
                      that is exactly what the placeholder promises ("leave blank to keep"). Before,
                      this predicate was `!key.trim()` alone, so following that instruction left a
                      permanently dead button and the provider could never be switched. */}
                  <SolidButton onClick={() => void applyCloud(o)} disabled={busy || !canApplyCloud(o)}>
                    {busy ? "Saving…" : `Save & use ${o.name}`}
                  </SolidButton>
                </div>
                {/* #2715: never let a disabled control be the only feedback (CODING_POLICY §5.6 —
                    no silent failure). Only shown when there is genuinely nothing to apply. */}
                {!canApplyCloud(o) && !busy && (
                  <div data-testid="cloud-key-required" className="text-[11px] text-white/55 mt-2">
                    Enter the {o.name} API key to switch to it — no key is stored for this provider yet.
                  </div>
                )}
                {error && <div className="text-[11px] text-bear mt-2">{error}</div>}
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
            )}
          </ProviderCard>
        ))}
      </div>

      {/* How it works — same info-box idiom as the Execution-mode confirmation, with the lightbulb */}
      <div className="mt-5 flex items-start gap-2 px-4 py-3 rounded-lg bg-white/[0.05] border border-white/10">
        <IconLightbulb width={14} height={14} className="text-bull shrink-0 mt-0.5" />
        <span className="text-[12px] text-white/70 leading-relaxed">
          <span className="text-white/90 font-medium">How it works.</span> Your chosen model only drafts the
          analysis. Every trade still passes the deterministic risk gates, and orders execute according to your
          Autonomy setting — in Manual-approval mode every trade waits for your approval before it executes.
          Paper-trading is the default. {selectedName !== "—" ? "" : ""}
        </span>
      </div>
    </div>
  );
}
