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
  const sessionSlugRef = useRef<string | null>(null);

  useEffect(() => {
    sessionSlugRef.current = null;
    prevPrice.current = null;
    setPrice(null);
    setFlash(null);
  }, [symbol, isPolymarket]);

  useEffect(() => {
    if (!isPolymarket) return;

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
      if (isPolymarket && msg.type === "polymarket") {
        const pm = msg as PolymarketMsg;
        if (pm.slug) {
          if (
            sessionSlugRef.current !== null &&
            pm.slug !== sessionSlugRef.current
          ) {
            sessionSlugRef.current = pm.slug;
            prevPrice.current = null;
            setPrice(null);
            setFlash(null);
          } else {
            sessionSlugRef.current = pm.slug;
          }
        }
      }

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
      <div className={styles.row}>
        <span className={`${styles.price} ${flash ? styles[flash] : ""}`}>
          {price ?? "—"}
        </span>
        <div className={styles.meta}>
          <span className={styles.symbol}>
            {isPolymarket && <span className={styles.pmBadge}>POLY</span>}
            {displayLabel}
          </span>
          <span className={`${styles.status} ${styles[status]}`}>{status}</span>
        </div>
      </div>
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
  return `${Math.round(n * 100)}%`;
}
