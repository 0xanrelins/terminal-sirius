import { useEffect, useRef, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import type { FeedMsg } from "../../types";
import styles from "./PriceTicker.module.css";

type Props = { symbol: string };

export function PriceTicker({ symbol }: Props) {
  const { subscribe, status } = useFeed();
  const [price, setPrice] = useState<string | null>(null);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const prevPrice = useRef<number | null>(null);

  useEffect(() => {
    return subscribe(symbol, (msg: FeedMsg) => {
      const next = extractPrice(msg);
      if (next === null) return;

      const prev = prevPrice.current;
      if (prev !== null) {
        setFlash(next > prev ? "up" : next < prev ? "down" : null);
        setTimeout(() => setFlash(null), 300);
      }

      prevPrice.current = next;
      setPrice(formatPrice(next));
    });
  }, [symbol, subscribe]);

  return (
    <div className={styles.ticker}>
      <span className={styles.symbol}>{symbol.replace("-PERP.BINANCE", "")} PERP</span>
      <span className={`${styles.price} ${flash ? styles[flash] : ""}`}>
        {price ?? "—"}
      </span>
      <span className={`${styles.status} ${styles[status]}`}>{status}</span>
    </div>
  );
}

function extractPrice(msg: FeedMsg): number | null {
  if (msg.type === "trade") return parseFloat(msg.price);
  if (msg.type === "quote") return (parseFloat(msg.bid) + parseFloat(msg.ask)) / 2;
  return null;
}

function formatPrice(n: number): string {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
