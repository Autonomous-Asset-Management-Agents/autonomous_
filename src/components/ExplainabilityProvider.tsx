import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { useToast } from "@/components/ui/use-toast";
import { auth } from "@/lib/firebase";
import { getApiBase } from "@/lib/api";

interface ExplainabilityEvent {
    type: "trade_rejected" | "trade_executed" | "risk_halt" | "info";
    title: string;
    message: string;
    timestamp: string;
}

interface ExplainabilityContextType {
    events: ExplainabilityEvent[];
    isConnected: boolean;
}

const ExplainabilityContext = createContext<ExplainabilityContextType>({
    events: [],
    isConnected: false,
});

// eslint-disable-next-line react-refresh/only-export-components
export const useExplainability = () => useContext(ExplainabilityContext);

interface ProviderProps {
    children: ReactNode;
}

export const ExplainabilityProvider = ({ children }: ProviderProps) => {
    const [events, setEvents] = useState<ExplainabilityEvent[]>([]);
    const [isConnected, setIsConnected] = useState(false);
    const { toast } = useToast();

    useEffect(() => {
        let ws: WebSocket | null = null;
        let reconnectTimeout: NodeJS.Timeout;

        const connect = async () => {
            const user = auth.currentUser;
            if (!user) return;

            try {
                const token = await user.getIdToken();
                const apiBaseUrl = getApiBase();

                // Convert http/https to ws/wss
                const wsProtocol = apiBaseUrl.startsWith("https") ? "wss" : "ws";
                const wsHost = apiBaseUrl.replace(/^https?:\/\//, "");
                const wsUrl = `${wsProtocol}://${wsHost}/ws/explainability`;

                // Send the Firebase ID token via the Sec-WebSocket-Protocol
                // subprotocol list instead of a ?token= query param. Query
                // strings leak into proxy access logs, browser history, and
                // the Referer header; subprotocols do not.
                //
                // Contract with backend (serve_public_api.py):
                //   protocols[0] — marker "access_token.jwt.v1"
                //   protocols[1] — the raw Firebase ID token
                // Server echoes "access_token.jwt.v1" on accept.
                const WS_AUTH_SUBPROTOCOL = "access_token.jwt.v1";
                ws = new WebSocket(wsUrl, [WS_AUTH_SUBPROTOCOL, token]);

                ws.onopen = () => {
                    setIsConnected(true);
                };

                ws.onmessage = (event) => {
                    try {
                        const data: ExplainabilityEvent = JSON.parse(event.data);
                        setEvents((prev) => [data, ...prev].slice(0, 50)); // Keep last 50 events

                        // Show interactive toast for specific XAI events
                        if (data.type === "trade_rejected") {
                            toast({
                                title: `✋ ${data.title}`,
                                description: data.message,
                                variant: "destructive",
                                duration: 10000, // Show longer for reading
                            });
                        } else if (data.type === "risk_halt") {
                            toast({
                                title: `🛑 ${data.title}`,
                                description: data.message,
                                variant: "destructive",
                                duration: 10000,
                            });
                        } else if (data.type === "trade_executed") {
                            toast({
                                title: `✅ ${data.title}`,
                                description: data.message,
                            });
                        }
                    } catch (e) {
                        console.error("Failed to parse explainability event", e);
                    }
                };

                ws.onclose = () => {
                    setIsConnected(false);
                    // Auto-reconnect after 5 seconds
                    reconnectTimeout = setTimeout(connect, 5000);
                };
            } catch (error) {
                console.error("Failed to get token for WebSocket", error);
            }
        };

        // Firebase auth state observer to trigger connection
        const unsubscribe = auth.onAuthStateChanged((user) => {
            if (user) {
                connect();
            } else {
                if (ws) ws.close();
            }
        });

        return () => {
            unsubscribe();
            if (ws) ws.close();
            clearTimeout(reconnectTimeout);
        };
    }, [toast]);

    return (
        <ExplainabilityContext.Provider value={{ events, isConnected }}>
            {children}
        </ExplainabilityContext.Provider>
    );
};
