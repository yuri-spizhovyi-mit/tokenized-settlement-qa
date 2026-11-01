"""TAPI-13..TAPI-16: a rejected send or a missing nonce cannot stall the hot wallet
(issue #12, book Ch 1.3).

The node only executes nonce N+1 after N. If a nonce is handed out but its
transaction never reaches the chain (the node refused it), every later transaction
waits behind the hole, without an error, forever (F-19).
"""

from __future__ import annotations

import allure
import pytest
from web3.exceptions import TimeExhausted

from lib.builders import USDC
from lib.naive_transfer import CountingNonceManager
from lib.transfers import get, post, statuses, wait_for_attempts
from transfer_api import NonceManager, TransferService

pytestmark = allure.feature("Transfer API: nonce gaps")

GWEI = 10**9


def _send(chain, token, wallet, manager, to, amount, gas=100_000):
    """Sign locally with the manager's nonce; on a refusal, let the manager re-sync."""
    base = chain.w3.eth.get_block("latest")["baseFeePerGas"]
    tx = token.contract.functions.transfer(to, amount).build_transaction({
        "from": wallet.address, "nonce": manager.next(), "gas": gas,
        "maxFeePerGas": 2 * base + GWEI, "maxPriorityFeePerGas": GWEI,
    })
    try:
        return chain.w3.eth.send_raw_transaction(wallet.sign_transaction(tx).raw_transaction)
    except Exception:
        manager.resync()
        return None


@pytest.mark.req("TAPI-13")
@pytest.mark.parametrize("manager_cls", [
    NonceManager,
    pytest.param(CountingNonceManager, marks=pytest.mark.xfail(
        strict=True, raises=(AssertionError, TimeExhausted),
        reason="F-19: counting on past a rejected nonce queues everything behind it"), id="CountingNonceManager"),
])
def test_rejected_send_does_not_stall_later_transactions(system, hot_wallet, manager_cls):
    """TAPI-13: the send that gets nonce k is refused (gas below intrinsic). Three more
    sends follow. All three are mined, and the wallet's nonce is k + 3."""
    a, chain, token = system.accounts, system.chain, system.token
    start = chain.nonce(hot_wallet.address)
    manager = manager_cls(chain.w3, hot_wallet.address)

    assert _send(chain, token, hot_wallet, manager, a.payee.address, 1, gas=20_000) is None  # refused
    hashes = [_send(chain, token, hot_wallet, manager, a.payee.address, n) for n in (1, 2, 3)]
    chain.mine()

    assert token.balance_of(a.payee.address) == 6
    assert chain.nonce(hot_wallet.address) == start + 3
    for h in hashes:
        assert chain.w3.eth.wait_for_transaction_receipt(h, timeout=2)["status"] == 1


@pytest.mark.req("TAPI-14")
def test_send_from_the_same_key_elsewhere_is_recovered_by_resync(system, api, service, hot_wallet):
    """TAPI-14: someone uses the hot wallet's key outside the service, so the service's
    next nonce is already taken. The node answers "nonce too low"; the service re-syncs
    and retries at once, and the transfer confirms."""
    a, chain, token = system.accounts, system.chain, system.token
    start = service.nonces.peek()
    token.transfer(hot_wallet, a.payee.address, 1 * USDC)  # the outside send uses `start`
    transfer = post(api, "after-outside-send", a.payee.address, str(2 * USDC)).json()

    service.tick()
    wait_for_attempts(service)
    chain.mine()
    service.tick()

    view = get(api, transfer["id"])
    assert view["status"] == "confirmed" and view["nonce"] == start + 1
    assert statuses(view)[:4] == ["accepted", "signed", "accepted", "signed"]
    assert "nonce too low" in view["history"][2]["detail"]
    assert token.balance_of(a.payee.address) == 3 * USDC
    assert chain.nonce(hot_wallet.address) == start + 2


