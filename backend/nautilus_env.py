"""
Polymarket env sync + L2 credential derive for Nautilus adapters.

Call prepare_polymarket_env() once at process startup (before TradingNode build).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolymarketWalletConfig:
    private_key: str | None
    signature_type: int
    funder: str | None
    api_key: str | None
    api_secret: str | None
    passphrase: str | None

    @property
    def has_private_key(self) -> bool:
        return bool(self.private_key)

    @property
    def has_l2_api(self) -> bool:
        return bool(self.api_key and self.api_secret and self.passphrase)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def polymarket_wallet_config() -> PolymarketWalletConfig:
    sig_raw = _env("POLYMARKET_SIGNATURE_TYPE")
    signature_type = int(sig_raw) if sig_raw.isdigit() else 0
    return PolymarketWalletConfig(
        private_key=_env("POLYMARKET_PRIVATE_KEY") or None,
        signature_type=signature_type,
        funder=_env("POLYMARKET_FUNDER_ADDRESS") or None,
        api_key=_env("POLYMARKET_API_KEY") or None,
        api_secret=_env("POLYMARKET_API_SECRET") or None,
        passphrase=_env("POLYMARKET_API_PASSPHRASE") or None,
    )


def clob_client_kwargs(cfg: PolymarketWalletConfig | None = None) -> dict[str, Any]:
    """Keyword args for py_clob_client_v2.ClobClient (derive + signing)."""
    c = cfg or polymarket_wallet_config()
    if not c.private_key:
        raise ValueError("POLYMARKET_PRIVATE_KEY is not set")
    kwargs: dict[str, Any] = {
        "host": "https://clob.polymarket.com",
        "chain_id": 137,
        "key": c.private_key,
    }
    if c.signature_type:
        kwargs["signature_type"] = c.signature_type
        if c.funder:
            kwargs["funder"] = c.funder
    elif c.funder:
        kwargs["signature_type"] = 1
        kwargs["funder"] = c.funder
    return kwargs


def sync_polymarket_env() -> None:
    """Copy project env vars into names Nautilus Polymarket adapters read."""
    pk = _env("POLYMARKET_PRIVATE_KEY")
    if pk and not _env("POLYMARKET_PK"):
        os.environ["POLYMARKET_PK"] = pk

    for src, dst in (
        ("POLYMARKET_API_KEY", "POLYMARKET_API_KEY"),
        ("POLYMARKET_API_SECRET", "POLYMARKET_API_SECRET"),
        ("POLYMARKET_API_PASSPHRASE", "POLYMARKET_PASSPHRASE"),
        ("POLYMARKET_FUNDER_ADDRESS", "POLYMARKET_FUNDER"),
    ):
        val = _env(src)
        if val and not _env(dst):
            os.environ[dst] = val


def ensure_polymarket_l2_env() -> bool:
    """
    Derive L2 API creds from private key when missing.
    Returns True if L2 creds are available after this call.
    """
    if _env("POLYMARKET_API_KEY"):
        return True
    cfg = polymarket_wallet_config()
    if not cfg.private_key:
        return False
    try:
        from py_clob_client_v2 import ClobClient

        client = ClobClient(**clob_client_kwargs(cfg))
        derived = client.create_or_derive_api_key()
        os.environ["POLYMARKET_API_KEY"] = derived.api_key
        os.environ["POLYMARKET_API_SECRET"] = derived.api_secret
        os.environ["POLYMARKET_API_PASSPHRASE"] = derived.api_passphrase
        print("[nautilus] derived Polymarket L2 API creds for ExecutionClient")
        sync_polymarket_env()
        return True
    except Exception as e:
        print(f"[warn] Polymarket L2 derive failed: {e}")
        return False


def prepare_polymarket_env() -> PolymarketWalletConfig:
    """Single startup entry: sync names, derive L2 if needed, sync again."""
    sync_polymarket_env()
    ensure_polymarket_l2_env()
    return polymarket_wallet_config()


def credentials_configured() -> bool:
    return polymarket_wallet_config().has_private_key
