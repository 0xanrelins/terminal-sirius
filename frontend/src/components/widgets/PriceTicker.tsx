import { useEffect, useRef, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import type { FeedMsg, PolymarketMsg } from "../../types";
import styles from "./PriceTicker.module.css";

type Props = {
  symbol: string;
  source?: "binance" | "polymarket";
  label?: string;
};

export function PriceTicker({ symbol, source, label }: Props) {
  const isPolymarket = source === "polymarket" || symbol.endsWith(".POLYMARKET");
  const { subscribe, status } = useFeed();
  const [price, setPrice] = useState<string | null>(null);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const prevPrice = useRef<number | null>(null);

  useEffect(() => {
    if (!isPolymarket) return;
    const series = symbol.replace(".POLYMARKET", "");
    fetch("/polymarket/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ series }),
    }).catch(() => {});

    fetch("/polymarket/presets")
      .then((r) => r.json())
      .then((presets: { symbol: string; yes_price: number | null }[]) => {
        const match = presets.find((p) => p.symbol === symbol);
        if (match?.yes_price != null) {
          prevPrice.current = match.yes_price;
          setPrice(formatPolymarket(match.yes_price));
        }
      })
      .catch(() => {});
  }, [symbol, isPolymarket]);

  useEffect(() => {
    return subscribe(symbol, (msg: FeedMsg) => {
      const next = extractPrice(msg, isPolymarket);
      if (next === null) return;

      const prev = prevPrice.current;
      if (prev !== null) {
        setFlash(next > prev ? "up" : next < prev ? "down" : null);
        setTimeout(() => setFlash(null), 300);
      }

      prevPrice.current = next;
      setPrice(isPolymarket ? formatPolymarket(next) : formatPrice(next));
    });
  }, [symbol, subscribe, isPolymarket]);

  const displayLabel = isPolymarket
    ? `${label ?? symbol.replace(".POLYMARKET", "").split("-")[0].toUpperCase()} UP 15m`
    : `${symbol.replace("-PERP.BINANCE", "")} PERP`;

  return (
    <div className={styles.ticker}>
      <span className={styles.symbol}>
        {isPolymarket && <span className={styles.pmBadge}>POLY</span>}
        {displayLabel}
      </span>
      <span className={`${styles.price} ${flash ? styles[flash] : ""}`}>
        {price ?? "—"}
      </span>
      <span className={`${styles.status} ${styles[status]}`}>{status}</span>
    </div>
  );
}

function extractPrice(msg: FeedMsg, polymarket: boolean): number | null {
  if (polymarket) {
    if (msg.type !== "polymarket") return null;
    return (msg as PolymarketMsg).yes_price;
  }
  if (msg.type === "trade") return parseFloat(msg.price);
  if (msg.type === "quote") return (parseFloat(msg.bid) + parseFloat(msg.ask)) / 2;
  return null;
}

function formatPrice(n: number): string {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPolymarket(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}
