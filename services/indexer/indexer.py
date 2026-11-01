"""A reorg-aware indexer for SettlementToken Transfer and Escrow Opened/Released/Refunded.

Each processed block is stored as `(number, hash, parent_hash)` and its decoded logs
as rows keyed by `(block_number, log_index)`. A block and its events are written in
one database transaction, so a crash leaves either the whole block or none of it.

Reorg handling (book Ch 3), on by default (`check_parent = True`):

1. **Parent check**, before storing block N: its `parentHash` must equal the stored
   hash of N-1. If not, walk back height by height, comparing stored hashes with the
   chain's, to the newest block both agree on (the common ancestor). Delete every
   block above it, and their events, in one transaction. Then re-index forward.
2. **Tip check**, when there is no new block to process: is the block we stored last
   still on the chain? A reorg can replace blocks at the same height (`anvil_reorg`)
   or make the chain shorter (`evm_revert`) without producing a new block, so the
   parent check alone would not see it until the next block arrives.

`confirmations` holds the indexer back from the tip: it only processes blocks up to
`latest - confirmations`. A reorg shallower than that never reaches the database.

Logs are fetched by **block hash** (EIP-234), not by number. If the block is replaced
between `get_block` and `get_logs`, the node answers "unknown block" instead of
returning the new block's logs under the old block's hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eth_utils import event_abi_to_log_topic
from hexbytes import HexBytes
from sqlalchemy import Connection, Engine, delete, func, insert, select
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BlockNotFound

from .db import blocks, events

TOKEN_EVENTS = ("Transfer",)
ESCROW_EVENTS = ("Opened", "Released", "Refunded")


class ReorgTooDeep(RuntimeError):
    """The chain disagrees with every block we stored, back to `start_block`."""


@dataclass
class SyncResult:
    processed: int = 0  # blocks stored in this sync
    rolled_back: int = 0  # blocks deleted because a reorg abandoned them
    reorgs: int = 0  # reorgs detected
    tip: int | None = None  # highest stored block after the sync


def _hex(value: bytes | str) -> str:
    return HexBytes(value).to_0x_hex()


class Indexer:
    #: Compare parent hashes and the stored tip with the chain. NaiveIndexer
    #: (tests/lib/naive_indexer.py) turns this off to show what goes wrong (F-15).
    check_parent = True

    def __init__(
        self,
        w3: Web3,
        engine: Engine,
        token: Contract,
        escrow: Contract,
        *,
        start_block: int = 0,
        confirmations: int = 0,
    ):
        if confirmations < 0:
            raise ValueError("confirmations must be >= 0")
        self.w3 = w3
        self.engine = engine
        self.start_block = start_block
        self.confirmations = confirmations
        self.addresses = [token.address, escrow.address]
        # topic0 -> (emitter address, web3 event class)
        self._decoders: dict[tuple[str, bytes], Any] = {}
        for contract, names in ((token, TOKEN_EVENTS), (escrow, ESCROW_EVENTS)):
            for name in names:
                event_cls = getattr(contract.events, name)
                topic = bytes(event_abi_to_log_topic(event_cls.abi))
                self._decoders[(contract.address, topic)] = event_cls()

    # ---- reads ---------------------------------------------------------------------

    def tip(self) -> int | None:
        """Highest stored block number, or None if nothing is stored yet."""
        with self.engine.connect() as conn:
            return conn.execute(select(func.max(blocks.c.number))).scalar()

    def stored_hash(self, number: int, conn: Connection | None = None) -> str | None:
        query = select(blocks.c.hash).where(blocks.c.number == number)
        if conn is not None:
            return conn.execute(query).scalar()
        with self.engine.connect() as c:
            return c.execute(query).scalar()

    def target(self) -> int:
        """Newest block the indexer may process: `latest - confirmations`."""
        return self.w3.eth.block_number - self.confirmations

    # ---- sync ----------------------------------------------------------------------

    def sync(self, max_blocks: int | None = None) -> SyncResult:
        """Index forward to `target()`, storing at most `max_blocks` new blocks.

        Restart-safe: it always resumes from the stored tip. When there is nothing new
        to process it checks that the stored tip is still canonical (a reorg can replace
        blocks without making the chain longer) and re-indexes if it is not."""
        result = SyncResult()
        while True:
            tip = self.tip()
            number = self.start_block if tip is None else tip + 1
            if max_blocks is not None and result.processed >= max_blocks:
                break
            if number > self.target():
                if self.check_parent and tip is not None and self._chain_hash(tip) != self.stored_hash(tip):
                    self._roll_back(tip, result)
                    continue
                break
            block = self.w3.eth.get_block(number)
            if self.check_parent and tip is not None and _hex(block["parentHash"]) != self.stored_hash(tip):
                self._roll_back(tip, result)
                continue
            self._store(block)
            result.processed += 1
        result.tip = self.tip()
        return result

    def _chain_hash(self, number: int) -> str | None:
        try:
            return _hex(self.w3.eth.get_block(number)["hash"])
        except BlockNotFound:
            return None  # the chain is now shorter than our stored history

    def _roll_back(self, height: int, result: SyncResult) -> int:
        """Walk back from `height` to the common ancestor, delete everything above it,
        and return the ancestor's number (start_block - 1 if nothing is left)."""
        ancestor = height
        while ancestor >= self.start_block and self._chain_hash(ancestor) != self.stored_hash(ancestor):
            ancestor -= 1
        if ancestor < self.start_block and self.stored_hash(self.start_block) is not None:
            # Not even start_block is canonical any more: we can't know the state before it.
            raise ReorgTooDeep(f"chain diverges at or before start block {self.start_block}")
        with self.engine.begin() as conn:
            conn.execute(delete(events).where(events.c.block_number > ancestor))
            removed = conn.execute(delete(blocks).where(blocks.c.number > ancestor)).rowcount
        result.rolled_back += removed
        result.reorgs += 1
        return ancestor

    def _store(self, block: Any) -> None:
        block_hash = _hex(block["hash"])
        logs = self.w3.eth.get_logs({"blockHash": block["hash"], "address": self.addresses})
        rows = []
        for log in logs:
            assert _hex(log["blockHash"]) == block_hash and log["blockNumber"] == block["number"], (
                f"log {log['logIndex']} is from another block"
            )
            row = self.decode(log)
            if row is not None:
                rows.append(row)

        with self.engine.begin() as conn:
            conn.execute(
                insert(blocks).values(
                    number=block["number"], hash=block_hash, parent_hash=_hex(block["parentHash"])
                )
            )
            if rows:
                conn.execute(insert(events), rows)
            self._before_commit(conn, block["number"])

    def _before_commit(self, conn: Connection, number: int) -> None:
        """Hook inside the block's transaction, after its rows are written. Tests use it
        to simulate a crash mid-block (IDX-04)."""

    def decode(self, log: Any) -> dict[str, Any] | None:
        if not log["topics"]:
            return None
        decoder = self._decoders.get((Web3.to_checksum_address(log["address"]), bytes(log["topics"][0])))
        if decoder is None:
            return None  # an event we don't index (Approval, RoleGranted, ...)
        decoded = decoder.process_log(log)
        args = decoded["args"]
        row: dict[str, Any] = {
            "block_number": log["blockNumber"],
            "log_index": log["logIndex"],
            "block_hash": _hex(log["blockHash"]),
            "tx_hash": _hex(log["transactionHash"]),
            "address": Web3.to_checksum_address(log["address"]),
            "name": decoded["event"],
            "from_addr": None,
            "to_addr": None,
            "value": None,
            "deal_id": None,
            "payer": None,
            "payee": None,
            "amount": None,
            "deadline": None,
        }
        name = decoded["event"]
        if name == "Transfer":
            row.update(from_addr=args["from"], to_addr=args["to"], value=int(args["value"]))
        elif name == "Opened":
            row.update(
                deal_id=int(args["id"]), payer=args["payer"], payee=args["payee"],
                amount=int(args["amount"]), deadline=int(args["deadline"]),
            )
        elif name == "Released":
            row.update(deal_id=int(args["id"]), payee=args["payee"], amount=int(args["amount"]))
        elif name == "Refunded":
            row.update(deal_id=int(args["id"]), payer=args["payer"], amount=int(args["amount"]))
        return row
