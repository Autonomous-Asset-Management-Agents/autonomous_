import { AUTHORIZATION_TEXT, LEARN_MORE_URL } from "./brokerAuthorization";

/**
 * #5135 (GTM-1): Alpaca's authorization prompt, shown before any Alpaca key is validated or stored.
 * The wording is rendered verbatim from AUTHORIZATION_TEXT (one block per paragraph). Two buttons as
 * in Alpaca's template, no checkbox. "learn more about autonomous_" opens in the default browser
 * (main.cjs setWindowOpenHandler → shell.openExternal, #2367).
 */
const [TITLE, PARA_1, PARA_2] = AUTHORIZATION_TEXT.split("\n\n");
const LINK_PHRASE = "learn more about autonomous_";
const [PARA_2_HEAD, PARA_2_TAIL] = [
  PARA_2.slice(0, PARA_2.lastIndexOf(LINK_PHRASE)),
  PARA_2.slice(PARA_2.lastIndexOf(LINK_PHRASE) + LINK_PHRASE.length),
];

const BUTTON =
  "rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase border-none transition-all transform active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed";

export function BrokerAuthorizationDialog({
  onAnswer,
  busy = false,
}: {
  onAnswer: (answer: "allow" | "deny") => void;
  busy?: boolean;
}) {
  return (
    <div className="aaa-console fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="broker-authorization-title"
        className="w-full max-w-md rounded-2xl border border-white/10 shadow-2xl bg-[#1c1c1e] p-6 space-y-4"
      >
        <h3 id="broker-authorization-title" className="text-lg font-bold text-white leading-tight">
          {TITLE}
        </h3>
        <p className="text-[13px] text-white/75 leading-relaxed">{PARA_1}</p>
        <p className="text-[13px] text-white/75 leading-relaxed">
          {PARA_2_HEAD}
          <a href={LEARN_MORE_URL} target="_blank" rel="noreferrer" className="text-[#00c27a] underline">
            {LINK_PHRASE}
          </a>
          {PARA_2_TAIL}
        </p>
        <div className="flex justify-end gap-3 pt-2">
          <button className={`${BUTTON} text-white/80 bg-white/10 hover:bg-white/15`} onClick={() => onAnswer("deny")} disabled={busy}>
            DENY
          </button>
          <button className={`${BUTTON} text-white bg-[#00c27a] hover:bg-[#00d687]`} onClick={() => onAnswer("allow")} disabled={busy}>
            ALLOW
          </button>
        </div>
      </div>
    </div>
  );
}
