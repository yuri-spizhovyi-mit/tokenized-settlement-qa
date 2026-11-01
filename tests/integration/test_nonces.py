"""INT-10: nonce management for locally signed transactions (book Ch 1.3).

Eight workers send token transfers from the *same* account at the same time, each
signing a raw transaction locally with its own nonce. A barrier makes them all
choose a nonce before any of them sends, which is exactly the window a real
service hits under load.

- With the naive manager (ask the node for the pending count each time), all eight
  pick the same nonce. One transfer lands; the other seven are rejected or dropped.
- With a local manager (seed once, hand out under a lock), all eight land with
  consecutive nonces.

The assertions are on chain state, the payee's balance, not on exceptions: a
duplicate send can come back *without* an error, and only the balance shows that
the money didn't move (docs/findings.md, F-11).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import allure
import pytest
from web3 import Web3

from lib.nonces import LocalNonceManager, NaiveNonceManager

pytestmark = [allure.feature("Nonce management"), pytest.mark.req("INT-10")]

WORKERS = 8


def _fund_payer(system, amount: int) -> None:
    a, token = system.accounts, system.token
    for who in (a.payer.address, a.payee.address):
        if not token.is_allowlisted(who):
            token.set_allowlisted(a.compliance, who, True)
    token.mint(a.minter, a.payer.address, amount)


def _send_concurrently(system, manager_factory):
    """Each worker i sends `transfer(payee, i + 1)`, so every transfer is distinct
    and the payee's balance shows exactly which ones landed."""
    a, chain = system.accounts, system.chain
    url = chain.w3.provider.endpoint_uri
    sender = a.payer
    base_fee = chain.w3.eth.get_block("latest")["baseFeePerGas"]
    chain_id = chain.w3.eth.chain_id
    shared = manager_factory(chain.w3, sender.address)
    barrier = threading.Barrier(WORKERS)

    def worker(i: int):
        w3 = Web3(Web3.HTTPProvider(url))  # one provider per thread
        token = w3.eth.contract(address=system.token.address, abi=system.token.contract.abi)
        nonce = shared.next()
        barrier.wait()  # everyone has chosen a nonce before anyone sends
        tx = token.functions.transfer(a.payee.address, i + 1).build_transaction({
            "from": sender.address,
            "nonce": nonce,
            "gas": 120_000,
            "maxFeePerGas": base_fee * 2 + 10**9,
            "maxPriorityFeePerGas": 10**9,
            "chainId": chain_id,
        })
        signed = sender.sign_transaction(tx)
        try:
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            return nonce, tx_hash, None
        except Exception as exc:  # the node's rejection is the data point
            return nonce, None, str(exc)

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(worker, range(WORKERS)))

    mined = []
    for nonce, tx_hash, _ in results:
        if tx_hash is not None:
            receipt = chain.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            if receipt["status"] == 1:
                mined.append((nonce, receipt))
    return results, mined


def test_naive_nonce_manager_collides_under_concurrency(system):
    a, chain, token = system.accounts, system.chain, system.token
    _fund_payer(system, 1_000)
    start_nonce = chain.nonce(a.payer.address)
    payee_before = token.balance_of(a.payee.address)

    results, mined = _send_concurrently(system, NaiveNonceManager)

    chosen = {nonce for nonce, _, _ in results}
    assert chosen == {start_nonce}, f"every worker should have picked {start_nonce}, got {chosen}"
    assert len(mined) == 1, f"only one of {WORKERS} same-nonce transfers can land, got {len(mined)}"
    received = token.balance_of(a.payee.address) - payee_before
    assert 1 <= received <= WORKERS, "exactly one transfer's amount arrived"
    assert received != sum(range(1, WORKERS + 1)), "the naive manager lost transfers"
    assert chain.nonce(a.payer.address) == start_nonce + 1
    errors = [err for _, _, err in results if err]
    allure.attach(
        "\n".join(errors) or "(no errors: duplicates accepted silently)",
        name="node rejections",
        attachment_type=allure.attachment_type.TEXT,
    )


def test_local_nonce_manager_lands_every_transaction(system):
    a, chain, token = system.accounts, system.chain, system.token
    _fund_payer(system, 1_000)
    start_nonce = chain.nonce(a.payer.address)
    payee_before = token.balance_of(a.payee.address)

    results, mined = _send_concurrently(system, LocalNonceManager)

    assert [err for _, _, err in results if err] == []
    assert sorted(n for n, _, _ in results) == list(range(start_nonce, start_nonce + WORKERS))
    assert len(mined) == WORKERS
    assert token.balance_of(a.payee.address) - payee_before == sum(range(1, WORKERS + 1))
    assert chain.nonce(a.payer.address) == start_nonce + WORKERS
