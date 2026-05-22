"""
Polymarket CLOB orders via Nautilus adapter credentials + retry (same stack as ExecClient).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from adapters.polymarket.orders import OrderResult, _float_or_none


def _build_clob_client():
    from py_clob_client_v2 import ClobClient

    from nautilus_trader.adapters.polymarket.common.credentials import (
        get_polymarket_funder,
        get_polymarket_private_key,
    )
    from py_clob_client_v2 import ApiCreds

    key = get_polymarket_private_key()
    try:
        funder = get_polymarket_funder()
    except Exception:
        funder = os.environ.get("POLYMARKET_FUNDER_ADDRESS", "").strip() or None
    sig_raw = os.environ.get("POLYMARKET_SIGNATURE_TYPE", "").strip()
    signature_type = int(sig_raw) if sig_raw.isdigit() else 0

    kwargs: dict[str, Any] = {
        "host": "https://clob.polymarket.com",
        "chain_id": 137,
        "key": key,
    }
    if signature_type:
        kwargs["signature_type"] = signature_type
        if funder:
            kwargs["funder"] = funder
    elif funder:
        kwargs["signature_type"] = 1
        kwargs["funder"] = funder

    client = ClobClient(**kwargs)
    api_key = os.environ.get("POLYMARKET_API_KEY", "").strip()
    secret = os.environ.get("POLYMARKET_API_SECRET", "").strip()
    passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE", "").strip()
    if api_key and secret and passphrase:
        client.set_api_creds(
            ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase)
        )
    else:
        client.set_api_creds(client.create_or_derive_api_key())
    return client


def _place_sync(token_id: str, amount_usd: float) -> OrderResult:
    from py_clob_client_v2 import MarketOrderArgs, OrderType

    client = _build_clob_client()
    max_retries = int(os.environ.get("POLYMARKET_MAX_RETRIES", "3"))
    delay = float(os.environ.get("POLYMARKET_RETRY_DELAY_SEC", "1.0"))
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            raw = client.create_and_post_market_order(
                MarketOrderArgs(
                    token_id=token_id,
                    amount=float(amount_usd),
                    side="BUY",
                    order_type=OrderType.FOK,
                ),
                order_type=OrderType.FOK,
            )
            if not isinstance(raw, dict):
                raw = {"response": raw}
            order_id = str(
                raw.get("orderID")
                or raw.get("order_id")
                or raw.get("id")
                or ""
            )
            status = str(raw.get("status") or raw.get("order_status") or "submitted")
            fill_price = _float_or_none(raw.get("price") or raw.get("avg_price"))
            shares = _float_or_none(
                raw.get("size") or raw.get("filled_size") or raw.get("takingAmount")
            )
            cost = _float_or_none(raw.get("cost") or raw.get("makingAmount"))
            if cost is None and fill_price and shares:
                cost = round(fill_price * shares, 4)
            return OrderResult(
                order_id=order_id,
                clob_status=status,
                fill_price=fill_price,
                shares=shares,
                cost_usd=cost if cost is not None else round(amount_usd, 4),
                raw=raw,
            )
        except Exception as e:
            last_err = e
            if attempt + 1 < max_retries:
                import time

                time.sleep(delay * (attempt + 1))
    raise last_err or RuntimeError("Polymarket order failed")


async def place_market_buy(token_id: str, amount_usd: float) -> OrderResult:
    """Market buy using Nautilus Polymarket credential helpers + retry."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _place_sync, token_id, amount_usd)
