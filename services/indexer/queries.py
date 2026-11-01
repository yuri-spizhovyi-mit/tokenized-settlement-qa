"""Read models over the indexed events, as of a given block.

Every amount is summed in Python `int`s from exact `Uint256` columns, so the result
is identical on SQLite and Postgres and never touches a float (F-16).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import Engine, select

from .db import blocks, events

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class DealStatus(str, Enum):
    FUNDED = "Funded"
    RELEASED = "Released"
    REFUNDED = "Refunded"


@dataclass(frozen=True)
class IndexedDeal:
    deal_id: int
    payer: str
    payee: str
    amount: int
    deadline: int
    status: DealStatus


@dataclass(frozen=True)
class EventRow:
    block_number: int
    log_index: int
    block_hash: str
    name: str
    address: str


def _rows(engine: Engine, at_block: int | None, names: tuple[str, ...]):
    query = select(events).where(events.c.name.in_(names))
    if at_block is not None:
        query = query.where(events.c.block_number <= at_block)
    query = query.order_by(events.c.block_number, events.c.log_index)
    with engine.connect() as conn:
        return conn.execute(query).mappings().all()


def ordered_events(engine: Engine) -> list[EventRow]:
    """Every stored event in chain order: (block_number, log_index)."""
    query = select(
        events.c.block_number, events.c.log_index, events.c.block_hash, events.c.name, events.c.address
    ).order_by(events.c.block_number, events.c.log_index)
    with engine.connect() as conn:
        return [EventRow(**row) for row in conn.execute(query).mappings()]


def balances(engine: Engine, at_block: int | None = None) -> dict[str, int]:
    """Holder -> balance from Transfer events (mints from, and burns to, address 0)."""
    result: dict[str, int] = defaultdict(int)
    for row in _rows(engine, at_block, ("Transfer",)):
        if row["from_addr"] != ZERO_ADDRESS:
            result[row["from_addr"]] -= row["value"]
        if row["to_addr"] != ZERO_ADDRESS:
            result[row["to_addr"]] += row["value"]
    return dict(result)


def total_supply(engine: Engine, at_block: int | None = None) -> int:
    """Minted minus burned, from Transfer events to and from address 0."""
    supply = 0
    for row in _rows(engine, at_block, ("Transfer",)):
        if row["from_addr"] == ZERO_ADDRESS:
            supply += row["value"]
        if row["to_addr"] == ZERO_ADDRESS:
            supply -= row["value"]
    return supply


def deals(engine: Engine, at_block: int | None = None) -> dict[int, IndexedDeal]:
    """Deal id -> its state, replayed from Opened / Released / Refunded in order."""
    result: dict[int, IndexedDeal] = {}
    for row in _rows(engine, at_block, ("Opened", "Released", "Refunded")):
        if row["name"] == "Opened":
            result[row["deal_id"]] = IndexedDeal(
                row["deal_id"], row["payer"], row["payee"], row["amount"], row["deadline"], DealStatus.FUNDED
            )
        else:
            old = result[row["deal_id"]]
            status = DealStatus.RELEASED if row["name"] == "Released" else DealStatus.REFUNDED
            result[row["deal_id"]] = IndexedDeal(
                old.deal_id, old.payer, old.payee, old.amount, old.deadline, status
            )
    return result


def funded_total(engine: Engine, at_block: int | None = None) -> int:
    """Sum of amounts of deals still Funded: what the escrow should hold."""
    return sum(d.amount for d in deals(engine, at_block).values() if d.status is DealStatus.FUNDED)


def block_hash(engine: Engine, number: int) -> str | None:
    with engine.connect() as conn:
        return conn.execute(select(blocks.c.hash).where(blocks.c.number == number)).scalar()
