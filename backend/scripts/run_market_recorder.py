"""Deprecated: market recorder runs inside the shared TradingNode (node.py)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    print(
        "Market recorder is integrated into the main Nautilus node.\n"
        "Start the backend instead:\n"
        "  cd backend && ./scripts/run_backend.sh\n"
        "Disable with MARKET_RECORDER_ENABLED=0 in .env"
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
