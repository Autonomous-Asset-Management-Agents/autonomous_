import { useState } from "react";
import { sendChatMessage, type ChatMessage } from "@/lib/chatClient";

export type { ChatMessage };

export function useChat(initialLog: ChatMessage[] = []) {
    const [chatInput, setChatInput] = useState("");
    const [chatLog, setChatLog] = useState<ChatMessage[]>(initialLog);
    const [chatBusy, setChatBusy] = useState(false);

    const submitChat = async (e?: React.FormEvent) => {
        if (e) {
            e.preventDefault();
        }
        const text = chatInput.trim();
        if (!text || chatBusy) return;
        
        setChatBusy(true);
        setChatLog((l) => [...l, { role: "user", content: text }]);
        setChatInput("");
        
        try {
            const reply = await sendChatMessage(text);
            setChatLog((l) => [...l, reply]);
        } finally {
            setChatBusy(false);
        }
    };

    return {
        chatInput,
        setChatInput,
        chatLog,
        chatBusy,
        submitChat
    };
}
