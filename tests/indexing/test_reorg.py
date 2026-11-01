"""IDX-01..IDX-03, IDX-08, IDX-09: the indexer survives reorgs; a naive one does not.

Two ways to make a reorg on Anvil:
- **snapshot**: `evm_snapshot`, mine block H, index it, `evm_revert`, mine a
  *different* block H' at the same height, then H+1 on top of it;
- **anvil_reorg**: replace the last N blocks in place with blocks carrying other
  transactions. The height doesn't change, so no new block announces the reorg.

Every reorg test runs against `Indexer` and `NaiveIndexer` (no parent-hash check).
The naive run is `xfail(strict=True)`: if it ever *passes*, the test is no longer
able to see a reorg, and CI goes red (docs/findings.md, F-15).
"""

from __future__ import annotations

import allure
import pytest

from indexer import Indexer, ReorgTooDeep, queries
from lib.builders import USDC
from lib.naive_indexer import NaiveIndexer
from lib.reconcile import reconcile

from lib.indexing import assert_on_canonical_chain, chain_event_keys, stored_blocks, stored_event_keys

pytestmark = allure.feature("Indexer: reorgs")

NAIVE_FAILS = pytest.mark.xfail(
    strict=True, raises=AssertionError,
    reason="F-15: without the parent-hash check the abandoned block stays in the database",
)
IMPLEMENTATIONS = [Indexer, pytest.param(NaiveIndexer, marks=NAIVE_FAILS, id="NaiveIndexer")]


def _fund(system) -> None:
    a, token = system.accounts, system.token
    for account in (a.payer, a.payee, a.stranger):
        token.set_allowlisted(a.compliance, account.address, True)
    token.mint(a.minter, a.payer.address, 1_000 * USDC)


def _reorg_by_snapshot(system, indexer):
    """Index block H (payer -> payee 100), then replace it with H' (payer -> stranger 7)
    and mine H+1 on top. Returns (H, abandoned hash, the sync result after the reorg)."""
    a, chain, token = system.accounts, system.chain, system.token
    _fund(system)
    indexer.sync()
    snapshot = chain.snapshot()
    abandoned = token.transfer(a.payer, a.payee.address, 100 * USDC)
    height = abandoned.block_number
    abandoned_hash = chain.block_hash(height)
    indexer.sync()
    assert stored_blocks(indexer)[height] == abandoned_hash  # the indexer did see it

    chain.revert(snapshot)
    replacement = token.transfer(a.payer, a.stranger.address, 7 * USDC)
    assert replacement.block_number == height and chain.block_hash(height) != abandoned_hash
    chain.mine()  # H+1, whose parent is H'
    return height, abandoned_hash, indexer.sync()


def _assert_replacement_indexed(system, indexer) -> None:
    a = system.accounts
    assert_on_canonical_chain(indexer)
    assert indexer.tip() == system.chain.w3.eth.block_number - indexer.confirmations
    assert stored_event_keys(indexer) == chain_event_keys(indexer, indexer.tip())
    assert queries.balances(indexer.engine) == {a.payer.address: 993 * USDC, a.stranger.address: 7 * USDC}
    assert reconcile(system.token, system.escrow, indexer.engine, indexer.tip(), [a.payee.address]) == []


@pytest.mark.req("IDX-01")
@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_reorg_by_snapshot_drops_abandoned_block_and_indexes_replacement(system, make_indexer, impl):
    """IDX-01: H+1's parentHash is H', not the stored H. The indexer walks back to H-1
    (the common ancestor), deletes H and its Transfer, and indexes H' and H+1."""
    indexer = make_indexer(impl)
    height, abandoned_hash, result = _reorg_by_snapshot(system, indexer)

    # Data first, counters second: the naive run must fail on *what it stored*.
    _assert_replacement_indexed(system, indexer)
    assert stored_blocks(indexer)[height] != abandoned_hash
    assert (result.reorgs, result.rolled_back, result.processed) == (1, 1, 2)


