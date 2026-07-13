import { useEffect, useRef, useState, useCallback } from "react";
import { API_BASE } from "@/lib/api";

export interface EngineMessage {
    id: string;
    timestamp: number;
    message: string;
    type?: string;
}

const MAX_MESSAGES = 10;

/**
 * Subscribes to the engine's WebSocket endpoint /ws/updates.
 * Returns the most recent messages (up to MAX_MESSAGES) and connection status.
 * Automatically reconnects on disconnect with exponential backoff.
 */
export function useEngineWebSocket() {
    const [messages, setMessages] = useState<EngineMessage[]>([]);
    const [connected, setConnected] = useState(false);
    const wsRef = useRef<WebSocket | null>(null);
    const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const retryDelay = useRef(1000);

    const wsUrl = API_BASE
        .replace(/^https/, "wss")
        .replace(/^http/, "ws")
        + "/ws/updates";

    const connect = useCallback(function attemptConnection() {
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        try {
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            ws.onopen = () => {
                setConnected(true);
                retryDelay.current = 1000; // reset backoff on success
            };

            ws.onmessage = (event) => {
                try {
                    const data = typeof event.data === "string"
                        ? JSON.parse(event.data)
                        : event.data;

                    const msg: EngineMessage = {
                        id: crypto.randomUUID(),
                        timestamp: Date.now(),
                        message: data?.message ?? data?.thought ?? data?.text ?? JSON.stringify(data),
                        type: data?.type,
                    };

                    setMessages((prev) => [msg, ...prev].slice(0, MAX_MESSAGES));
                } catch {
                    // non-JSON message — treat as plain text
                    setMessages((prev) => [{
                        id: crypto.randomUUID(),
                        timestamp: Date.now(),
                        message: String(event.data),
                    }, ...prev].slice(0, MAX_MESSAGES));
                }
            };

            ws.onclose = () => {
                setConnected(false);
                wsRef.current = null;
                // Reconnect with exponential backoff (max 30s)
                retryRef.current = setTimeout(() => {
                    retryDelay.current = Math.min(retryDelay.current * 2, 30000);
                    attemptConnection();
                }, retryDelay.current);
            };

            ws.onerror = () => {
                ws.close();
            };
        } catch {
            // WebSocket not available in this environment — skip silently
        }
    }, [wsUrl]);

    useEffect(() => {
        connect();
        return () => {
            if (retryRef.current) clearTimeout(retryRef.current);
            wsRef.current?.close();
        };
    }, [connect]);

    return { messages, connected };
}
