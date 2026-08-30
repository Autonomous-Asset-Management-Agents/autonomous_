import { useState } from "react";
import { activateLicense } from "@/lib/api";

/**
 * LicenseActivation (GTM-2 #1809 T4) — the offline key-paste panel of the paid
 * Private/Senior path. The user pastes the signed key they received after checkout; the
 * LOCAL engine verifies the Ed25519 signature OFFLINE (no login, no connection to our
 * servers — BaFin-safe) and, only on a pass, persists it so the tier flips on the next
 * poll. It never fails silently: every outcome surfaces a visible note, and a blank key
 * is refused client-side (no pointless round-trip). Styled with the sanctioned console
 * idioms (surface + #00c27a green, no gold/amber).
 */
export function LicenseActivation({
  refetch,
  onActivated,
}: {
  refetch: () => Promise<void>;
  onActivated?: () => void;
}) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "err" | "hint"; msg: string } | null>(null);

  const onActivate = async () => {
    if (busy) return;
    const token = key.trim();
    if (!token) {
      setNote({ tone: "hint", msg: "Paste your license key first." });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      const res = await activateLicense(token);
      if (res.ok) {
        setNote({ tone: "ok", msg: "✓ Verified offline — Senior is now active." });
        await refetch();
        onActivated?.();
      } else {
        setNote({
          tone: "err",
          msg: "That key could not be verified. Check you pasted it in full, or that it has not expired.",
        });
      }
    } catch {
      setNote({
        tone: "err",
        msg: "That key could not be verified. Check you pasted it in full, or that it has not expired.",
      });
    } finally {
      setBusy(false);
    }
  };

  const noteClass =
    note?.tone === "ok" ? "text-[#00c27a]" : note?.tone === "err" ? "text-[#ff6b61]" : "text-white/55";

  return (
    <div className="mt-3 rounded-xl border border-white/10 bg-[#00c27a]/[0.04] p-4">
      <h4 className="text-[13px] font-semibold text-white mb-1">Activate Senior</h4>
      <p className="text-[11.5px] text-white/40 mb-2.5 leading-relaxed">
        Paste the license key from your purchase email. It is <strong className="text-white/60">verified
        offline</strong> on this machine — no login, no connection to our servers. Valid one year.
      </p>
      <textarea
        value={key}
        onChange={(e) => setKey(e.target.value)}
        placeholder="AAA-SENIOR-… paste your license key here"
        spellCheck={false}
        className="w-full min-h-[80px] resize-y rounded-lg border border-white/10 bg-black/40 p-2.5 font-mono text-[11px] leading-relaxed text-white/85 placeholder:text-white/25 outline-none focus:border-[#00c27a]/40"
      />
      <div className="mt-2.5 flex items-center gap-3">
        <button
          onClick={() => void onActivate()}
          disabled={busy}
          className="rounded-lg px-5 py-2 text-[12px] font-bold tracking-wide text-white bg-[#00c27a] hover:bg-[#00d687] border border-transparent transition-all transform active:scale-[0.98] disabled:opacity-60"
        >
          {busy ? "ACTIVATING…" : "Activate"}
        </button>
        {note && <span className={`text-[11.5px] ${noteClass}`}>{note.msg}</span>}
      </div>
    </div>
  );
}
