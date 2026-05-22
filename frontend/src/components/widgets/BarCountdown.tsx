import { useEffect, useState } from "react";
import {
  currentBarBucket,
  INTERVAL_SECONDS,
  secondsUntilBucketEnd,
} from "../../lib/barTime";
import styles from "./BarCountdown.module.css";

const INTERVAL = "15m";

function formatCountdown(totalSec: number): string {
  const clamped = Math.max(0, totalSec);
  const m = Math.floor(clamped / 60);
  const s = clamped % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatUtcHHmm(unixSec: number): string {
  return new Date(unixSec * 1000).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
  });
}

export function BarCountdown() {
  const [remaining, setRemaining] = useState(() =>
    secondsUntilBucketEnd(INTERVAL)
  );
  const [bucketOpen, setBucketOpen] = useState(() => currentBarBucket(INTERVAL));

  useEffect(() => {
    const tick = () => {
      setRemaining(secondsUntilBucketEnd(INTERVAL));
      setBucketOpen(currentBarBucket(INTERVAL));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);

  const barSec = INTERVAL_SECONDS[INTERVAL] ?? 900;
  const bucketClose = bucketOpen + barSec;
  const urgency =
    remaining <= 30 ? "critical" : remaining <= 120 ? "urgent" : null;

  return (
    <div className={styles.ticker}>
      <span className={styles.symbol}>15m bar close</span>
      <span
        className={`${styles.countdown} ${urgency ? styles[urgency] : ""}`}
      >
        {formatCountdown(remaining)}
      </span>
      <span className={styles.window}>
        {formatUtcHHmm(bucketOpen)}–{formatUtcHHmm(bucketClose)} utc
      </span>
    </div>
  );
}
