"""Assertions and reads shared by the indexer and reconciliation tests."""

from __future__ import annotations

from sqlalchemy import select

from indexer import Indexer
from indexer.db import blocks, events


def stored_blocks(indexer: Indexer) -> dict[int, str]:
    with indexer.engine.connect() as conn:
        return dict(conn.execute(select(blocks.c.number, blocks.c.hash)).all())


def stored_event_keys(indexer: Indexer) -> list[tuple[int, int]]:
    with indexer.engine.connect() as conn:
        query = select(events.c.block_number, events.c.log_index).order_by(
            events.c.block_number, events.c.log_index
        )
        return [tuple(row) for row in conn.execute(query).all()]


def chain_event_keys(indexer: Indexer, to_block: int) -> list[tuple[int, int]]:
    """(block, log_index) of every event the indexer should have stored, straight from
    the node with one eth_getLogs: the reference the database must equal."""
    logs = indexer.w3.eth.get_logs(
        {"fromBlock": indexer.start_block, "toBlock": to_block, "address": indexer.addresses}
    )
    return [(log["blockNumber"], log["logIndex"]) for log in logs if indexer.decode(log) is not None]


def assert_on_canonical_chain(indexer: Indexer) -> None:
    """Every stored block, and the block of every stored event, is on the chain now."""
    chain_hash = {n: indexer.w3.eth.get_block(n)["hash"].to_0x_hex() for n in stored_blocks(indexer)}
    assert stored_blocks(indexer) == chain_hash
    with indexer.engine.connect() as conn:
        for number, block_hash in conn.execute(select(events.c.block_number, events.c.block_hash)):
            assert block_hash == chain_hash[number], f"event from abandoned block {number}"
