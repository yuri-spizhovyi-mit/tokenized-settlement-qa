"""Helpers for the transfer API tests."""

from __future__ import annotations

import httpx
from sqlalchemy import select
from web3.exceptions import TransactionNotFound

from transfer_api.db import attempts


def post(api, key: str, to: str, amount) -> httpx.Response:
    return httpx.post(f"{api.url}/transfers", json={"to": to, "amount": amount},
                      headers={"Idempotency-Key": key}, timeout=30)


def get(api, transfer_id: str) -> dict:
    return httpx.get(f"{api.url}/transfers/{transfer_id}", timeout=30).json()


def statuses(view: dict) -> list[str]:
    return [h["status"] for h in view["history"]]


def wait_for_attempts(service) -> None:
    """Wait until the node has a receipt for every broadcast attempt it still knows.

    `tick()` never waits: right after a broadcast, Anvil may not have the receipt yet
    (F-13), and the *next* tick picks it up. Tests that want "the next tick sees it
    mined" wait here first, on the node, not with a sleep. Attempts the node dropped
    or never accepted, and ones queued behind a nonce gap, are skipped. Don't call it
    with automine off."""
    with service.engine.connect() as conn:
        hashes = conn.execute(select(attempts.c.tx_hash)).scalars().all()
    for tx_hash in hashes:
        try:
            tx = service.w3.eth.get_transaction(tx_hash)
        except TransactionNotFound:
            continue  # dropped by the node, or never accepted
        if tx["blockNumber"] is None and tx["nonce"] > service.w3.eth.get_transaction_count(tx["from"], "pending"):
            continue  # queued behind a nonce gap: no receipt until the hole is filled
        service.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=10)
