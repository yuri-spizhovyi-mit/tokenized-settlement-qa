"""IDX-07: amounts are exact integers end to end, far beyond float range."""

from __future__ import annotations

import allure
import pytest
from sqlalchemy import Column, MetaData, Numeric, Table, create_engine, select

from indexer import queries
from indexer.db import Uint256, events
from lib.builders import DealBuilder
from lib.reconcile import reconcile

pytestmark = allure.feature("Indexer: precision")

AMOUNTS = {
    "2^53+1 (first integer a float can't hold)": 2**53 + 1,
    "2^63+1 (above SQLite INTEGER)": 2**63 + 1,
    "10^30+7": 10**30 + 7,
    "2^256-1 (uint256 max)": 2**256 - 1,
}


@pytest.mark.req("IDX-07")
@pytest.mark.parametrize("amount", AMOUNTS.values(), ids=AMOUNTS.keys())
def test_balance_round_trips_exactly(system, make_indexer, amount):
    """IDX-07: mint `amount`, index it, read it back: the stored value, the balance and
    the supply are all exactly `amount`, as Python ints, and reconcile with the chain."""
    a, token = system.accounts, system.token
    token.set_allowlisted(a.compliance, a.payer.address, True)
    token.mint(a.minter, a.payer.address, amount)
    indexer = make_indexer()
    indexer.sync()

    with indexer.engine.connect() as conn:
        stored = conn.execute(select(events.c.value).where(events.c.name == "Transfer")).scalar_one()
    assert type(stored) is int and stored == amount
    assert queries.balances(indexer.engine) == {a.payer.address: amount}
    assert queries.total_supply(indexer.engine) == amount == token.total_supply()
    assert reconcile(token, system.escrow, indexer.engine, indexer.tip()) == []


@pytest.mark.req("IDX-07")
def test_uint128_max_deal_amount_round_trips_exactly(system, make_indexer):
    """IDX-07: the escrow stores amounts as uint128; the largest one survives too."""
    amount = 2**128 - 1
    opened = DealBuilder(system).amount(amount).open()
    indexer = make_indexer()
    indexer.sync()

    assert queries.deals(indexer.engine)[opened.deal_id].amount == amount
    assert queries.funded_total(indexer.engine) == amount == system.token.balance_of(system.escrow.address)


@pytest.mark.req("IDX-07")
def test_sqlite_numeric_column_silently_loses_precision_above_2_pow_63():
    """IDX-07 negative control (F-16): why `Uint256` is TEXT on SQLite. A plain
    NUMERIC(78, 0) column there stores 2^63+1 as a REAL and returns 2^63, no error."""
    engine = create_engine("sqlite://")
    metadata = MetaData()
    table = Table("t", metadata, Column("v", Numeric(78, 0)))
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(table.insert().values(v=2**63 + 1))
        assert conn.execute(select(table.c.v)).scalar() == 2**63  # off by one, silently


@pytest.mark.req("IDX-07")
@pytest.mark.parametrize("bad", [1.0, -1, 2**256, True, "5"])
def test_uint256_column_rejects_anything_but_a_uint256_int(bad):
    """IDX-07: a float, a negative, an overflow, a bool or a string never reaches the
    database: the type refuses it at bind time."""
    dialect = create_engine("sqlite://").dialect
    with pytest.raises((TypeError, ValueError)):
        Uint256().process_bind_param(bad, dialect)
