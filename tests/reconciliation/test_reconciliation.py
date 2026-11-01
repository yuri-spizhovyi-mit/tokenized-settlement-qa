"""REC-01..REC-06: the database agrees with the chain, at one pinned confirmed block.

The indexer runs with 2 confirmations, so the pin is `latest - 2`: the newest block
we treat as settled. Anvil's `finalized` tag is block 0 on a dev chain (F-17), so the
pin is the indexer's confirmed tip, and every chain read is pinned to that block's
**hash**. All comparisons are exact integers.
"""

from __future__ import annotations

import allure
import pytest
from sqlalchemy import update

from indexer import create_schema, make_engine, queries
from indexer.db import events
from lib.activity import generate_activity
from lib.builders import USDC
from lib.naive_indexer import NaiveIndexer
from lib.reconcile import reconcile

pytestmark = allure.feature("Reconciliation")

CONFIRMATIONS = 2


@pytest.fixture
def indexed(system, make_indexer):
    """Activity on chain, indexed up to the confirmed block. Returns (indexer, pin, activity)."""
    activity = generate_activity(system)
    system.chain.mine(CONFIRMATIONS)  # so the whole burst is confirmed
    indexer = make_indexer(confirmations=CONFIRMATIONS)
    indexer.sync()
    pin = indexer.tip()
    assert pin == system.chain.w3.eth.block_number - CONFIRMATIONS
    return indexer, pin, activity


@pytest.mark.req("REC-01")
def test_every_holder_balance_matches_balance_of_at_the_pinned_block(system, indexed):
    """REC-01: for each of the 6 holders (and an address that never held tokens),
    balanceOf at the pinned block hash == the indexed balance, exactly."""
    indexer, pin, activity = indexed
    db = queries.balances(indexer.engine, pin)
    assert set(db) == set(activity.holders) | {system.accounts.relayer.address}
    for holder in activity.holders:
        on_chain = system.token.contract.functions.balanceOf(holder).call(block_identifier=pin)
        assert db[holder] == on_chain, holder
    assert reconcile(system.token, system.escrow, indexer.engine, pin,
                     extra_holders=[system.accounts.arbiter.address]) == []


@pytest.mark.req("REC-02")
def test_sum_of_balances_equals_total_supply(system, indexed):
    """REC-02: the indexed balances add up to totalSupply, and so does minted minus
    burned: the 4 USDC burn is reflected on both sides."""
    indexer, pin, _ = indexed
    supply = system.token.contract.functions.totalSupply().call(block_identifier=pin)
    assert sum(queries.balances(indexer.engine, pin).values()) == supply
    assert queries.total_supply(indexer.engine, pin) == supply
    assert supply == 5_000 * USDC + 1_234_567 + 10 * USDC - 4 * USDC


@pytest.mark.req("REC-03")
def test_escrow_balance_equals_sum_of_funded_deals(system, indexed):
    """REC-03: of three deals, one was released and one refunded; the escrow holds
    exactly the one still Funded, and the database says the same."""
    indexer, pin, activity = indexed
    deals = queries.deals(indexer.engine, pin)
    assert [d.status.value for d in deals.values()] == ["Released", "Refunded", "Funded"]
    held = system.token.contract.functions.balanceOf(system.escrow.address).call(block_identifier=pin)
    assert held == queries.funded_total(indexer.engine, pin) == activity.funded_amount


@pytest.mark.req("REC-04")
def test_reconciliation_holds_at_the_pin_while_the_chain_moves_on(system, indexed):
    """REC-04: transfers after the pin (unconfirmed blocks) don't disturb a
    reconciliation pinned to its block, while comparing with `latest` would fail."""
    indexer, pin, _ = indexed
    a = system.accounts
    late = system.token.transfer(a.payer, a.stranger.address, 1 * USDC)
    indexer.sync()
    assert indexer.tip() == late.block_number - CONFIRMATIONS  # the transfer isn't confirmed yet

    assert reconcile(system.token, system.escrow, indexer.engine, pin) == []
    latest_balance = system.token.balance_of(a.stranger.address)
    assert latest_balance != queries.balances(indexer.engine, pin)[a.stranger.address]


@pytest.mark.req("REC-05")
def test_reconciliation_reports_a_one_unit_discrepancy(system, indexed):
    """REC-05: the check can fail. Bump one stored Transfer by 1 unit, the smallest
    possible error. Reconciliation names both holders. The supply checks stay green:
    the unit moved between two holders, so the sum is unchanged. That is why per-holder
    balances are checked and not only totalSupply."""
    indexer, pin, _ = indexed
    a = system.accounts
    with indexer.engine.begin() as conn:
        conn.execute(
            update(events)
            .where(events.c.name == "Transfer", events.c.to_addr == a.stranger.address)
            .values(value=250 * USDC + 1)
        )

    found = {(m.check, m.subject) for m in reconcile(system.token, system.escrow, indexer.engine, pin)}
    assert found == {
        ("balance", a.payer.address),
        ("balance", a.stranger.address),
    }


@pytest.mark.req("REC-06")
def test_naive_indexer_fails_reconciliation_after_a_reorg(system, make_indexer, tmp_path):
    """REC-06: reconciliation catches F-15 end to end. After the same reorg, the
    database of the naive indexer no longer describes the chain; the real one does."""
    a, chain, token = system.accounts, system.chain, system.token
    for account in (a.payer, a.payee, a.stranger):
        token.set_allowlisted(a.compliance, account.address, True)
    token.mint(a.minter, a.payer.address, 1_000 * USDC)
    # Two databases: the real indexer uses the test's (SQLite or CI's Postgres), the
    # naive one a SQLite file of its own.
    naive_engine = make_engine(f"sqlite:///{tmp_path / 'naive.db'}")
    create_schema(naive_engine)
    real, naive = make_indexer(), make_indexer(NaiveIndexer, engine=naive_engine)

    snapshot = chain.snapshot()
    token.transfer(a.payer, a.payee.address, 100 * USDC)
    real.sync()
    naive.sync()
    chain.revert(snapshot)
    token.transfer(a.payer, a.stranger.address, 7 * USDC)
    chain.mine()
    real.sync()
    naive.sync()

    pin = real.tip()
    holders = [a.payer.address, a.payee.address, a.stranger.address]
    assert reconcile(token, system.escrow, real.engine, pin, holders) == []
    # The naive database even has the right hash for the pinned block (it stored H+1
    # without checking its parent), so the block check alone passes. The balances
    # don't: it still holds the abandoned transfer and not the replacement.
    assert naive.tip() == pin
    found = reconcile(token, system.escrow, naive.engine, pin, holders)
    assert [(m.check, m.subject, m.chain, m.db) for m in found] == [
        ("balance", h, chain_value, db_value)
        for h, chain_value, db_value in sorted([
            (a.payer.address, 993 * USDC, 900 * USDC),
            (a.payee.address, 0, 100 * USDC),
            (a.stranger.address, 7 * USDC, 0),
        ])
    ]  # and totalSupply still reconciles: 1,000 USDC either way