@pytest.mark.req("IDX-02")
@pytest.mark.parametrize("confirmations", [0, 1])
@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_anvil_reorg_at_same_height_is_detected_without_a_new_block(system, make_indexer, impl, confirmations):
    """IDX-02: anvil_reorg replaces the last 3 blocks without changing the height, so no
    new block's parentHash can announce it. The tip check (is the stored tip still the
    chain's block at that height?) finds it, including below a confirmation depth."""
    a, chain, token = system.accounts, system.chain, system.token
    _fund(system)
    abandoned = token.transfer(a.payer, a.payee.address, 100 * USDC)
    chain.mine(2)
    indexer = make_indexer(impl, confirmations=confirmations)
    indexer.sync()
    height_before = chain.w3.eth.block_number
    abandoned_hash = chain.block_hash(abandoned.block_number)
    assert queries.balances(indexer.engine)[a.payee.address] == 100 * USDC

    replacement = token.contract.functions.transfer(a.stranger.address, 7 * USDC).build_transaction(
        {"from": a.payer.address, "gas": 200_000}
    )
    chain.anvil_reorg(3, [({k: replacement[k] for k in ("from", "to", "data", "gas")}, 0)])
    assert chain.w3.eth.block_number == height_before
    assert chain.block_hash(abandoned.block_number) != abandoned_hash

    result = indexer.sync()

    _assert_replacement_indexed(system, indexer)
    assert result.reorgs == 1 and result.rolled_back == 3 - confirmations


@pytest.mark.req("IDX-03")
def test_naive_indexer_keeps_abandoned_transfer_and_misses_replacement(system, make_indexer):
    """IDX-03 (F-15): pins *why* the naive run fails. It never re-reads height H, so
    the database still holds the abandoned payer -> payee 100 and not the canonical
    payer -> stranger 7, and reconciliation reports the fork."""
    a = system.accounts
    indexer = make_indexer(NaiveIndexer)
    height, abandoned_hash, result = _reorg_by_snapshot(system, indexer)

    assert (result.reorgs, result.rolled_back, result.processed) == (0, 0, 1)
    assert stored_blocks(indexer)[height] == abandoned_hash  # a block that no longer exists
    assert queries.balances(indexer.engine) == {a.payer.address: 900 * USDC, a.payee.address: 100 * USDC}
    mismatches = reconcile(system.token, system.escrow, indexer.engine, height)
    assert [m.check for m in mismatches] == ["block_hash"]


@pytest.mark.req("IDX-08")
def test_reorg_shallower_than_confirmations_never_reaches_the_database(system, make_indexer):
    """IDX-08: with 2 confirmations the indexer stops at latest - 2, so a 1-block reorg
    at the tip replaces a block it never stored: no rollback, nothing to undo."""
    a, chain, token = system.accounts, system.chain, system.token
    _fund(system)
    indexer = make_indexer(confirmations=2)
    abandoned = token.transfer(a.payer, a.payee.address, 100 * USDC)
    first = indexer.sync()
    assert indexer.tip() == abandoned.block_number - 2 == indexer.target()
    assert abandoned.block_number not in stored_blocks(indexer)

    replacement = token.contract.functions.transfer(a.stranger.address, 7 * USDC).build_transaction(
        {"from": a.payer.address, "gas": 200_000}
    )
    chain.anvil_reorg(1, [({k: replacement[k] for k in ("from", "to", "data", "gas")}, 0)])
    chain.mine(2)
    second = indexer.sync()

    assert first.reorgs == second.reorgs == 0 and second.rolled_back == 0
    assert indexer.tip() == abandoned.block_number
    _assert_replacement_indexed(system, indexer)


@pytest.mark.req("IDX-09")
def test_reorg_below_start_block_raises_reorg_too_deep(system, make_indexer):
    """IDX-09: if even start_block is replaced, the indexer can't know the state before
    it. It must stop loudly rather than rebuild from a guess."""
    a, chain, token = system.accounts, system.chain, system.token
    _fund(system)
    snapshot = chain.snapshot()
    start = token.transfer(a.payer, a.payee.address, 100 * USDC).block_number
    indexer = make_indexer(start_block=start)
    indexer.sync()
    chain.revert(snapshot)
    token.transfer(a.payer, a.stranger.address, 7 * USDC)
    chain.mine()

    with pytest.raises(ReorgTooDeep, match=f"start block {start}"):
        indexer.sync()
    assert stored_blocks(indexer) == {start: stored_blocks(indexer)[start]}  # nothing deleted
