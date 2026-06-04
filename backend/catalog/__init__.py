"""ParquetDataCatalog for native streaming capture and liq-post-event research."""
from __future__ import annotations

import os
from pathlib import Path

from nautilus_trader.persistence.catalog import ParquetDataCatalog

# Default: live recorder output under backend/catalog. Override with CATALOG_PATH.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LIVE_CATALOG = Path(__file__).resolve().parent
_ARCHIVE_PATH = _REPO_ROOT / "archive" / "parquet-catalog-2026-06-03"
_DEFAULT_PATH = _LIVE_CATALOG if os.environ.get("CATALOG_USE_ARCHIVE", "").lower() not in (
    "1",
    "true",
    "yes",
) else _ARCHIVE_PATH


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
