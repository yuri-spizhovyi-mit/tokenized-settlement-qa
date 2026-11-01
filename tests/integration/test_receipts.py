"""INT-09: reverted vs included (book Ch 1.5).

Two ways a transaction "fails", which a client must not confuse:
- **rejected at estimation:** the node simulates it, it reverts, and nothing is
  sent. No nonce is used and no gas is paid. The client sees the decoded error
  immediately.
- **included with status 0:** sent with an explicit gas limit, so there's no
  estimation, and mined. It reverted *on chain*: the nonce is consumed and gas is
  paid, but no state changes and no events are emitted. The receipt carries **no
  revert reason**; the client gets it back by replaying the call against the
  parent block.
"""

from __future__ import annotations

import allure
import pytest

from lib.builders import DealBuilder
from lib.clients import DealState
from lib.errors import expect_revert

pytestmark = [allure.feature("Receipts: reverted vs included"), pytest.mark.req("INT-09")]


def test_rejected_at_estimation_sends_nothing(system):
    a, chain, escrow = system.accounts, system.chain, system.escrow
    opened = DealBuilder(system).open()
    nonce_before = chain.nonce(a.stranger.address)
    eth_before = chain.eth_balance(a.stranger.address)
    block_before = chain.w3.eth.block_number

    with expect_revert("NotArbiter"):
        escrow.release(a.stranger, opened.deal_id)

    assert chain.nonce(a.stranger.address) == nonce_before, "a rejected call must not use a nonce"
    assert chain.eth_balance(a.stranger.address) == eth_before, "no gas paid"
    assert chain.w3.eth.block_number == block_before, "nothing was mined"


def test_included_revert_consumes_nonce_and_gas_but_changes_nothing(system):
    a, chain, token, escrow = system.accounts, system.chain, system.token, system.escrow
    opened = DealBuilder(system).open()
    nonce_before = chain.nonce(a.stranger.address)
    eth_before = chain.eth_balance(a.stranger.address)
    escrow_tokens_before = token.balance_of(escrow.address)

    receipt = chain.send_allow_failure(
        escrow.contract.functions.release(opened.deal_id), a.stranger, gas=200_000
    )

    # It was mined...
    assert receipt["status"] == 0
    assert receipt["blockNumber"] > 0
    # ...so the nonce is consumed and the gas is paid...
    assert chain.nonce(a.stranger.address) == nonce_before + 1
    fee = receipt["gasUsed"] * receipt["effectiveGasPrice"]
    assert fee > 0
    assert chain.eth_balance(a.stranger.address) == eth_before - fee
    assert receipt["gasUsed"] < 200_000, "reverted early, so not all the gas limit was burned"
    # ...but nothing else happened: no logs, no state change.
    assert receipt["logs"] == []
    assert escrow.deal(opened.deal_id).state is DealState.FUNDED
    assert token.balance_of(escrow.address) == escrow_tokens_before


def test_revert_reason_of_included_tx_is_recovered_by_replay(system):
    """The receipt has no reason field. Replaying the same call against the parent
    block's state recovers the exact custom error."""
    a, chain, escrow = system.accounts, system.chain, system.escrow
    opened = DealBuilder(system).open()
    fn = escrow.contract.functions.release(opened.deal_id)

    receipt = chain.send_allow_failure(fn, a.stranger, gas=200_000)
    assert receipt["status"] == 0
    assert "revertReason" not in receipt

    with expect_revert("NotArbiter"):
        chain.call(fn, a.stranger, block=receipt["blockNumber"] - 1)


def test_send_helper_refuses_a_status_zero_receipt(system):
    """The harness's own guard: `Chain.send` must turn a mined revert into a failure,
    or a service-object call could 'succeed' on a status-0 receipt."""
    from lib.chain import ReceiptFailed

    a, chain, escrow = system.accounts, system.chain, system.escrow
    opened = DealBuilder(system).open()
    with pytest.raises(ReceiptFailed):
        chain.send(escrow.contract.functions.release(opened.deal_id), a.stranger, gas=200_000)
