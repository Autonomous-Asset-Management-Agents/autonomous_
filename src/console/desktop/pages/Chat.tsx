import { useState, useRef, useEffect } from "react";
import { sendChat } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { IconChat } from "@/console/shared/Icons";

/**
 * Console Chat page (G3, #1050). Ported from the desktop bundle.
 *
 * Talks to the engine through the shared `@/lib/api` layer, which carries the
 * desktop X-Engine-Key automatically (see desktopBridge + api.ts) — so this one
 * component works unchanged in both the cloud build and the Electron shell.
 *
 * Transcript lives in the console store so it survives sidebar navigation.
 *
 * Error handling is deterministic, never content-parsing: `sendChat` returns
 * null on a transport/parse failure → we show a "can't reach the engine" line.
 * The engine's reply itself is rendered verbatim — the frontend must NOT parse
 * the LLM's prose to second-guess it (any unhelpful reply text is a backend
 * concern, fixed engine-side, not papered over here).
 */
export function Chat() {
  const messages = useStore((s) => s.chatMessages);
  const addMessage = useStore((s) => s.addChatMessage);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, sending]);

  async function submit() {
    const text = input.trim();
    if (!text || sending) return;
    addMessage("user", text);
    setInput("");
    setSending(true);
    const reply = await sendChat(text);
    setSending(false);
    // reply === null is the deterministic transport-failure signal (sendChat
    // returns null on a fetch/parse error). Otherwise render it verbatim.
    addMessage("assistant", reply ?? "Couldn't reach the engine — is it running?");
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-8 py-6 border-b border-white/5">
        <div className="eyebrow mb-2">Chat</div>
        <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">System communication</h1>
        <p className="text-white/45 text-[13px] mt-1.5 max-w-xl">
          Ask the engine about your portfolio, market state, a symbol, or a recent decision.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-4">
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center text-white/30">
            <IconChat width={28} height={28} className="text-white/20 mb-3" />
            <div className="text-[13px]">No messages yet.</div>
            <div className="text-[12px] text-white/20 mt-1">
              Try: “summarize my portfolio” · “is the market open?” · “how many positions?”
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[72%] rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap ${
                m.role === "user"
                  ? "bg-[#00c27a]/15 border border-[#00c27a]/25 text-white/92"
                  : "bg-white/[0.04] border border-white/8 text-white/80"
              }`}
            >
              {m.text}
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex justify-start">
            <div className="rounded-2xl px-4 py-2.5 bg-white/[0.04] border border-white/8 text-white/40 text-[13.5px]">…</div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="px-8 py-5 border-t border-white/5">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            rows={1}
            placeholder="Message the engine…"
            className="flex-1 resize-none rounded-xl bg-white/[0.04] border border-white/10 px-4 py-2.5 text-[13.5px] text-white/92 placeholder:text-white/25 focus:outline-none focus:border-white/25 max-h-32"
          />
          <button
            onClick={() => void submit()}
            disabled={!input.trim() || sending}
            className="shrink-0 rounded-xl px-4 py-2.5 text-[13px] font-semibold bg-[#00c27a] text-black disabled:opacity-40 disabled:cursor-not-allowed hover:bg-[#00d886] transition-colors"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
