"""INT-08: deadline boundaries with time travel on a real node.

Coarse travel uses `evm_increaseTime` + `evm_mine`. Anvil keeps adding wall-clock
time on top of the offset, so that can't land on an exact second (F-12). Boundary
assertions therefore pin the block timestamp exactly:
- a *revert* at second T: mine a block at T, then eth_call against it (`call_at`);
- a *success* at second T: pin the next block to T and send with explicit gas
  (`send_at`), because eth_estimateGas ignores the pinned time (F-14). The test
  then asserts the block really was mined at T.
"""

from __future__ import annotations

import allure
import pytest

from lib.builders import DAY, HOUR, DealBuilder
from lib.clients import DealState
from lib import eip712
from lib.errors import expect_revert

pytestmark = [allure.feature("Deadlines and time travel"), pytest.mark.req("INT-08")]


def test_increase_time_moves_clock_forward_at_least_requested(system):
    """Coarse travel: at least the requested seconds pass, possibly a few more."""
    chain = system.chain
    before = chain.now()
    after = chain.increase_time(DAY)
    assert DAY <= after - before <= DAY + 30, f"moved {after - before}s"


def test_refund_one_second_before_deadline_reverts_too_early(system):
    a, chain, escrow = system.accounts, system.chain, system.escrow
    opened = DealBuilder(system).deadline_in(DAY).open()
    deadline = opened.event["deadline"]
    chain.increase_time(DAY - HOUR)  # most of the way, coarsely

    with expect_revert("TooEarly", deadline):
        chain.call_at(deadline - 1, escrow.contract.functions.refund(opened.deal_id), a.payer)

    assert chain.now() == deadline - 1, "the check must run at exactly deadline - 1"
    assert escrow.deal(opened.deal_id).state is DealState.FUNDED


def test_refund_exactly_at_deadline_succeeds(system):
    a, chain, escrow = system.accounts, system.chain, system.escrow
    opened = DealBuilder(system).deadline_in(DAY).open()
    deadline = opened.event["deadline"]

    refunded = escrow.refund(a.payer, opened.deal_id, at=deadline)  # asserts mined at deadline

    assert chain.w3.eth.get_block(refunded.block_number)["timestamp"] == deadline
    assert escrow.deal(opened.deal_id).state is DealState.REFUNDED


def test_signed_release_valid_at_its_deadline_and_expired_one_second_later(system):
    """The signature deadline is inclusive (SEC-D3): valid at `deadline`, expired at
    `deadline + 1` with SignatureExpired(deadline)."""
    a, chain, escrow = system.accounts, system.chain, system.escrow
    first = DealBuilder(system).open()
    second = DealBuilder(system).open()
    chain_id = chain.w3.eth.chain_id
    sig_deadline = chain.now() + HOUR

    sig_first = eip712.sign_release(a.arbiter, chain_id, escrow.address, first.deal_id, 0, sig_deadline)
    escrow.release_with_sig(a.relayer, first.deal_id, sig_deadline, sig_first, at=sig_deadline)

    sig_second = eip712.sign_release(a.arbiter, chain_id, escrow.address, second.deal_id, 1, sig_deadline)
    fn = escrow.contract.functions.releaseWithSig(second.deal_id, sig_deadline, sig_second)
    with expect_revert("SignatureExpired", sig_deadline):
        chain.call_at(sig_deadline + 1, fn, a.relayer)
