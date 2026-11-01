"""INT-01..INT-04: deploy from artifacts, then open, release and refund end to end.

Each test asserts chain state (balances, deal struct, supply) *and* the decoded
events, against a real node over JSON-RPC: the path a production client takes.
"""

from __future__ import annotations

import allure
import pytest

from lib import artifacts
from lib.builders import DAY, USDC, DealBuilder
from lib.clients import DealState

pytestmark = allure.feature("Escrow lifecycle")


@pytest.mark.req("INT-01")
def test_deploy_runtime_code_matches_forge_artifact(system):
    """INT-01: what's on chain is what `forge build` produced, not a hand-copied ABI.
    SettlementToken has no immutables, so its runtime code must equal the artifact's
    deployedBytecode byte for byte."""
    import json

    deployed = system.chain.w3.eth.get_code(system.token.address)
    artifact_json = json.loads((artifacts.OUT_DIR / "SettlementToken.sol" / "SettlementToken.json").read_text())
    assert deployed.to_0x_hex() == artifact_json["deployedBytecode"]["object"]
    # Escrow has immutables (token, EIP-712 cache), so compare wiring instead.
    assert system.escrow.contract.functions.token().call() == system.token.address
    assert system.escrow.arbiter() == system.accounts.arbiter.address


@pytest.mark.req("INT-02")
def test_open_records_deal_pulls_tokens_and_emits_opened(system):
    """INT-02: open moves exactly the amount payer -> escrow, records every field and
    emits Opened(id, payer, payee, amount, deadline) from the escrow address."""
    a, token, escrow = system.accounts, system.token, system.escrow
    builder = DealBuilder(system).amount(250 * USDC).deadline_in(7 * DAY)
    deadline = builder.prepare()
    payer_before, escrow_before = token.balance_of(a.payer.address), token.balance_of(escrow.address)
    supply_before = token.total_supply()

    opened = escrow.open(a.payer, a.payee.address, 250 * USDC, deadline)

    assert opened.event.emitter == escrow.address
    assert opened.event.args == {
        "id": 0,
        "payer": a.payer.address,
        "payee": a.payee.address,
        "amount": 250 * USDC,
        "deadline": deadline,
    }
    assert opened.transfer.emitter == token.address
    assert opened.transfer.args == {"from": a.payer.address, "to": escrow.address, "value": 250 * USDC}
    assert escrow.deal(0) == escrow.deal(opened.deal_id)
    deal = escrow.deal(opened.deal_id)
    assert (deal.payer, deal.payee, deal.amount, deal.deadline, deal.state) == (
        a.payer.address, a.payee.address, 250 * USDC, deadline, DealState.FUNDED,
    )
    assert token.balance_of(a.payer.address) == payer_before - 250 * USDC
    assert token.balance_of(escrow.address) == escrow_before + 250 * USDC
    assert token.total_supply() == supply_before
    assert escrow.next_id() == 1


@pytest.mark.req("INT-03")
def test_release_pays_payee_and_emits_released(system):
    """INT-03: the arbiter's release moves the full amount to the payee, empties the
    escrow and marks the deal Released."""
    a, token, escrow = system.accounts, system.token, system.escrow
    opened = DealBuilder(system).amount(100 * USDC).open()
    payee_before = token.balance_of(a.payee.address)

    released = escrow.release(a.arbiter, opened.deal_id)

    assert released.emitter == escrow.address
    assert released.args == {"id": opened.deal_id, "payee": a.payee.address, "amount": 100 * USDC}
    assert token.balance_of(a.payee.address) == payee_before + 100 * USDC
    assert token.balance_of(escrow.address) == 0
    assert escrow.deal(opened.deal_id).state is DealState.RELEASED


@pytest.mark.req("INT-04")
def test_refund_after_deadline_returns_funds_to_payer(system):
    """INT-04: after the deadline, any caller (here a relayer) refunds; the money goes
    to the payer, never to the caller."""
    a, token, escrow = system.accounts, system.token, system.escrow
    opened = DealBuilder(system).amount(75 * USDC).deadline_in(DAY).open()
    payer_before = token.balance_of(a.payer.address)
    relayer_before = token.balance_of(a.relayer.address)

    refunded = escrow.refund(a.relayer, opened.deal_id, at=opened.event["deadline"])

    assert refunded.args == {"id": opened.deal_id, "payer": a.payer.address, "amount": 75 * USDC}
    assert token.balance_of(a.payer.address) == payer_before + 75 * USDC
    assert token.balance_of(a.relayer.address) == relayer_before
    assert escrow.deal(opened.deal_id).state is DealState.REFUNDED


@pytest.mark.req("INT-11")
def test_snapshot_isolation_resets_state_and_nonces(system):
    """INT-11: evm_snapshot/evm_revert restores balances, deals *and* account nonces,
    which is what lets every test start from the same deployed state."""
    a, chain, escrow = system.accounts, system.chain, system.escrow
    snapshot = chain.snapshot()
    nonce_before = chain.nonce(a.payer.address)

    DealBuilder(system).open()
    assert escrow.next_id() == 1
    assert chain.nonce(a.payer.address) > nonce_before

    chain.revert(snapshot)
    assert escrow.next_id() == 0
    assert chain.nonce(a.payer.address) == nonce_before
