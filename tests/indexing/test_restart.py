"""IDX-04, IDX-05: stopping and restarting the indexer loses nothing and repeats nothing.

The reference is one `eth_getLogs` over the whole range: the database must hold
exactly those (block_number, log_index) keys, each once, in chain order.
"""

from __future__ import annotations

import allure
import pytest
from sqlalchemy import select

from indexer import Indexer, make_engine
from indexer.db import events
from lib.activity import generate_activity
from lib.indexing import assert_on_canonical_chain, chain_event_keys, stored_event_keys

pytestmark = allure.feature("Indexer: restart")


class SimulatedCrash(RuntimeError):
    pass


class CrashingIndexer(Indexer):
    """Dies inside the transaction of block `crash_at`, after its events are written
    but before the commit: the worst moment for a crash."""

    crash_at: int = -1

    def _before_commit(self, conn, number):
        if number == self.crash_at:
            raise SimulatedCrash(f"killed while storing block {number}")


@pytest.mark.req("IDX-04")
def test_restart_mid_sync_resumes_with_no_missed_or_duplicated_events(system, make_indexer, db_url):
    """IDX-04: sync in chunks of 3 blocks, each chunk by a *new* process (a new engine
    on the same database). Every chunk resumes at the stored tip + 1, so the result
    equals one uninterrupted sync: no gap, no duplicate."""
    generate_activity(system)
    latest = system.chain.w3.eth.block_number
    restarts = 0
    while True:
        indexer = make_indexer(engine=make_engine(db_url))
        before = indexer.tip()
        result = indexer.sync(max_blocks=3)
        indexer.engine.dispose()
        if result.processed == 0:
            break
        assert indexer.tip() == (before + result.processed if before is not None else result.processed - 1)
        restarts += 1

    keys = stored_event_keys(indexer)
    assert restarts >= 5  # the activity spans more than a dozen blocks
    assert indexer.tip() == latest
    assert keys == chain_event_keys(indexer, latest)
    assert len(keys) == len(set(keys))
    assert_on_canonical_chain(indexer)


@pytest.mark.req("IDX-05")
def test_crash_inside_a_block_commits_nothing_and_restart_completes(system, make_indexer, db_url):
    """IDX-05: a crash after a block's events are written but before the commit rolls
    the whole block back. The restarted indexer re-reads that block from the chain, so
    its events appear exactly once."""
    activity = generate_activity(system)
    escrow_open_block = system.escrow.contract.events.Opened().get_logs(
        argument_filters={"id": activity.funded_deal}, from_block=0
    )[0]["blockNumber"]
    latest = system.chain.w3.eth.block_number

    crashing = make_indexer(CrashingIndexer)
    crashing.crash_at = escrow_open_block
    with pytest.raises(SimulatedCrash):
        crashing.sync()
    assert crashing.tip() == escrow_open_block - 1
    with crashing.engine.connect() as conn:
        partial = conn.execute(select(events).where(events.c.block_number == escrow_open_block)).all()
    assert partial == []  # neither the block row nor any of its 2 events survived

    restarted = make_indexer(engine=make_engine(db_url))
    restarted.sync()
    keys = stored_event_keys(restarted)
    assert keys == chain_event_keys(restarted, latest)
    assert len(keys) == len(set(keys))
    assert sum(1 for block, _ in keys if block == escrow_open_block) == 2  # Opened + Transfer


@pytest.mark.req("IDX-04")
def test_sync_twice_is_a_no_op(system, make_indexer):
    """IDX-04: with no new blocks, a second sync stores nothing and changes nothing."""
    generate_activity(system)
    indexer = make_indexer()
    indexer.sync()
    keys = stored_event_keys(indexer)
    again = indexer.sync()
    assert (again.processed, again.reorgs, again.rolled_back) == (0, 0, 0)
    assert stored_event_keys(indexer) == keys
