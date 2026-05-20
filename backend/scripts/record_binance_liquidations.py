#!/usr/bin/env python3
"""
Standalone Binance USDT-M liquidation WebSocket recorder (!forceOrder@arr).

Runs without uvicorn/Nautilus. Writes NDJSON lines:
  {"received_at": "<ISO8601 UTC>", "raw": <parsed WS JSON>}

Primary raw archive. Optional `--postgres` mirrors into `liquidation_events` (uvicorn does not write raw by default).

Usage (from repo backend/, venv active):
  python scripts/record_binance_liquidations.py
  python scripts/record_binance_liquidations.py --postgres
  python scripts/record_binance_liquidations.py --output-dir /var/log/liq_raw --shard hourly
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `import db` / `import liquidations` when run as script
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import websockets
from dotenv import load_dotenv

load_dotenv(_BACKEND_ROOT / ".env")

WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iter_force_order_items(envelope: dict) -> list[dict]:
    """Binance combined payload → list of forceOrder event dicts."""
    data = envelope.get("data", envelope)
    events = data if isinstance(data, list) else [data]
    out: list[dict] = []
    for item in events:
        if isinstance(item, dict) and item.get("e") == "forceOrder":
            out.append(item)
    return out


def _current_log_path(output_dir: Path, shard: str) -> Path:
    now = datetime.now(timezone.utc)
    output_dir.mkdir(parents=True, exist_ok=True)
    if shard == "hourly":
        name = now.strftime("%Y-%m-%dT%H") + ".ndjson"
    else:
        name = now.strftime("%Y-%m-%d") + ".ndjson"
    return output_dir / name


async def run_recorder(
    *,
    output_dir: Path,
    shard: str,
    postgres: bool,
) -> None:
    if postgres:
        import db
        from liquidations import force_order_trade_id, maybe_prune_liquidation_events

        await db.init()
    prune_counter = [0]

    current_path: Path | None = None
    out_fp = None

    def ensure_file() -> None:
        nonlocal current_path, out_fp
        path = _current_log_path(output_dir, shard)
        if path != current_path:
            if out_fp is not None:
                out_fp.close()
            current_path = path
            out_fp = open(path, "a", encoding="utf-8")

    try:
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    print(f"[liq-recorder] connected {WS_URL}", flush=True)
                    async for raw in ws:
                        received_at = _utc_now_iso()
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError:
                            print(f"[liq-recorder] skip invalid JSON", flush=True)
                            continue

                        ensure_file()
                        assert out_fp is not None
                        line = json.dumps(
                            {"received_at": received_at, "raw": parsed},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        out_fp.write(line + "\n")
                        out_fp.flush()

                        if postgres:
                            for item in iter_force_order_items(parsed):
                                tid = force_order_trade_id(item)
                                if await db.insert_liquidation_event(tid, item):
                                    prune_counter[0] += 1
                                    if prune_counter[0] % 100 == 0:
                                        await maybe_prune_liquidation_events()
            except Exception as e:
                print(f"[liq-recorder] disconnected: {e}", flush=True)
                await asyncio.sleep(3)
    finally:
        if out_fp is not None:
            out_fp.close()
        if postgres:
            await db.close()


def main() -> None:
    default_dir = os.environ.get(
        "LIQ_RECORDER_OUTPUT_DIR",
        str(_BACKEND_ROOT / "data" / "liq_raw"),
    )
    parser = argparse.ArgumentParser(description="Record Binance forceOrder WS to NDJSON.")
    parser.add_argument(
        "--output-dir",
        default=default_dir,
        help="NDJSON output directory (default: LIQ_RECORDER_OUTPUT_DIR or backend/data/liq_raw)",
    )
    shard_env = os.environ.get("LIQ_RECORDER_SHARD", "daily").lower()
    if shard_env not in ("daily", "hourly"):
        shard_env = "daily"
    parser.add_argument(
        "--shard",
        choices=("daily", "hourly"),
        default=shard_env,
        help="Log file rotation: one file per UTC day or per UTC hour",
    )
    env_pg = os.environ.get("LIQ_RECORDER_POSTGRES", "").lower() in ("1", "true", "yes")
    parser.add_argument(
        "--postgres",
        dest="postgres",
        action="store_true",
        help="Also insert into liquidation_events (needs DATABASE_URL)",
    )
    parser.add_argument(
        "--no-postgres",
        dest="postgres",
        action="store_false",
        help="Disable DB mirror even if LIQ_RECORDER_POSTGRES=1",
    )
    parser.set_defaults(postgres=env_pg)
    args = parser.parse_args()
    out = Path(args.output_dir).expanduser().resolve()

    asyncio.run(
        run_recorder(
            output_dir=out,
            shard=args.shard,
            postgres=args.postgres,
        )
    )


if __name__ == "__main__":
    main()
