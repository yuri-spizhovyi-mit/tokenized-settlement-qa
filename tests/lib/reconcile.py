"""Chain-vs-database reconciliation at one pinned block (book Ch 3).

`reconcile()` compares, with exact integers:
- the block itself: the database's hash for the pinned height is the chain's hash,
  so both sides describe the same fork;
- per holder: `balanceOf(holder)` at that block == the indexed balance, for every
  holder either side knows about (indexed holders, plus any extra addresses given);
- supply: the sum of indexed balances == `totalSupply()`, and so does mint minus burn;
- escrow: `balanceOf(escrow)` == the sum of amounts of deals the database says are
  still Funded.

Chain reads are `eth_call`s pinned to the block **hash** (EIP-1898), never to
`latest`: the chain keeps moving while we compare, and a number alone could resolve
to a different fork than the one the database indexed.

It returns every mismatch rather than stopping at the first, so a failing test
shows the whole picture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from hexbytes import HexBytes
from sqlalchemy import Engine

from indexer import queries

from .clients import EscrowClient, TokenClient


@dataclass(frozen=True)
class Mismatch:
    check: str
    subject: str
    chain: int | str | None
    db: int | str | None

    def __str__(self) -> str:
        return f"{self.check}[{self.subject}]: chain={self.chain} db={self.db}"


def reconcile(
    token: TokenClient,
    escrow: EscrowClient,
    engine: Engine,
    at_block: int,
    extra_holders: Iterable[str] = (),
) -> list[Mismatch]:
    w3 = token.chain.w3
    mismatches: list[Mismatch] = []

    chain_hash = w3.eth.get_block(at_block)["hash"].to_0x_hex()
    db_hash = queries.block_hash(engine, at_block)
    if chain_hash != db_hash:
        # Different forks: per-holder numbers would be meaningless. Report and stop.
        return [Mismatch("block_hash", str(at_block), chain_hash, db_hash)]
    pin = HexBytes(chain_hash)

    def balance_at(holder: str) -> int:
        return token.contract.functions.balanceOf(holder).call(block_identifier=pin)

    db_balances = queries.balances(engine, at_block)
    for holder in sorted(set(db_balances) | set(extra_holders)):
        on_chain, in_db = balance_at(holder), db_balances.get(holder, 0)
        if on_chain != in_db:
            mismatches.append(Mismatch("balance", holder, on_chain, in_db))

    supply = token.contract.functions.totalSupply().call(block_identifier=pin)
    if sum(db_balances.values()) != supply:
        mismatches.append(Mismatch("sum_of_balances", "totalSupply", supply, sum(db_balances.values())))
    minted_minus_burned = queries.total_supply(engine, at_block)
    if minted_minus_burned != supply:
        mismatches.append(Mismatch("minted_minus_burned", "totalSupply", supply, minted_minus_burned))

    held, funded = balance_at(escrow.address), queries.funded_total(engine, at_block)
    if held != funded:
        mismatches.append(Mismatch("escrow_funded", escrow.address, held, funded))
    return mismatches
