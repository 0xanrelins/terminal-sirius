"""Polymarket CLOB order placement (live trading via Nautilus adapter stack)."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

_client = None
_use_nautilus_stack = os.environ.get("LIVE_USE_NAUTILUS_EXEC", "true").lower() in (
    "1",
    "true",
    "yes",
)


@dataclass
class OrderResult:
    order_id: str
    clob_status: str
    fill_price: float | None
    shares: float | None
    cost_usd: float | None
    raw: dict[str, Any]


def credentials_configured() -> bool:
    return bool(os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip())


def _api_creds_from_env_or_derive(client) -> None:
    """Use explicit L2 creds from .env, or derive them from the private key."""
    from py_clob_client_v2 import ApiCreds

    api_key = os.environ.get("POLYMARKET_API_KEY", "").strip()
    secret = os.environ.get("POLYMARKET_API_SECRET", "").strip()
    passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE", "").strip()
    if api_key and secret and passphrase:
        client.set_api_creds(
            ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase)
        )
        return

    derived = client.create_or_derive_api_key()
    client.set_api_creds(derived)
    print("[live-order] derived Polymarket API creds from private key (L1→L2)")


def can_place_orders() -> bool:
    from live import config as live_cfg

    return live_cfg.is_enabled() and credentials_configured()


def _get_client():
    global _client
    if _client is not None:
        return _client

    from py_clob_client_v2 import ClobClient

    key = os.environ["POLYMARKET_PRIVATE_KEY"].strip()
    funder = os.environ.get("POLYMARKET_FUNDER_ADDRESS", "").strip() or None
    sig_raw = os.environ.get("POLYMARKET_SIGNATURE_TYPE", "").strip()
    signature_type = int(sig_raw) if sig_raw.isdigit() else None

    kwargs: dict[str, Any] = {
        "host": "https://clob.polymarket.com",
        "chain_id": 137,
        "key": key,
    }
    if signature_type is not None:
        kwargs["signature_type"] = signature_type
        if funder:
            kwargs["funder"] = funder
    elif funder:
        kwargs["signature_type"] = 1
        kwargs["funder"] = funder

    client = ClobClient(**kwargs)
    _api_creds_from_env_or_derive(client)
    _client = client
    return _client


def _place_market_buy_sync(token_id: str, amount_usd: float) -> OrderResult:
    from py_clob_client_v2 import MarketOrderArgs, OrderType

    client = _get_client()
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


def _float_or_none(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


async def place_market_buy(token_id: str, amount_usd: float) -> OrderResult:
    if not can_place_orders():
        raise RuntimeError("Live orders disabled or credentials missing")
    if _use_nautilus_stack:
        from nautilus_bridge.polymarket_exec import place_market_buy as nt_place

        result = await nt_place(token_id, amount_usd)
        print(
            f"[live-order] Nautilus Polymarket stack "
            f"token={token_id[:12]}… ${amount_usd:.2f} id={result.order_id or '?'}"
        )
        return result
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _place_market_buy_sync, token_id, amount_usd
    )


def reset_client() -> None:
    """Drop cached client (e.g. after credential rotation)."""
    global _client
    _client = None
