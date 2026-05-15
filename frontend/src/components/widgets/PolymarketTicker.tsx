import { useEffect, useRef, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import type { PolymarketMsg } from "../../types";
import styles from "./PolymarketTicker.module.css";

type Props = { symbol: string; question: string };

export function PolymarketTicker({ symbol, question }: Props) {
  const { subscribe, status } = useFeed();
  const [yesPrice, setYesPrice] = useState<number | null>(null);
  const [bid, setBid] = useState<number | null>(null);
  const [ask, setAsk] = useState<number | null>(null);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const prev = useRef<number | null>(null);

  useEffect(() => {
    // Fetch initial price from Gamma REST so widget shows data before WS arrives
    const slug = symbol.replace(".POLYMARKET", "");
    fetch(`/polymarket/markets?q=${encodeURIComponent(slug)}&limit=5`)
      .then((r) => r.json())
      .then((markets: { slug: string; yes_price: number | null }[]) => {
        const match = markets.find((m) => m.slug === slug);
        if (match?.yes_price != null) setYesPrice(match.yes_price);
      })
      .catch(() => {});
  }, [symbol]);

  useEffect(() => {
    return subscribe(symbol, (msg) => {
      if (msg.type !== "polymarket") return;
      const pm = msg as PolymarketMsg;

      if (pm.bid != null) setBid(pm.bid);
      if (pm.ask != null) setAsk(pm.ask);

      const next = pm.yes_price;
      if (next == null) return;

      if (prev.current !== null) {
        setFlash(next > prev.current ? "up" : next < prev.current ? "down" : null);
        setTimeout(() => setFlash(null), 400);
      }
      prev.current = next;
      setYesPrice(next);
    });
  }, [symbol, subscribe]);

  const pct = yesPrice !== null ? (yesPrice * 100).toFixed(1) : null;
  const isLikely = yesPrice !== null && yesPrice >= 0.5;

  return (
    <div className={styles.ticker}>
      <div className={styles.badge}>POLYMARKET</div>
      <p className={styles.question}>{question}</p>

      <div className={styles.priceRow}>
        <span className={`${styles.yes} ${flash ? styles[flash] : ""} ${isLikely ? styles.likely : styles.unlikely}`}>
          YES {pct !== null ? `${pct}%` : "—"}
        </span>
        <span className={styles.no}>
          NO {pct !== null ? `${(100 - parseFloat(pct)).toFixed(1)}%` : "—"}
        </span>
      </div>

      {bid != null && ask != null && (
        <div className={styles.spread}>
          <span className={styles.bid}>{(bid * 100).toFixed(1)}¢</span>
          <span className={styles.sep}>/</span>
          <span className={styles.ask}>{(ask * 100).toFixed(1)}¢</span>
        </div>
      )}

      <span className={`${styles.status} ${styles[status]}`}>{status}</span>
    </div>
  );
}
