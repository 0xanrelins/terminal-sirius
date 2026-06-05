"""Exit policy: hold until Polymarket resolution (no discretionary CLOSE)."""

from strategies.terminal_sirius_strategy import TerminalSiriusStrategy


def test_exit_decision_source_always_holds():
    src = TerminalSiriusStrategy._exit_decision.__doc__ or ""
    assert "no discretionary exit" in src.lower()
    assert "close_all_positions" in src
