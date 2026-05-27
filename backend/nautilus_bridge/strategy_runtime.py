"""Runtime wiring between FastAPI loop and Nautilus strategies."""
from __future__ import annotations

import asyncio
import queue
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strategies.liq_poly_config import LiqPolyRuntimeConfig

_event_queue: queue.Queue | None = None
_main_loop: asyncio.AbstractEventLoop | None = None
_runtime: dict[str, LiqPolyRuntimeConfig] = {}
_catchup_queues: dict[str, queue.Queue] = {
    "live": queue.Queue(maxsize=32),
    "sim": queue.Queue(maxsize=32),
}


def set_event_queue(q: queue.Queue) -> None:
    global _event_queue
    _event_queue = q


def get_event_queue() -> queue.Queue:
    if _event_queue is None:
        raise RuntimeError("strategy event queue not set")
    return _event_queue


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def get_main_loop() -> asyncio.AbstractEventLoop:
    if _main_loop is None:
        raise RuntimeError("main asyncio loop not set")
    return _main_loop


def set_runtime(mode: str, cfg: LiqPolyRuntimeConfig) -> None:
    _runtime[mode] = cfg


def get_runtime(mode: str) -> LiqPolyRuntimeConfig:
    if mode not in _runtime:
        raise RuntimeError(f"runtime not set for mode={mode!r}")
    return _runtime[mode]


def request_strategy_catchup(mode: str, reset_signaled: bool) -> asyncio.Future:
    loop = get_main_loop()
    fut: asyncio.Future = loop.create_future()
    _catchup_queues[mode].put((reset_signaled, fut))
    return fut


def drain_catchup_request(mode: str) -> tuple[bool, asyncio.Future] | None:
    try:
        return _catchup_queues[mode].get_nowait()
    except queue.Empty:
        return None


def refresh_runtime_from_env(mode: str) -> None:
    """Reload LiqPolyRunner config after /live/config or /simulation/config env changes."""
    from strategies.liq_poly_config import runtime_from_env

    try:
        restore = get_runtime(mode).restore
    except RuntimeError:
        from strategies.liq_poly_config import RestoreState

        restore = RestoreState()
    set_runtime(mode, runtime_from_env(mode, restore))
