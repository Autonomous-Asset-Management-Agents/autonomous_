import { useCallback, useRef, useState, type ReactNode } from "react";
import { recordBrokerAuthorization } from "@/lib/desktopBridge";
import { BrokerAuthorizationDialog } from "@/console/desktop/BrokerAuthorizationDialog";
import { AUTHORIZATION_TEXT, AUTHORIZATION_VERSION, maskKeyId } from "@/console/desktop/brokerAuthorization";

/**
 * #5155 (GTM-1): Alpaca's authorization prompt for every place the app takes Alpaca credentials
 * (setup wizard, Settings broker keys, further paper accounts, the live switch). Lifted out of the
 * #5135 wizard so the fail-closed core lives once:
 *
 * - `authorize(slot, keyId)` opens the dialog and resolves AFTER the answer is written via the
 *   bridge (one file per answer, sealed on the WORM chain on the next engine boot).
 * - A write error (ok:false or a throw) resolves `"error"` — callers treat everything but `"allow"`
 *   as a stop: nothing is validated, stored or connected.
 * - Text, version and the "••••" key mask are set HERE; a caller can neither pass a copy of the text
 *   nor the clear-text key id.
 * - `dialog` is the open dialog (or null); the caller renders it in its own tree.
 *
 * `src/test/brokerAuthorizationCallSites.test.ts` fails for any file that takes Alpaca credentials
 * without importing this hook.
 */

/** The account slots of `ai_trading_bot/core/keychain.py::PLAETZE` (desktop: paper1–paper3, live).
 *  The shell (`desktop/electron/broker-authorization.cjs`) refuses any other name. */
export type BrokerAuthorizationSlot = "paper1" | "paper2" | "paper3" | "live";

export type BrokerAuthorizationResult = "allow" | "deny" | "error";

interface Pending {
  slot: BrokerAuthorizationSlot;
  keyId: string;
  resolve: (r: BrokerAuthorizationResult) => void;
}

export function useBrokerAuthorization(): {
  authorize: (slot: BrokerAuthorizationSlot, keyId: string) => Promise<BrokerAuthorizationResult>;
  dialog: ReactNode;
} {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const pending = useRef<Pending | null>(null);

  const authorize = useCallback(
    (slot: BrokerAuthorizationSlot, keyId: string) =>
      new Promise<BrokerAuthorizationResult>((resolve) => {
        // A second call while one is open supersedes it; the first one stops (fail-closed).
        pending.current?.resolve("error");
        pending.current = { slot, keyId, resolve };
        setBusy(false);
        setOpen(true);
      }),
    [],
  );

  async function answer(a: "allow" | "deny") {
    const p = pending.current;
    if (!p) return;
    setBusy(true);
    let ok: boolean;
    try {
      const rec = await recordBrokerAuthorization({
        answer: a,
        slot: p.slot,
        keyMasked: maskKeyId(p.keyId),
        text: AUTHORIZATION_TEXT,
        version: AUTHORIZATION_VERSION,
      });
      ok = rec?.ok === true;
    } catch {
      ok = false;
    }
    if (pending.current !== p) return; // superseded meanwhile — already resolved
    pending.current = null;
    setOpen(false);
    setBusy(false);
    p.resolve(ok ? a : "error");
  }

  const dialog = open ? <BrokerAuthorizationDialog onAnswer={(a) => void answer(a)} busy={busy} /> : null;
  return { authorize, dialog };
}
