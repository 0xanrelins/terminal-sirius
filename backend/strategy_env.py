"""Shared env parsing for sim/live strategy asset lists."""
from __future__ import annotations

import json
import os


def parse_csv_keys(raw: str | None) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {k.strip().upper() for k in raw.split(",") if k.strip()}


def keys_from_json_env(env_key: str) -> set[str]:
    raw = os.environ.get(env_key)
    if not raw or not str(raw).strip():
        return set()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k).strip().upper() for k in data if str(k).strip()}
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return set()


def resolve_active_keys(
    *,
    catalog: set[str],
    csv_env: str,
    thresholds_env: str,
) -> set[str]:
    """Explicit CSV list wins; else JSON keys; else full catalog."""
    explicit = parse_csv_keys(os.environ.get(csv_env))
    if explicit:
        return {k for k in explicit if k in catalog}
    from_json = keys_from_json_env(thresholds_env)
    if from_json:
        return {k for k in from_json if k in catalog}
    return set(catalog)
