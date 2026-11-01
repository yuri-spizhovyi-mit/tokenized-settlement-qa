"""Schema for the transfer API (SQLAlchemy; SQLite in sessions, Postgres in CI).

- `transfers`: one row per accepted request. `idempotency_key` is UNIQUE: the database,
  not application code, decides which of two concurrent requests with the same key
  wins (TAPI-D2).
- `attempts`: every signed transaction for a transfer, written **before** it is
  broadcast (write-ahead). A restart rebroadcasts the stored raw bytes instead of
  signing again, so a crash can never pay twice (F-21).
- `transitions`: the status history, one row per change, so a test (or a support
  engineer) can read exactly how a transfer moved.

Amounts reuse the indexer's exact `Uint256` column (IDX-D3, F-16).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
)

from indexer.db import Uint256

metadata = MetaData()

transfers = Table(
    "transfers",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("request_hash", String(66), nullable=False),  # keccak of the canonical body
    Column("recipient", String(42), nullable=False),
    Column("amount", Uint256, nullable=False),
    Column("status", String(16), nullable=False),
    Column("nonce", BigInteger),
    Column("tx_hash", String(66)),  # the attempt that was mined, once known
    Column("block_number", BigInteger),
    Column("error", Text),
    Column("created_seq", BigInteger, nullable=False),  # time.time_ns(): FIFO order (F-22)
)

attempts = Table(
    "attempts",
    metadata,
    Column("transfer_id", String(36), ForeignKey("transfers.id"), primary_key=True),
    Column("attempt", Integer, primary_key=True, autoincrement=False),
    Column("nonce", BigInteger, nullable=False),
    Column("tx_hash", String(66), nullable=False, unique=True),
    Column("raw_tx", Text, nullable=False),
    Column("max_fee_per_gas", Uint256, nullable=False),
    Column("sent_at_block", BigInteger, nullable=False),
)

transitions = Table(
    "transitions",
    metadata,
    Column("transfer_id", String(36), ForeignKey("transfers.id"), primary_key=True),
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("status", String(16), nullable=False),
    Column("block_number", BigInteger, nullable=False),
    Column("detail", Text),
)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # The API serves requests from a thread pool and the worker runs in its own
        # thread: allow cross-thread use and wait on SQLite's writer lock.
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys = ON")

        return engine
    return create_engine(url)


def create_schema(engine: Engine) -> None:
    metadata.create_all(engine)


def drop_schema(engine: Engine) -> None:
    metadata.drop_all(engine)
