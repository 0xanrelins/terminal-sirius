import { useEffect, useState } from "react";
import styles from "./NewYorkTime.module.css";

const NY_TZ = "America/New_York";

const timeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: NY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function formatNewYorkTime(date: Date): string {
  return timeFormatter.format(date);
}

export function NewYorkTime() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const tick = () => setNow(new Date());
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className={styles.ticker}>
      <div className={styles.row}>
        <span className={styles.time}>{formatNewYorkTime(now)}</span>
        <span className={styles.label}>New York Time</span>
      </div>
    </div>
  );
}
