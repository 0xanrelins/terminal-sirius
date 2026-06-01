"""Bar open-time alignment (Binance / Lightweight Charts convention)."""

INTERVAL_SECONDS: dict[str, int] = {
    "1s": 1,
    "5s": 5,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259_200,
    "1w": 604_800,
}


def bar_open_time(time_sec: int, interval: str) -> int:
    """Map any bar timestamp (open or close) to its bucket open time in seconds."""
    bar_sec = INTERVAL_SECONDS.get(interval, 60)
    return (time_sec // bar_sec) * bar_sec


def bar_open_time_ns(ts_ns: int, interval: str) -> int:
    return bar_open_time(ts_ns // 1_000_000_000, interval)


def is_aligned_open_time(time_sec: int, interval: str) -> bool:
    bar_sec = INTERVAL_SECONDS.get(interval, 60)
    return time_sec % bar_sec == 0
