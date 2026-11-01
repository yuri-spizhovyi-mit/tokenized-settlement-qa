"""IDX-06: events in the same block keep the chain's order, by log_index."""

from __future__ import annotations

import allure
import pytest

from indexer import queries
from lib.builders import DAY, USDC

pytestmark = allure.feature("Indexer: ordering")


@pytest.mark.req("IDX-06")
def test_events_in_one_block_are_ordered_by_log_index(system, make_indexer):
    """IDX-06: three transactions mined in one block emit four indexed events from two
    contracts: Transfer, then open's Opened (escrow) and Transfer (token), then a
    Transfer. The database returns them in log_index order, the same as the node,
    and replaying them in that order gives the right balances."""
    a, chain, token, escrow = system.accounts, system.chain, system.token, system.escrow
    for account in (a.payer, a.payee, a.stranger):
        token.set_allowlisted(a.compliance, account.address, True)
    token.mint(a.minter, a.payer.address, 1_000 * USDC)
    token.approve(a.payer, escrow.address, 400 * USDC)
    deadline = chain.now() + DAY

    receipts = chain.send_in_one_block([
        (token.contract.functions.transfer(a.payee.address, 5 * USDC), a.payer),
        (escrow.contract.functions.open(a.payee.address, 400 * USDC, deadline), a.payer),
        (token.contract.functions.transfer(a.stranger.address, 2 * USDC), a.payer),
    ])
    block = receipts[0]["blockNumber"]
    indexer = make_indexer()
    indexer.sync()

    in_block = [e for e in queries.ordered_events(indexer.engine) if e.block_number == block]
    chain_logs = [log for r in receipts for log in r["logs"] if indexer.decode(log) is not None]
    assert [e.log_index for e in in_block] == [log["logIndex"] for log in chain_logs]
    assert [e.log_index for e in in_block] == sorted(e.log_index for e in in_block)
    assert [(e.name, e.address) for e in in_block] == [
        ("Transfer", token.address),
        ("Opened", escrow.address),
        ("Transfer", token.address),
        ("Transfer", token.address),
    ]
    assert queries.balances(indexer.engine, block) == {
        a.payer.address: 593 * USDC,
        a.payee.address: 5 * USDC,
        escrow.address: 400 * USDC,
        a.stranger.address: 2 * USDC,
    }
