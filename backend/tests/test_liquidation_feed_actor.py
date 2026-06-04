"""LiquidationFeedActor Parquet catalog batch flush."""

from liquidation_feed_actor import LiquidationFeedActor, LiquidationFeedActorConfig
from recorders.data_types import LiquidationTick


class _MockCatalog:
    def __init__(self) -> None:
        self.batches: list[list[LiquidationTick]] = []

    def write_data(self, batch: list[LiquidationTick]) -> None:
        self.batches.append(list(batch))


def _sample_tick() -> LiquidationTick:
    return LiquidationTick(
        symbol="BTCUSDT-PERP.BINANCE",
        side="SELL",
        notional=1000.0,
        price=50000.0,
        quantity=0.02,
        ts_event=1,
        ts_init=1,
    )


def test_flush_catalog_writes_and_clears_buffer() -> None:
    actor = LiquidationFeedActor(
        LiquidationFeedActorConfig(component_id="LiqFeed-Test"),
    )
    mock = _MockCatalog()
    actor._catalog = mock
    actor._buffer = [_sample_tick(), _sample_tick()]

    actor._flush_catalog()

    assert len(mock.batches) == 1
    assert len(mock.batches[0]) == 2
    assert actor._buffer == []


def test_buffer_flush_at_max_batch(monkeypatch) -> None:
    actor = LiquidationFeedActor(
        LiquidationFeedActorConfig(
            component_id="LiqFeed-Test",
            catalog_max_batch=2,
        ),
    )
    mock = _MockCatalog()
    actor._catalog = mock
    monkeypatch.setattr(actor, "publish_data", lambda *_a, **_k: None)

    actor._emit_tick(_sample_tick())
    assert mock.batches == []
    assert len(actor._buffer) == 1

    actor._emit_tick(_sample_tick())
    assert len(mock.batches) == 1
    assert len(mock.batches[0]) == 2
    assert actor._buffer == []
