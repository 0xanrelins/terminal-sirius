/**
 * FeedContext — one WebSocket connection shared by all widgets.
 *
 * Widgets call `useFeed().subscribe(symbol, handler)` to receive messages
 * for a specific symbol. The subscription is automatically cleaned up on unmount.
 *
 * Message routing: incoming WS messages are dispatched by `msg.symbol` to all
 * registered handlers for that symbol. Handlers that match any symbol ("*")
 * receive every message.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { FeedMsg } from "../types";

export type WsStatus = "connecting" | "open" | "closed" | "error";
type Handler = (msg: FeedMsg) => void;

interface FeedContextValue {
  subscribe: (symbol: string, handler: Handler) => () => void;
  status: WsStatus;
}

const FeedContext = createContext<FeedContextValue | null>(null);

export function FeedProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<WsStatus>("connecting");
  const subs = useRef<Map<string, Set<Handler>>>(new Map());
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    function connect() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      wsRef.current = ws;
      setStatus("connecting");

      ws.onopen = () => setStatus("open");

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data) as FeedMsg;
          dispatch(msg);
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        setStatus("closed");
        reconnectRef.current = setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        setStatus("error");
        ws.close();
      };
    }

    connect();

    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
  }, []);

  function dispatch(msg: FeedMsg) {
    const sym = "symbol" in msg && msg.symbol ? msg.symbol : undefined;
    if (sym) subs.current.get(sym)?.forEach((h) => h(msg));
    subs.current.get("*")?.forEach((h) => h(msg));
  }

  const subscribe = useCallback((symbol: string, handler: Handler): (() => void) => {
    if (!subs.current.has(symbol)) subs.current.set(symbol, new Set());
    subs.current.get(symbol)!.add(handler);
    return () => subs.current.get(symbol)?.delete(handler);
  }, []);

  return (
    <FeedContext.Provider value={{ subscribe, status }}>
      {children}
    </FeedContext.Provider>
  );
}

export function useFeed(): FeedContextValue {
  const ctx = useContext(FeedContext);
  if (!ctx) throw new Error("useFeed must be used inside <FeedProvider>");
  return ctx;
}
