"""Database schema for the indexer (SQLAlchemy 2.x, SQLite or Postgres).

Two tables:
- `blocks`: one row per processed block, `(number, hash, parent_hash)`. This is what
  reorg detection compares against.
- `events`: one row per decoded log, keyed by `(block_number, log_index)`, the order
  the chain itself defines. `block_hash` is stored too, so every row says which fork
  it came from.

Amounts are `Uint256`: NUMERIC(78, 0) on Postgres, TEXT on SQLite, `int` in Python.
Never a float, and never SQLite NUMERIC, which silently turns anything above 2^63
into a REAL (docs/findings.md, F-16).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Column,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    event,
)
from sqlalchemy.types import TypeDecorator

# A uint256 has at most 78 decimal digits.
UINT256_DIGITS = 78


class Uint256(TypeDecorator):
    """An exact unsigned 256-bit integer. Python side: `int`, always."""

    impl = Numeric(UINT256_DIGITS, 0)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(UINT256_DIGITS))
        return dialect.type_descriptor(Numeric(UINT256_DIGITS, 0, asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"Uint256 takes an int, got {type(value).__name__}")
        if not 0 <= value < 2**256:
            raise ValueError(f"{value} is not a uint256")
        return str(value) if dialect.name == "sqlite" else Decimal(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, Decimal):
            # NUMERIC(78, 0) comes back as an integral Decimal; refuse anything else.
            if value != value.to_integral_value():
                raise ValueError(f"non-integral amount {value} in the database")
        return int(value)


metadata = MetaData()

blocks = Table(
    "blocks",
    metadata,
    Column("number", BigInteger, primary_key=True, autoincrement=False),
    Column("hash", String(66), nullable=False, unique=True),
    Column("parent_hash", String(66), nullable=False),
)

events = Table(
    "events",
    metadata,
    Column(
        "block_number",
        BigInteger,
        ForeignKey("blocks.number", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    ),
    Column("log_index", Integer, primary_key=True, autoincrement=False),
    Column("block_hash", String(66), nullable=False),
    Column("tx_hash", String(66), nullable=False),
    Column("address", String(42), nullable=False),  # emitting contract
    Column("name", String(16), nullable=False),  # Transfer | Opened | Released | Refunded
    # Transfer(from, to, value)
    Column("from_addr", String(42)),
    Column("to_addr", String(42)),
    Column("value", Uint256),
    # Opened(id, payer, payee, amount, deadline) / Released(id, payee, amount) /
    # Refunded(id, payer, amount)
    Column("deal_id", Uint256),
    Column("payer", String(42)),
    Column("payee", String(42)),
    Column("amount", Uint256),
    Column("deadline", Uint256),
)


def make_engine(url: str) -> Engine:
    """Create an engine; on SQLite, turn on foreign keys (off by default there)."""
    engine = create_engine(url, future=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys = ON")

    return engine


def create_schema(engine: Engine) -> None:
    metadata.create_all(engine)


def drop_schema(engine: Engine) -> None:
    metadata.drop_all(engine)
