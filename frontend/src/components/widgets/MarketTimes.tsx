import { useEffect, useState } from "react";
import {
  getMarketRowState,
  MARKET_SESSIONS,
} from "../../lib/marketSessions";
import styles from "./MarketTimes.module.css";

export function MarketTimes() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className={styles.root}>
      <div className={styles.list}>
        {MARKET_SESSIONS.map((session) => {
          const row = getMarketRowState(session, now);
          const showProgress =
            row.isOpen && row.progress01 != null && row.progress01 > 0;

          return (
            <div key={session.id} className={styles.row}>
              <div className={styles.left}>
                <span
                  className={`${styles.dot} ${
                    row.isOpen ? styles.dotOpen : styles.dotClosed
                  }`}
                />
                <div className={styles.meta}>
                  <div className={styles.name}>
                    {session.city} [{session.exchange}]
                  </div>
                  <div className={styles.localTime}>
                    {row.localTimeHHmm}
                  </div>
                </div>
              </div>
              <div className={styles.right}>
                <div
                  className={
                    row.isOpen ? styles.statusOpen : styles.statusClosed
                  }
                >
                  {row.statusLabel}
                </div>
                <div className={styles.countdown}>{row.countdownLabel}</div>
              </div>
              {showProgress && (
                <div className={styles.progressTrack}>
                  <div
                    className={styles.progressFill}
                    style={{ width: `${row.progress01! * 100}%` }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
