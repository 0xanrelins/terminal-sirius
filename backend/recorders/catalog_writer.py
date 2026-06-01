"""Batched append-only writer for ParquetDataCatalog."""
from __future__ import annotations

import queue
import threading
import time
from collections import defaultdict
from collections import deque
from typing import DefaultDict

from nautilus_trader.core.data import Data

from catalog import get_catalog


class CatalogWriter:
    """Buffered writer which flushes by row-count or interval."""

    def __init__(self, *, flush_interval_ms: int, max_batch_rows: int) -> None:
        self._flush_interval_ms = flush_interval_ms
        self._max_batch_rows = max_batch_rows
        self._queue: queue.Queue[Data] = queue.Queue(maxsize=max_batch_rows * 4)
        self._buffers: DefaultDict[type, list[Data]] = defaultdict(list)
        self._recent_keys: DefaultDict[type, set[tuple]] = defaultdict(set)
        self._recent_order: DefaultDict[type, deque[tuple]] = defaultdict(deque)
        self._thread: threading.Thread | None = None
        self._running = False
        self._failed = False
        self._last_error: str | None = None
        self._dropped_not_running = 0
        self._dropped_queue_full = 0
        self._written_rows = 0
        self._flush_count = 0
        self._last_flush_ns = 0
        self._catalog = get_catalog()

    def start(self) -> None:
        if self._running:
            return
        self._failed = False
        self._last_error = None
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="catalog-writer")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            self._flush_all()
        except Exception as e:
            self._failed = True
            self._last_error = f"flush_on_stop failed: {e!r}"
            print(f"[recorder] catalog writer stop flush failed: {e!r}")

    def enqueue(self, item: Data) -> bool:
        if not self._running:
            self._dropped_not_running += 1
            return False
        if self._failed:
            self._dropped_not_running += 1
            return False
        item_key = self._dedup_key(item)
        if item_key is not None:
            keyset = self._recent_keys[type(item)]
            if item_key in keyset:
                return True
            keyset.add(item_key)
            order = self._recent_order[type(item)]
            order.append(item_key)
            while len(order) > self._max_batch_rows * 8:
                old = order.popleft()
                keyset.discard(old)
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            # Keep recorder stable under bursty conditions.
            self._dropped_queue_full += 1
            return False

    def _run(self) -> None:
        try:
            while self._running:
                try:
                    item = self._queue.get(timeout=self._flush_interval_ms / 1000.0)
                    self._buffers[type(item)].append(item)
                    if self._buffer_size() >= self._max_batch_rows:
                        self._flush_all()
                except queue.Empty:
                    self._flush_all()
        except Exception as e:
            self._failed = True
            self._last_error = f"writer loop failed: {e!r}"
            self._running = False
            print(f"[recorder] catalog writer failed: {e!r}")

    def _buffer_size(self) -> int:
        return sum(len(items) for items in self._buffers.values())

    def _flush_all(self) -> None:
        if not self._buffers:
            return
        for items in list(self._buffers.values()):
            if not items:
                continue
            items.sort(key=lambda x: int(x.ts_init))
            # ParquetDataCatalog: live append may overlap prior file intervals (docs/concepts/data.md).
            self._catalog.write_data(items, skip_disjoint_check=True)
            self._written_rows += len(items)
            self._flush_count += 1
            self._last_flush_ns = time.time_ns()
            items.clear()

    def is_healthy(self) -> bool:
        return self._running and not self._failed

    def stats_snapshot(self) -> dict[str, int | str | None | bool]:
        thread_alive = self._thread.is_alive() if self._thread is not None else False
        return {
            "running": self._running,
            "failed": self._failed,
            "thread_alive": thread_alive,
            "queue_size": self._queue.qsize(),
            "buffer_size": self._buffer_size(),
            "dropped_not_running": self._dropped_not_running,
            "dropped_queue_full": self._dropped_queue_full,
            "written_rows": self._written_rows,
            "flush_count": self._flush_count,
            "last_flush_ns": self._last_flush_ns,
            "last_error": self._last_error,
        }

    @staticmethod
    def _dedup_key(item: Data) -> tuple | None:
        if hasattr(item, "market"):
            return (getattr(item, "market"), int(getattr(item, "ts_event")))
        if all(hasattr(item, field) for field in ("symbol", "last_price")):
            return (getattr(item, "symbol"), int(getattr(item, "ts_event")))
        if all(hasattr(item, field) for field in ("symbol", "side", "price", "quantity")):
            return (
                getattr(item, "symbol"),
                getattr(item, "side"),
                float(getattr(item, "price")),
                float(getattr(item, "quantity")),
                int(getattr(item, "ts_event")),
            )
        return None
