#!/usr/bin/env python3
"""
Print Polymarket CLOB API creds derived from POLYMARKET_PRIVATE_KEY in .env.

Usage (from repo root):
  cd backend && python scripts/derive_polymarket_api_creds.py

Do not commit output. Rotate keys if you ever pasted the private key in chat.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from pathlib import Path

load_dotenv()
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

key = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
if not key:
    print("Set POLYMARKET_PRIVATE_KEY in .env first", file=sys.stderr)
    sys.exit(1)

    from py_clob_client_v2 import ClobClient

funder = os.environ.get("POLYMARKET_FUNDER_ADDRESS", "").strip() or None
kwargs: dict = {
    "host": "https://clob.polymarket.com",
    "key": key,
    "chain_id": 137,
}
if funder:
    kwargs["signature_type"] = 1
    kwargs["funder"] = funder

client = ClobClient(**kwargs)
    creds = client.create_or_derive_api_key()

api_key = getattr(creds, "api_key", None) or getattr(creds, "key", "")
api_secret = getattr(creds, "api_secret", None) or getattr(creds, "secret", "")
passphrase = getattr(creds, "api_passphrase", None) or getattr(creds, "passphrase", "")

print("Add to .env (optional — backend derives these automatically if omitted):\n")
print(f"POLYMARKET_API_KEY={api_key}")
print(f"POLYMARKET_API_SECRET={api_secret}")
print(f"POLYMARKET_API_PASSPHRASE={passphrase}")
