"""ParquetDataCatalog configuration for Terminal Sirius backtest pipeline."""
from __future__ import annotations

import os
from pathlib import Path

from nautilus_trader.persistence.catalog import ParquetDataCatalog

# Default: backend/catalog/data — override with CATALOG_PATH env var
_DEFAULT_PATH = Path(__file__).resolve().parent / "data"


def get_catalog(path: str | Path | None = None) -> ParquetDataCatalog:
    """Return a ParquetDataCatalog at *path* (env CATALOG_PATH or default)."""
    resolved = Path(
        path
        or os.environ.get("CATALOG_PATH", "")
        or _DEFAULT_PATH
    ).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return ParquetDataCatalog(str(resolved))


__all__ = ["get_catalog"]
