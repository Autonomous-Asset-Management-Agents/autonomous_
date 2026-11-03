import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Send, MessageCircle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getApiBase, startLive, stop, sendChat } from "@/lib/api";
import { isPublicViewOnly } from "@/lib/publicMode";

interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: Date;
}

interface ChatPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export const ChatPanel = ({ isOpen, onClose }: ChatPanelProps) => {
  const publicInfoOnly = isPublicViewOnly();
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "1",
      role: "system",
      content: publicInfoOnly
        ? "Welcome. Ask about portfolio, strategy, news, market trends, or earnings. This chat is for information only—no trading commands."
        : "Welcome to AAA Trading Bot. Ask about your portfolio, strategy, news, market trends, earnings, or any trading-related question.",
      timestamp: new Date(),
    },
    {
      id: "1b",
      role: "system",
      content: "This is a prototype of this function and has its bugs. We are working on perfecting it—it’s more of a tech demonstration of what we plan to implement.",
      timestamp: new Date(),
    },
  ]);
  const [input, setInput] = useState("");
  const [isConnected, setIsConnected] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Connect to WebSocket for real-time updates (not used in public view-only; proxy has no WS)
  useEffect(() => {
    if (!isOpen || publicInfoOnly) return;

    const wsUrl = getApiBase().replace("http", "ws") + "/ws/updates";
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: "system",
          content: publicInfoOnly
            ? "🟢 Connected. Ask anything about portfolio, strategy, news, or market insights."
            : "🟢 Connected. Ask about portfolio, strategy, news, trends, earnings, or general market questions.",
          timestamp: new Date(),
        },
      ]);
    };

    ws.onmessage = () => {
      // Only use WebSocket for connection liveness; do not show engine log/thought lines in chat
      setIsConnected(true);
    };

    ws.onerror = () => {
      setIsConnected(false);
    };

    ws.onclose = () => {
      setIsConnected(false);
      // Only add one "Disconnected" message to avoid spam when reconnecting
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.content?.includes("Disconnected")) return prev;
        return [
          ...prev,
          {
            id: Date.now().toString(),
            role: "system",
            content: "🔴 Disconnected from engine. Start the engine to reconnect.",
            timestamp: new Date(),
          },
        ];
      });
    };

    return () => {
      ws.close();
    };
  }, [isOpen, publicInfoOnly]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim()) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: "user",
      content: input.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsTyping(true);

    const command = input.toLowerCase().trim();
    let response = "";

    try {
      // In public view-only mode: only info via /chat—no start/stop or other commands
      const useChatOnly = publicInfoOnly;
      if (!useChatOnly && command.includes("start")) {
        // Desktop: /start-live is require_engine_key-gated. The raw fetch here sent
        // NO X-Engine-Key → 403, so "start" silently never started the strategy.
        // Use the authenticated api.ts helper (adds the key via fetchJson).
        const data = await startLive();
        response = data.status === "success" ? "✅ Live trading started!" : "Failed to start trading.";
      } else if (!useChatOnly && command.includes("stop")) {
        const data = await stop();
        response = data.status === "success" ? "⏹️ Trading stopped." : "Failed to stop trading.";
      } else {
        // /chat: portfolio, strategy, help, "why did you buy MRNA?", etc. (and in public mode, all messages)
        const reply = await sendChat(input.trim());
        response = reply ?? "I couldn't get an answer. Try asking about portfolio, strategy, news, or any market question.";
      }
    } catch (e) {
      response = publicInfoOnly
        ? "⚠️ Insights are temporarily unavailable. Please try again later."
        : "⚠️ Could not reach the engine. Make sure it's running on port 8001.";
    }

    setIsTyping(false);
    setMessages((prev) => [
      ...prev,
      {
        id: (Date.now() + 1).toString(),
        role: "assistant",
        content: response,
        timestamp: new Date(),
      },
    ]);
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, x: "100%" }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: "100%" }}
          transition={{ type: "spring", damping: 25, stiffness: 300 }}
          className="fixed top-0 right-0 h-full w-full sm:w-96 md:w-[420px] bg-card/95 backdrop-blur-lg border-l border-border z-50 flex flex-col"
        >
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-border">
            <div className="flex items-center gap-2">
              <MessageCircle className="w-5 h-5" />
              <span className="font-display text-lg">AAA Chat</span>
              <span className={`w-2 h-2 rounded-full ${isConnected ? "bg-success" : "bg-destructive"}`} />
            </div>
            <Button variant="ghost" size="icon" onClick={onClose}>
              <X className="w-5 h-5" />
            </Button>
          </div>

          {/* Messages */}
          <ScrollArea className="flex-1 p-4">
            <div className="space-y-4" ref={scrollRef}>
              {messages.map((message) => (
                <motion.div
                  key={message.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                      message.role === "user"
                        ? "bg-foreground text-background"
                        : message.role === "system"
                        ? "bg-muted/50 text-muted-foreground"
                        : "bg-muted text-foreground"
                    }`}
                  >
                    {message.content}
                  </div>
                </motion.div>
              ))}
              {isTyping && (
                <div className="flex justify-start">
                  <div className="bg-muted rounded-lg px-3 py-2 text-sm flex items-center gap-2">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    Thinking...
                  </div>
                </div>
              )}
            </div>
          </ScrollArea>

          {/* Input */}
          <div className="p-4 border-t border-border">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSend();
              }}
              className="flex gap-2"
            >
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Portfolio, news, trends, earnings..."
                className="flex-1 bg-input border-border text-sm"
              />
              <Button type="submit" size="icon" className="bg-foreground text-background hover:bg-foreground/90">
                <Send className="w-4 h-4" />
              </Button>
            </form>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
