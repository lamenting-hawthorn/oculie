"""
Shared Polymarket CLOB client factory.

Centralises auth so trade_engine, resolver, and any future modules
all use the same credential-building logic.

Signature types (set POLYMARKET_SIGNATURE_TYPE in .env):
  0 — EOA / MetaMask  (private key = funder's key; funder = derived address)
  1 — Email / Magic   (private key from Magic export; funder = Magic wallet address)
  2 — Browser proxy   (private key is a proxy key; funder = main wallet address)

Default is 0. Most users connecting MetaMask directly should use 0.
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def build_clob_client():
    """
    Build and return an authenticated ClobClient.

    Reads POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER, and optionally
    POLYMARKET_SIGNATURE_TYPE from the environment.

    API credentials are derived from the private key unless
    POLYMARKET_API_KEY / POLYMARKET_SECRET / POLYMARKET_PASSPHRASE are
    all pre-supplied.

    Raises KeyError if required env vars are missing.
    Raises ImportError if py_clob_client is not installed.
    """
    from py_clob_client.client import ClobClient

    private_key = os.environ["POLYMARKET_PRIVATE_KEY"]
    funder = os.environ["POLYMARKET_FUNDER"]
    sig_type = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "0"))

    logger.debug(
        "Building CLOB client — funder=%s signature_type=%d", funder, sig_type
    )

    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        funder=funder,
        signature_type=sig_type,
    )

    api_key = os.environ.get("POLYMARKET_API_KEY", "").strip()
    api_secret = os.environ.get("POLYMARKET_SECRET", "").strip()
    api_passphrase = os.environ.get("POLYMARKET_PASSPHRASE", "").strip()

    if api_key and api_secret and api_passphrase:
        client.set_api_creds({
            "api_key": api_key,
            "api_secret": api_secret,
            "api_passphrase": api_passphrase,
        })
        logger.debug("Using pre-supplied API credentials")
    else:
        creds = client.derive_api_key()
        client.set_api_creds(creds)
        logger.debug("API credentials derived from private key")

    return client


def get_usdc_balance(client) -> float:
    """
    Return the spendable USDC balance (collateral) for the authenticated client.
    Returns 0.0 on any error.
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        result = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        balance = int(result.get("balance", 0)) / 1e6
        logger.info("CLOB USDC balance: $%.2f", balance)
        return balance
    except Exception:
        logger.exception("Failed to fetch USDC balance from CLOB API")
        return 0.0


def cancel_order(client, order_id: str) -> bool:
    """
    Cancel a live order by order_id. Returns True on success.
    """
    try:
        client.cancel(order_id)
        logger.info("Cancelled order %s", order_id)
        return True
    except Exception:
        logger.exception("Failed to cancel order %s", order_id)
        return False
