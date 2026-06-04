"""Polymarket credential helpers for live trading (orders via Nautilus ExecClient only)."""
from __future__ import annotations

import nautilus_env


def credentials_configured() -> bool:
    return nautilus_env.credentials_configured()