class FeeSpikeOnFirstSend(TransferService):
    """The base fee jumps between pricing and sending the first transfer, so the node
    refuses it ("max fee per gas less than block base fee"). A block at the new base
    fee is mined right after, so later sends are priced correctly."""

    spiked = False

    def _after_write_ahead(self, transfer_id):
        if not self.spiked:
            self.w3.provider.make_request("anvil_setNextBlockBaseFeePerGas", [hex(100 * GWEI)])

    def _after_broadcast(self, transfer_id):
        if not self.spiked:
            self.spiked = True
            self.w3.provider.make_request("evm_mine", [])


@pytest.mark.req("TAPI-15")
@pytest.mark.parametrize("manager_cls, expected_gap_fills", [
    (NonceManager, 0),
    (CountingNonceManager, 1),
], ids=["NonceManager", "CountingNonceManager"])
def test_refused_send_mid_tick_leaves_no_gap(system, make_service, hot_wallet, manager_cls, expected_gap_fills):
    """TAPI-15: two transfers in one tick; the node refuses the first. With re-sync the
    second takes the refused nonce and the first retries next tick: no gap at all.
    Without re-sync the second queues behind a hole, and only the gap filler (TAPI-16)
    rescues it: both end confirmed, but one extra filler transaction was needed."""
    a, chain, token = system.accounts, system.chain, system.token
    start = chain.nonce(hot_wallet.address)
    service = make_service(FeeSpikeOnFirstSend, nonces=manager_cls(chain.w3, hot_wallet.address))
    first, _ = service.accept("spike-1", a.payee.address, str(USDC))
    second, _ = service.accept("spike-2", a.payee.address, str(2 * USDC))

    service.tick()
    assert "max fee per gas less than block base fee" in get_detail(service, first["id"])
    for _ in range(3):
        wait_for_attempts(service)
        chain.mine()
        service.tick()

    assert [service.get(t["id"])["status"] for t in (first, second)] == ["confirmed", "confirmed"]
    assert len(service.gap_fills) == expected_gap_fills
    assert token.balance_of(a.payee.address) == 3 * USDC
    assert chain.nonce(hot_wallet.address) == start + 2 + expected_gap_fills


def get_detail(service, transfer_id: str) -> str:
    return " | ".join(h["detail"] or "" for h in service.get(transfer_id)["history"])


@pytest.mark.req("TAPI-16")
def test_gap_with_no_error_is_filled_with_a_self_transfer(system, api, service, hot_wallet):
    """TAPI-16: a nonce is lost without any error (here: handed out and never used).
    The transfer behind it waits in the node's queue. The worker sees an outstanding
    nonce above the chain's, with nothing executable pending, and fills the hole with a
    0-value self-transfer; the transfer then executes."""
    a, chain, token = system.accounts, system.chain, system.token
    start = chain.nonce(hot_wallet.address)
    assert service.nonces.next() == start  # lost: nobody will ever send it
    transfer = post(api, "behind-a-gap", a.payee.address, str(9 * USDC)).json()

    service.tick()
    queued = get(api, transfer["id"])
    assert queued["status"] == "submitted" and queued["nonce"] == start + 1
    assert chain.nonce(hot_wallet.address) == start  # waiting behind the hole

    service.tick()  # fills nonce `start`
    assert service.gap_fills == [start]
    wait_for_attempts(service)
    chain.mine()
    service.tick()

    assert get(api, transfer["id"])["status"] == "confirmed"
    assert token.balance_of(a.payee.address) == 9 * USDC
    assert chain.nonce(hot_wallet.address) == start + 2
    filler = [
        tx
        for n in range(chain.w3.eth.block_number + 1)
        for tx in chain.w3.eth.get_block(n, full_transactions=True)["transactions"]
        if tx["from"] == hot_wallet.address and tx["nonce"] == start
    ]
    assert [(f["to"], f["value"], f["input"]) for f in filler] == [(hot_wallet.address, 0, b"")]
