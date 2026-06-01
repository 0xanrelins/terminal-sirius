from dataclasses import dataclass

from recorders.lookup import _nearest_by_ts


@dataclass
class _Row:
    ts_event: int
    value: int


def test_nearest_by_ts_selects_closest_row():
    rows = [
        _Row(ts_event=1_000, value=1),
        _Row(ts_event=2_000, value=2),
        _Row(ts_event=3_000, value=3),
    ]

    got = _nearest_by_ts(rows, ts_ns=2_400)

    assert got is not None
    assert got.value == 2
