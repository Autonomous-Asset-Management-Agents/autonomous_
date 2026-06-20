/**
 * Chat client for the public landing.
 *
 * TODO(integration): point at the real Cloud Run engine via VITE_PUBLIC_API_URL
 * and implement server-side rate limiting + read-only enforcement before going
 * live. Until then this module returns canned demo responses so the hero chat
 * feels alive during local development.
 */
import { getPublicApiBase } from "@/lib/publicMode";

export interface ChatMessage {
    role: "user" | "agent";
    agentName?: string;
    content: string;
}

const MOCK_RESPONSES: Array<{ keyword: RegExp; reply: Omit<ChatMessage, "role"> }> = [
    { keyword: /burry|valuation/i, reply: { agentName: "burry", content: "valuation stretched vs 5y mean · P/S 32×" } },
    { keyword: /drawdown|risk/i, reply: { agentName: "risk", content: "max drawdown cap: 8% · current: 2.1%" } },
    { keyword: /buy|position|nvda|trade/i, reply: { agentName: "coord", content: "top pick: NVDA · 4% target · senate passed 7/9" } },
    { keyword: /senate|vote/i, reply: { agentName: "senate", content: "last vote: NVDA long 4% · 7 yes, 2 no · quorum reached" } },
    { keyword: /fee|pricing|cost/i, reply: { agentName: "info", content: "no management fee in beta · performance fee tbd" } },
];

const DEFAULT_REPLY: Omit<ChatMessage, "role"> = {
    agentName: "analyst",
    content: "i only discuss positions, risk and votes here. try: \"why did burry vote no?\"",
};

export async function sendChatMessage(userText: string): Promise<ChatMessage> {
    const base = getPublicApiBase();
    if (base) {
        try {
            const res = await fetch(`${base}/public/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: userText }),
            });
            if (res.ok) {
                const data = await res.json();
                return { role: "agent", agentName: data.agent ?? "agent", content: String(data.content ?? "") };
            }
        } catch {
            // fall through to mock on any network/backend error — keeps UX alive
        }
    }
    // Local mock (no VITE_PUBLIC_API_URL set, or backend unreachable).
    await new Promise((r) => setTimeout(r, 400 + Math.random() * 300));
    const match = MOCK_RESPONSES.find((m) => m.keyword.test(userText));
    const pick = match?.reply ?? DEFAULT_REPLY;
    return { role: "agent", agentName: pick.agentName, content: pick.content };
}
