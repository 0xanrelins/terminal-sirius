import { useEffect, useRef, useState } from "react";
import type { FeedMsg } from "../types";

type Status = "connecting" | "open" | "closed" | "error";

export function useWebSocket(symbols: string[]) {
  const [lastMsg, setLastMsg] = useState<FeedMsg | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (symbols.length === 0) return;

    const symbolParam = symbols.join(",");
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?symbols=${encodeURIComponent(symbolParam)}`;

    function connect() {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      setStatus("connecting");

      ws.onopen = () => setStatus("open");

      ws.onmessage = (e) => {
        try {
          setLastMsg(JSON.parse(e.data) as FeedMsg);
        } catch {
          // malformed frame — ignore
        }
      };

      ws.onclose = () => {
        setStatus("closed");
        reconnectTimer.current = setTimeout(connect, 2000);
      };

      ws.onerror = () => {
        setStatus("error");
        ws.close();
      };
    }

    connect();

    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [symbols.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  return { lastMsg, status };
}
