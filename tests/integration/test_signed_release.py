"""INT-06, INT-07: EIP-712 signed release from Python.

The arbiter signs off-chain with eth_account; a relayer that holds no role submits
it. Python and Solidity must agree on the typehash and the domain separator, or
every wallet-signed release in production fails with InvalidSignature.
"""

from __future__ import annotations

import allure
import pytest

from lib import eip712
from lib.builders import HOUR, DealBuilder
from lib.clients import DealState
from lib.errors import expect_revert

pytestmark = allure.feature("Signed release (EIP-712)")


@pytest.mark.req("INT-07")
def test_python_and_solidity_agree_on_typehash(system):
    assert system.escrow.release_typehash() == eip712.RELEASE_TYPEHASH


@pytest.mark.req("INT-07")
def test_python_and_solidity_agree_on_domain_separator(system):
    chain_id = system.chain.w3.eth.chain_id
    assert system.escrow.domain_separator() == eip712.domain_separator(chain_id, system.escrow.address)


@pytest.mark.req("INT-06")
def test_relayer_submits_arbiter_signature(system):
    """INT-06: signed in Python, submitted by a third account; the payee is paid, the
    relayer gains nothing, the arbiter's nonce advances by one."""
    a, chain, token, escrow = system.accounts, system.chain, system.token, system.escrow
    opened = DealBuilder(system).open()
    sig_deadline = chain.now() + HOUR
    nonce = escrow.nonces(a.arbiter.address)
    signature = eip712.sign_release(
        a.arbiter, chain.w3.eth.chain_id, escrow.address, opened.deal_id, nonce, sig_deadline
    )
    payee_before = token.balance_of(a.payee.address)

    released = escrow.release_with_sig(a.relayer, opened.deal_id, sig_deadline, signature)

    assert released.args == {"id": opened.deal_id, "payee": a.payee.address, "amount": opened.event["amount"]}
    assert token.balance_of(a.payee.address) == payee_before + opened.event["amount"]
    assert token.balance_of(a.relayer.address) == 0
    assert escrow.nonces(a.arbiter.address) == nonce + 1
    assert escrow.deal(opened.deal_id).state is DealState.RELEASED


@pytest.mark.req("INT-06")
def test_replaying_the_same_signature_is_rejected(system):
    """INT-06: the second submission of the exact same bytes fails with
    InvalidSignature: the nonce moved on, so the digest no longer matches."""
    a, chain, escrow = system.accounts, system.chain, system.escrow
    opened = DealBuilder(system).open()
    sig_deadline = chain.now() + HOUR
    signature = eip712.sign_release(
        a.arbiter, chain.w3.eth.chain_id, escrow.address, opened.deal_id,
        escrow.nonces(a.arbiter.address), sig_deadline,
    )
    escrow.release_with_sig(a.relayer, opened.deal_id, sig_deadline, signature)

    with expect_revert("InvalidSignature"):
        escrow.release_with_sig(a.stranger, opened.deal_id, sig_deadline, signature)

    assert escrow.nonces(a.arbiter.address) == 1


@pytest.mark.req("INT-06")
def test_signature_by_non_arbiter_is_rejected(system):
    a, chain, escrow = system.accounts, system.chain, system.escrow
    opened = DealBuilder(system).open()
    sig_deadline = chain.now() + HOUR
    forged = eip712.sign_release(
        a.stranger, chain.w3.eth.chain_id, escrow.address, opened.deal_id, 0, sig_deadline
    )

    with expect_revert("InvalidSignature"):
        escrow.release_with_sig(a.relayer, opened.deal_id, sig_deadline, forged)

    assert escrow.deal(opened.deal_id).state is DealState.FUNDED


@pytest.mark.req("INT-06")
def test_signature_for_wrong_chain_id_is_rejected(system):
    a, chain, escrow = system.accounts, system.chain, system.escrow
    opened = DealBuilder(system).open()
    sig_deadline = chain.now() + HOUR
    signature = eip712.sign_release(
        a.arbiter, chain.w3.eth.chain_id + 1, escrow.address, opened.deal_id, 0, sig_deadline
    )

    with expect_revert("InvalidSignature"):
        escrow.release_with_sig(a.relayer, opened.deal_id, sig_deadline, signature)
