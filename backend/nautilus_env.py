"""
Map Terminal Sirius .env names to Nautilus Polymarket adapter expectations.

Call sync_polymarket_env() once at process startup (before TradingNode build).
"""
from __future__ import annotations

import os


def sync_polymarket_env() -> None:
    """Copy project env vars into names Nautilus Polymarket adapters read."""
    pk = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
    if pk and not os.environ.get("POLYMARKET_PK"):
        os.environ["POLYMARKET_PK"] = pk

    for src, dst in (
        ("POLYMARKET_API_KEY", "POLYMARKET_API_KEY"),
        ("POLYMARKET_API_SECRET", "POLYMARKET_API_SECRET"),
        ("POLYMARKET_API_PASSPHRASE", "POLYMARKET_PASSPHRASE"),
        ("POLYMARKET_FUNDER_ADDRESS", "POLYMARKET_FUNDER"),
    ):
        val = os.environ.get(src, "").strip()
        if val and not os.environ.get(dst):
            os.environ[dst] = val


def ensure_polymarket_l2_env() -> None:
    """
    Nautilus Polymarket ExecClient requires L2 API env at build time.
    Derive from private key when missing (same as legacy orders.py).
    """
    if os.environ.get("POLYMARKET_API_KEY", "").strip():
        return
    pk = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
    if not pk:
        return
    try:
        from py_clob_client_v2 import ApiCreds, ClobClient

        sig_raw = os.environ.get("POLYMARKET_SIGNATURE_TYPE", "").strip()
        signature_type = int(sig_raw) if sig_raw.isdigit() else 0
        funder = os.environ.get("POLYMARKET_FUNDER_ADDRESS", "").strip() or None
        kwargs: dict = {
            "host": "https://clob.polymarket.com",
            "chain_id": 137,
            "key": pk,
        }
        if signature_type:
            kwargs["signature_type"] = signature_type
            if funder:
                kwargs["funder"] = funder
        elif funder:
            kwargs["signature_type"] = 1
            kwargs["funder"] = funder
        client = ClobClient(**kwargs)
        derived = client.create_or_derive_api_key()
        os.environ["POLYMARKET_API_KEY"] = derived.api_key
        os.environ["POLYMARKET_API_SECRET"] = derived.api_secret
        os.environ["POLYMARKET_API_PASSPHRASE"] = derived.api_passphrase
        print("[nautilus] derived Polymarket L2 API creds for ExecutionClient")
    except Exception as e:
        print(f"[warn] Polymarket L2 derive failed: {e}")
