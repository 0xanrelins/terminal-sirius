import { useEffect, useState } from "react";
import { secondsUntilBucketEnd } from "../../lib/barTime";
import styles from "./BarCountdown.module.css";

const INTERVAL = "15m";

function formatCountdown(totalSec: number): string {
  const clamped = Math.max(0, totalSec);
  const m = Math.floor(clamped / 60);
  const s = clamped % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function BarCountdown() {
  const [remaining, setRemaining] = useState(() =>
    secondsUntilBucketEnd(INTERVAL)
  );

  useEffect(() => {
    const tick = () => setRemaining(secondsUntilBucketEnd(INTERVAL));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);

  const urgency =
    remaining <= 30 ? "critical" : remaining <= 120 ? "urgent" : null;

  return (
    <div className={styles.ticker}>
      <span
        className={`${styles.countdown} ${urgency ? styles[urgency] : ""}`}
      >
        {formatCountdown(remaining)}
      </span>
    </div>
  );
}
