"""
In-process Polymarket quote book fed only by Nautilus DataClient ticks (quote bridge).

Gamma/CLOB REST must not be used for prices when this registry has data for a slug.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class SlugQuoteBook:
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    ts_ms: int = 0

    @property
    def yes_mid(self) -> float | None:
        if self.yes_bid and self.yes_ask and self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 2
        return self.yes_ask or self.yes_bid

    @property
    def no_mid(self) -> float | None:
        if self.no_bid and self.no_ask and self.no_bid > 0 and self.no_ask > 0:
            return (self.no_bid + self.no_ask) / 2
        return self.no_ask or self.no_bid


_lock = threading.Lock()
_books: dict[str, SlugQuoteBook] = {}
_slug_iids: dict[str, tuple[str, str | None]] = {}


def update_slug_quote(
    slug: str,
    *,
    token: str,
    bid: float,
    ask: float,
    ts_ms: int | None = None,
) -> None:
    if not slug or bid <= 0 and ask <= 0:
        return
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    with _lock:
        book = _books.setdefault(slug, SlugQuoteBook())
        if token == "yes":
            book.yes_bid, book.yes_ask = bid, ask
        else:
            book.no_bid, book.no_ask = bid, ask
        book.ts_ms = ts


def get_slug_quotes(slug: str) -> SlugQuoteBook | None:
    with _lock:
        return _books.get(slug)


def register_slug_instruments(
    slug: str,
    *,
    yes_iid: str,
    no_iid: str | None = None,
) -> None:
    with _lock:
        _slug_iids[slug] = (yes_iid, no_iid)


def get_slug_instruments(slug: str) -> tuple[str, str | None] | None:
    with _lock:
        return _slug_iids.get(slug)


def clear_quotes() -> None:
    with _lock:
        _books.clear()
        _slug_iids.clear()
