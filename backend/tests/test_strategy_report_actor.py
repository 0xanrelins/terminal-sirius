import pandas as pd

from strategy_report_actor import dataframe_to_json_records


def test_dataframe_to_json_records_empty():
    assert dataframe_to_json_records(pd.DataFrame()) == []


def test_dataframe_to_json_records_index_and_nulls():
    df = pd.DataFrame(
        {"qty": ["1", None], "status": ["FILLED", "OPEN"]},
        index=pd.Index(["a", "b"], name="client_order_id"),
    )
    rows = dataframe_to_json_records(df)
    assert len(rows) == 2
    assert rows[0]["client_order_id"] == "a"
    assert rows[0]["qty"] == "1"
    assert rows[1]["qty"] is None
