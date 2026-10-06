import { useStore } from "@/console/store/useStore";
import { isDesktop } from "@/lib/desktopBridge";
import { BrokerKeysCard } from "@/console/desktop/BrokerKeysCard";

/**
 * "Trading-Schlüssel" card (Settings → System tab, directly under the AI-model card).
 *
 * The Alpaca Paper/Live API keys, SEPARATED out of the Trading tab's Trading-Account card
 * (#2863) so the Trading tab stays about trading behaviour and all credential entry lives
 * together under System. Keychain storage is completely unchanged — the same
 * <BrokerKeysCard> slots, only the card location moved. The live-key slot stays locked for
 * Junior (allow_live=false), exactly as before.
 */
export function TradingKeysCard() {
  const allowLive = useStore((s) => s.allowLive);
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-2">Trading keys</div>
      <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        Deine Alpaca-Zugangsdaten für Paper- und Live-Handel — sicher im
        System-Schlüsselbund gespeichert.
      </p>
      <BrokerKeysCard mode="paper" embedded />
      <BrokerKeysCard mode="live" embedded locked={allowLive === false} />
    </div>
  );
}
