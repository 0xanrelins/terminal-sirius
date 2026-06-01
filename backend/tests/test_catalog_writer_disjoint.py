"""CatalogWriter uses ParquetDataCatalog skip_disjoint_check for live append."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from catalog import get_catalog
from recorders.catalog_writer import CatalogWriter
from recorders.data_types import BinanceSecondPrice


@pytest.fixture
def temp_catalog(monkeypatch: pytest.MonkeyPatch):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "catalog"
        monkeypatch.setenv("CATALOG_PATH", str(path))
        yield path


def test_catalog_writer_flushes_overlapping_intervals(temp_catalog: Path) -> None:
    ts = 1_770_000_000_000_000_000
    writer = CatalogWriter(flush_interval_ms=200, max_batch_rows=100)
    writer.start()
    try:
        row = BinanceSecondPrice(
            ts_event=ts,
            ts_init=ts,
            symbol="BTCUSDT-PERP.BINANCE",
            last_price=1.0,
        )
        assert writer.enqueue(row) is True
        time.sleep(0.35)
        assert writer.enqueue(
            BinanceSecondPrice(
                ts_event=ts + 1_000_000_000,
                ts_init=ts + 1,
                symbol="BTCUSDT-PERP.BINANCE",
                last_price=2.0,
            ),
        )
        time.sleep(0.35)
        stats = writer.stats_snapshot()
        assert stats["failed"] is False
        assert stats["written_rows"] >= 2
        writer.stop()
    except Exception:
        writer.stop()
        raise

    assert len(get_catalog().query(data_cls=BinanceSecondPrice)) >= 2
