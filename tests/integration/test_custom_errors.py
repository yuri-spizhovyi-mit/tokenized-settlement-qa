"""INT-05: custom errors are decoded in Python and asserted by name *and* arguments.

A client that only knows "it reverted" can't tell a user to wait (TooEarly), to
contact compliance (AccountNotAllowlisted), or that the deal already settled
(WrongState). These tests pin the error a real JSON-RPC client receives. The
`AccountNotAllowlisted` cases come from the *token*, bubbled through Escrow's
SafeERC20 call, and still decode by name.
"""

from __future__ import annotations

import allure
import pytest

from lib.builders import DAY, DealBuilder
from lib.clients import DealState
from lib.errors import expect_revert

pytestmark = [allure.feature("Custom errors"), pytest.mark.req("INT-05")]


def test_not_arbiter_decoded(system):
    a, escrow = system.accounts, system.escrow
    opened = DealBuilder(system).open()

    with expect_revert("NotArbiter"):
        escrow.release(a.stranger, opened.deal_id)

    assert escrow.deal(opened.deal_id).state is DealState.FUNDED


def test_wrong_state_decoded_with_enum_argument(system):
    """WrongState carries the state it found: Released = 1."""
    a, escrow = system.accounts, system.escrow
    opened = DealBuilder(system).open()
    escrow.release(a.arbiter, opened.deal_id)

    with expect_revert("WrongState", DealState.RELEASED):
        escrow.release(a.arbiter, opened.deal_id)


def test_too_early_decoded_with_deadline(system):
    """TooEarly carries the deadline: the client can show when to retry."""
    a, escrow = system.accounts, system.escrow
    opened = DealBuilder(system).deadline_in(DAY).open()

    with expect_revert("TooEarly", opened.event["deadline"]):
        escrow.refund(a.payer, opened.deal_id)


def test_account_not_allowlisted_bubbles_from_token_through_escrow(system):
    """The escrow isn't allowlisted, so the token rejects the escrow as spender during
    open; SafeERC20 forwards the token's revert data, and it decodes by name."""
    a, escrow = system.accounts, system.escrow
    builder = DealBuilder(system).without_allowlisting(escrow.address)
    deadline = builder.prepare()

    with expect_revert("AccountNotAllowlisted", escrow.address):
        escrow.open(a.payer, a.payee.address, 250 * 10**6, deadline)

    assert escrow.next_id() == 0


def test_account_not_allowlisted_for_delisted_payee_on_release(system):
    a, token, escrow = system.accounts, system.token, system.escrow
    opened = DealBuilder(system).open()
    token.set_allowlisted(a.compliance, a.payee.address, False)

    with expect_revert("AccountNotAllowlisted", a.payee.address):
        escrow.release(a.arbiter, opened.deal_id)

    assert escrow.deal(opened.deal_id).state is DealState.FUNDED
    assert token.balance_of(escrow.address) == opened.event["amount"]


def test_openzeppelin_error_decoded_too(system):
    """Errors inherited from OpenZeppelin (in the token's ABI) decode the same way."""
    a, token = system.accounts, system.token
    with expect_revert("AccessControlUnauthorizedAccount", a.stranger.address, token.MINTER_ROLE):
        token.mint(a.stranger, a.payee.address, 1)


def test_expect_revert_rejects_the_wrong_error(system):
    """The helper itself must fail when the error is right but the argument is wrong,
    or the whole suite could pass on the wrong revert."""
    a, escrow = system.accounts, system.escrow
    opened = DealBuilder(system).deadline_in(DAY).open()

    with pytest.raises(AssertionError, match="expected TooEarly"):
        with expect_revert("TooEarly", opened.event["deadline"] + 1):
            escrow.refund(a.payer, opened.deal_id)
    with pytest.raises(AssertionError, match="got TooEarly"):
        with expect_revert("NotArbiter"):
            escrow.refund(a.payer, opened.deal_id)
