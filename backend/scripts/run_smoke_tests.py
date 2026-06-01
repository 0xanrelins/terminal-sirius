#!/usr/bin/env python3
"""Run offline tests without pytest (plain test_* functions)."""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULES = [
    "tests.test_ws_contract",
    "tests.test_chart_indicators",
]

# Needs backend venv (asyncpg via liquidations → db import)
OPTIONAL_MODULES = [
    "tests.test_liquidations_parse",
    "tests.test_realtime_bucket_actor",
    "tests.test_polymarket_realtime_bucket",
    "tests.test_liq_post_event_service",
]


def _run_module(module_name: str) -> int:
    mod = importlib.import_module(module_name)
    count = 0
    for name, obj in inspect.getmembers(mod):
        if not name.startswith("test_") or not callable(obj):
            continue
        if inspect.isclass(obj):
            continue
        obj()
        print(f"  PASS {name}")
        count += 1
    return count


def main() -> None:
    total = 0
    for module_name in MODULES + OPTIONAL_MODULES:
        print(f"== {module_name} ==")
        try:
            n = _run_module(module_name)
        except ModuleNotFoundError as exc:
            if module_name in OPTIONAL_MODULES:
                print(f"  SKIP (optional deps missing: {exc})")
                continue
            raise
        if n == 0:
            raise SystemExit(f"no tests found in {module_name}")
        total += n
    print(f"\nAll smoke tests passed ({total} tests).")


if __name__ == "__main__":
    main()
