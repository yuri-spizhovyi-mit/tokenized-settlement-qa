"""TransferService: accept transfer requests, then sign, send and track them.

Status progression of a transfer (every change is a row in `transitions`):

    accepted -> signed -> submitted -> mined -> confirmed
                                    \\-> failed   (mined with status 0: revert after accept)
    submitted -(no receipt for `stuck_after_blocks`)-> stuck -> submitted (replacement)

- **accepted**: validated (address, amount, an `eth_call` simulation from the hot wallet)
  and stored under its idempotency key. Nothing is on chain yet.
- **signed**: a raw transaction is stored in `attempts` *before* it is broadcast
  (write-ahead). A crash here is recovered by rebroadcasting those exact bytes.
- **submitted**: the node accepted the transaction.
- **mined**: a receipt with status 1. **confirmed**: `confirmations` blocks on top.
- **failed**: a receipt with status 0. The revert is decoded (e.g. the recipient was
  removed from the allowlist after the request was accepted).
- **stuck**: no receipt after `stuck_after_blocks`. The worker re-signs the *same nonce*
  with a higher fee. This covers a transaction still pending at too low a fee and one
  the node silently dropped (Anvil evicts it, F-20).

The worker is a single `tick()` so tests drive it step by step; `app.py` can also run
it on a background thread.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from eth_abi import decode as abi_decode
from eth_account.signers.local import LocalAccount
from eth_utils import keccak, to_checksum_address
from hexbytes import HexBytes
from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractCustomError, ContractLogicError, TransactionNotFound

from .db import attempts, transfers, transitions
from .nonces import NonceManager

GWEI = 10**9
MAX_SEND_TRIES = 5  # rejected broadcasts before a transfer is failed for good


class Status(str, Enum):
    ACCEPTED = "accepted"
    SIGNED = "signed"
    SUBMITTED = "submitted"
    STUCK = "stuck"
    MINED = "mined"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class InvalidRequest(ValueError):
    """The request itself is malformed (address, amount, key)."""


class Busy(Exception):
    """Backpressure: the backlog of unsettled transfers is at its limit (TAPI-D8)."""


class Rejected(Exception):
    """The transfer would revert right now; nothing was stored."""

    def __init__(self, name: str, args: tuple):
        super().__init__(f"{name}{args}")
        self.name, self.args_ = name, args


class IdempotencyConflict(Exception):
    """The key was already used for a *different* request."""


class Broadcast(Enum):
    SENT = "sent"
    KNOWN = "already known"
    NONCE_TOO_LOW = "nonce too low"
    REJECTED = "rejected"


@dataclass
class SendResult:
    outcome: Broadcast
    message: str = ""


class ErrorDecoder:
    """Custom-error selector -> name and argument types, from the token's ABI."""

    def __init__(self, abi: list):
        self._defs: dict[bytes, tuple[str, list[str]]] = {}
        for item in abi:
            if item.get("type") == "error":
                types = [i["type"] for i in item["inputs"]]
                selector = keccak(text=f"{item['name']}({','.join(types)})")[:4]
                self._defs[selector] = (item["name"], types)

    def decode(self, data: Any) -> tuple[str, tuple]:
        raw = bytes(HexBytes(data)) if data else b""
        if raw[:4] in self._defs:
            name, types = self._defs[raw[:4]]
            values = abi_decode(types, raw[4:])
            return name, tuple(to_checksum_address(v) if t == "address" else v for t, v in zip(types, values))
        return ("Unknown", (raw.hex(),))

    def from_exception(self, exc: Exception) -> tuple[str, tuple]:
        data = getattr(exc, "data", None)
        if isinstance(data, dict):
            data = data.get("data")
        return self.decode(data) if data else ("NoData", (str(exc),))


def request_hash(recipient: str, amount: int) -> str:
    return "0x" + keccak(text=f"{recipient.lower()}:{amount}").hex()


class TransferService:
    #: How a transfer left in `signed` is resumed after a restart. "rebroadcast" sends
    #: the stored bytes again (exactly once). NaiveRestartService (tests/lib) re-signs
    #: with a fresh nonce instead, and pays twice (F-21).
    resume_mode = "rebroadcast"

    def __init__(
        self,
        w3: Web3,
        engine: Engine,
        token: Contract,
        wallet: LocalAccount,
        *,
        confirmations: int = 1,
        stuck_after_blocks: int = 3,
        gas_limit: int = 100_000,
        tip: int = 1 * GWEI,
        nonces: NonceManager | None = None,
        max_submit_per_tick: int = 25,
        max_backlog: int | None = 500,
    ):
        self.w3 = w3
        self.engine = engine
        self.token = token
        self.wallet = wallet
        self.confirmations = confirmations
        self.stuck_after_blocks = stuck_after_blocks
        self.gas_limit = gas_limit
        self.tip = tip
        self.chain_id = w3.eth.chain_id
        self.nonces = nonces or NonceManager(w3, wallet.address)
        self.errors = ErrorDecoder(token.abi)
        self.gap_fills: list[int] = []  # nonces filled with a 0-value self-transfer
        self.max_submit_per_tick = max_submit_per_tick  # F-23: bounded, so tracking interleaves
        self.max_backlog = max_backlog  # TAPI-D8: None = unbounded
        self._tick_lock = threading.Lock()
        self._view = threading.local()  # the worker's per-tick snapshot of the chain (F-24)

    # ==== API side ===================================================================

    def accept(self, idempotency_key: str, to: str, amount: str) -> tuple[dict, bool]:
        """Validate and store a transfer. Returns (view, created). A replay of the same
        request under the same key returns the original transfer with created=False."""
        if not 1 <= len(idempotency_key) <= 128:
            raise InvalidRequest("Idempotency-Key must be 1 to 128 characters")
        if not Web3.is_address(to):
            raise InvalidRequest(f"not an address: {to!r}")
        if not (isinstance(amount, str) and amount.isdigit()):
            raise InvalidRequest("amount must be a decimal string of base units")
        value = int(amount)
        if not 0 < value < 2**256:
            raise InvalidRequest("amount must be between 1 and 2^256 - 1")
        recipient = to_checksum_address(to)
        digest = request_hash(recipient, value)

        existing = self._by_key(idempotency_key)
        if existing is not None:
            return self._replay(existing, digest), False  # replays are served even when busy

        if self.max_backlog is not None and self.backlog() >= self.max_backlog:
            raise Busy(f"{self.max_backlog} transfers are waiting to settle; retry later")

        try:  # would it succeed right now, from the hot wallet?
            self.token.functions.transfer(recipient, value).call({"from": self.wallet.address})
        except (ContractCustomError, ContractLogicError) as exc:
            raise Rejected(*self.errors.from_exception(exc)) from exc

        transfer_id = str(uuid.uuid4())
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(transfers).values(
                    id=transfer_id, idempotency_key=idempotency_key, request_hash=digest,
                    recipient=recipient, amount=value, status=Status.ACCEPTED.value,
                    created_seq=time.time_ns(),
                ))
                self._transition(conn, transfer_id, Status.ACCEPTED)
        except IntegrityError:
            # A concurrent request with the same key committed first: the UNIQUE
            # constraint decided. Answer as a replay of that one.
            return self._replay(self._by_key(idempotency_key), digest), False
        return self.get(transfer_id), True

    def backlog(self) -> int:
        """Transfers accepted but not yet final (confirmed or failed)."""
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(transfers).where(
                transfers.c.status.not_in([Status.CONFIRMED.value, Status.FAILED.value])
            )).scalar_one()

    def _replay(self, row: Any, digest: str) -> dict:
        if row.request_hash != digest:
            raise IdempotencyConflict("Idempotency-Key was already used for a different request")
        return self.get(row.id)

    def _by_key(self, key: str) -> Any:
        with self.engine.connect() as conn:
            return conn.execute(select(transfers).where(transfers.c.idempotency_key == key)).first()

    def get(self, transfer_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(transfers).where(transfers.c.id == transfer_id)).first()
            if row is None:
                return None
            history = conn.execute(
                select(transitions).where(transitions.c.transfer_id == transfer_id).order_by(transitions.c.seq)
            ).all()
            tries = conn.execute(
                select(attempts.c.tx_hash).where(attempts.c.transfer_id == transfer_id).order_by(attempts.c.attempt)
            ).scalars().all()
        return {
            "id": row.id,
            "status": row.status,
            "to": row.recipient,
            "amount": str(row.amount),  # a string: JSON numbers lose precision above 2^53
            "nonce": row.nonce,
            "tx_hash": row.tx_hash,
            "block_number": row.block_number,
            "error": row.error,
            "attempts": list(tries),
            "history": [{"status": h.status, "block": h.block_number, "detail": h.detail} for h in history],
        }

    # ==== worker side ================================================================

    def tick(self) -> None:
        """One pass of the worker: resume, fill nonce gaps, submit, track, confirm."""
        with self._tick_lock:
            try:
                self._refresh_view()
                resumed = [self._resume_signed(t) for t in self._rows(Status.SIGNED)]
                if resumed:
                    self.nonces.resync()
                self._fill_gaps()
                # A bounded batch: a tick that submitted the whole backlog first would track
                # no receipts until it finished, and confirmations would stall (F-23).
                for t in self._rows(Status.ACCEPTED, limit=self.max_submit_per_tick):
                    self._submit(t)
                self._refresh_view()
                latest = self._view.block
                for t in self._rows(Status.SUBMITTED):
                    self._track(t, latest)
                for t in self._rows(Status.MINED):
                    if latest - t.block_number >= self.confirmations:
                        self._set(t.id, Status.CONFIRMED)
            finally:
                self._view.block = None  # outside a tick, read the chain live

    def _refresh_view(self) -> None:
        """One read of the head per tick phase instead of one per bookkeeping row: block
        number and base fee were 6 of the 8 JSON-RPC calls per transfer (F-24)."""
        latest = self.w3.eth.get_block("latest")
        self._view.block = latest["number"]
        self._view.base_fee = latest["baseFeePerGas"]

    def _block_number(self) -> int:
        cached = getattr(self._view, "block", None)
        return cached if cached is not None else self.w3.eth.block_number

    def _rows(self, status: Status, limit: int | None = None) -> list[Any]:
        query = select(transfers).where(transfers.c.status == status.value).order_by(transfers.c.created_seq)
        if limit is not None:
            query = query.limit(limit)
        with self.engine.connect() as conn:
            return conn.execute(query).all()

    # ---- submit ----------------------------------------------------------------------

    def _submit(self, t: Any, retry_on_nonce_too_low: bool = True) -> None:
        tries = self._attempt_count(t.id)
        if tries >= MAX_SEND_TRIES:
            self._set(t.id, Status.FAILED, error=f"gave up after {tries} rejected sends")
            return
        nonce = self.nonces.next()
        attempt = self._sign(t, nonce, self._fee())
        with self.engine.begin() as conn:  # write-ahead: stored before it is sent
            conn.execute(insert(attempts).values(transfer_id=t.id, **attempt))
            conn.execute(update(transfers).where(transfers.c.id == t.id).values(nonce=nonce))
            self._set(t.id, Status.SIGNED, conn=conn, detail=f"nonce {nonce}")
        self._after_write_ahead(t.id)
        result = self._broadcast(attempt["raw_tx"])
        self._after_broadcast(t.id)
        self._handle_send(t, attempt, result, retry_on_nonce_too_low)

    def _handle_send(self, t: Any, attempt: dict, result: SendResult, retry: bool) -> None:
        if result.outcome in (Broadcast.SENT, Broadcast.KNOWN):
            self._set(t.id, Status.SUBMITTED, detail=attempt["tx_hash"])
            return
        if result.outcome is Broadcast.NONCE_TOO_LOW and self._receipt(attempt["tx_hash"]) is not None:
            self._set(t.id, Status.SUBMITTED, detail="already mined")  # our own tx, sent before a crash
            return
        # Never used: the node refused it. Re-sync, so the next send reuses this nonce
        # (or jumps past one someone else used) instead of leaving a gap (issue #12).
        self.nonces.resync()
        if getattr(self._view, "block", None) is not None:
            self._refresh_view()  # a refusal may mean the base fee moved
        self._set(t.id, Status.ACCEPTED, detail=f"{result.outcome.value}: {result.message}; nonce re-synced")
        if result.outcome is Broadcast.NONCE_TOO_LOW and retry:
            self._submit(self._row(t.id), retry_on_nonce_too_low=False)

    def _resume_signed(self, t: Any) -> None:
        """A transfer left in `signed`: we crashed after writing the attempt, before or
        after broadcasting it. Rebroadcast the stored bytes; the node tells us which."""
        attempt = self._last_attempt(t.id)._asdict()
        if self.resume_mode != "rebroadcast":
            self._set(t.id, Status.ACCEPTED, detail="re-signing after restart")
            return
        self._handle_send(t, attempt, self._broadcast(attempt["raw_tx"]), retry=False)

    def _sign(self, t: Any, nonce: int, max_fee: int) -> dict:
        tx = self.token.functions.transfer(t.recipient, t.amount).build_transaction({
            "from": self.wallet.address,
            "nonce": nonce,
            "gas": self.gas_limit,  # explicit: no estimation at send time (TAPI-D4)
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": min(self.tip, max_fee),
            "chainId": self.chain_id,
        })
        signed = self.wallet.sign_transaction(tx)
        return {
            "attempt": self._attempt_count(t.id) + 1,
            "nonce": nonce,
            "tx_hash": signed.hash.to_0x_hex(),
            "raw_tx": signed.raw_transaction.to_0x_hex(),
            "max_fee_per_gas": max_fee,
            "sent_at_block": self._block_number(),
        }

    def _fee(self) -> int:
        base = getattr(self._view, "base_fee", None) if getattr(self._view, "block", None) is not None else None
        if base is None:
            base = self.w3.eth.get_block("latest")["baseFeePerGas"]
        return 2 * base + self.tip

    def _broadcast(self, raw_tx: str) -> SendResult:
        try:
            self.w3.eth.send_raw_transaction(raw_tx)
            return SendResult(Broadcast.SENT)
        except Exception as exc:  # the node's refusal; classify by message
            message = str(exc)
            lowered = message.lower()
            if "already known" in lowered or "known transaction" in lowered:
                return SendResult(Broadcast.KNOWN, message)
            if "nonce too low" in lowered:
                return SendResult(Broadcast.NONCE_TOO_LOW, message)
            return SendResult(Broadcast.REJECTED, message)

    # ---- track -----------------------------------------------------------------------

    def _track(self, t: Any, latest: int) -> None:
        for a in self._attempts(t.id):
            receipt = self._receipt(a.tx_hash)
            if receipt is None:
                continue
            with self.engine.begin() as conn:
                conn.execute(update(transfers).where(transfers.c.id == t.id).values(
                    tx_hash=a.tx_hash, block_number=receipt["blockNumber"], nonce=a.nonce,
                ))
            if receipt["status"] == 1:
                self._set(t.id, Status.MINED, detail=a.tx_hash)
                if latest - receipt["blockNumber"] >= self.confirmations:
                    self._set(t.id, Status.CONFIRMED)
            else:
                name, args = self._revert_reason(t, receipt["blockNumber"])
                self._set(t.id, Status.FAILED, error=f"{name}{args}", detail=a.tx_hash)
            return

        last = self._last_attempt(t.id)
        if latest - last.sent_at_block < self.stuck_after_blocks:
            return
        # Stuck: same nonce, higher fee. Replaces a pending one; re-sends a dropped one.
        self._set(t.id, Status.STUCK, detail=f"no receipt for {latest - last.sent_at_block} blocks")
        fee = max(last.max_fee_per_gas * 5 // 4 + 1, self._fee())
        attempt = self._sign(t, last.nonce, fee)
        with self.engine.begin() as conn:
            conn.execute(insert(attempts).values(transfer_id=t.id, **attempt))
        result = self._broadcast(attempt["raw_tx"])
        detail = f"replaced: attempt {attempt['attempt']}, max fee {fee}"
        if result.outcome is Broadcast.REJECTED:
            detail += f" (node: {result.message})"
        self._set(t.id, Status.SUBMITTED, detail=detail)

    def _revert_reason(self, t: Any, block: int) -> tuple[str, tuple]:
        """Replay the transfer against the block it failed in to decode why."""
        try:
            self.token.functions.transfer(t.recipient, t.amount).call(
                {"from": self.wallet.address}, block_identifier=block
            )
        except (ContractCustomError, ContractLogicError) as exc:
            return self.errors.from_exception(exc)
        return ("Unknown", ("replay succeeded",))

    def _fill_gaps(self) -> None:
        """A transaction waiting behind a nonce nobody will ever send is stuck forever:
        replacing it doesn't help. Fill each missing nonce with a 0-value self-transfer."""
        outstanding = [
            self._last_attempt(t.id).nonce for t in self._rows(Status.SUBMITTED)
        ]
        if not outstanding:
            return
        mined = self.w3.eth.get_transaction_count(self.wallet.address, "latest")
        executable = self.w3.eth.get_transaction_count(self.wallet.address, "pending")
        lowest = min(outstanding)
        if executable != mined or lowest <= mined:
            return  # something is pending at the next nonce, or there is no gap
        for nonce in range(mined, lowest):
            fee = self._fee()
            signed = self.wallet.sign_transaction({
                "to": self.wallet.address, "value": 0, "gas": 21_000, "nonce": nonce,
                "maxFeePerGas": fee, "maxPriorityFeePerGas": min(self.tip, fee), "chainId": self.chain_id,
            })
            self._broadcast(signed.raw_transaction.to_0x_hex())
            self.gap_fills.append(nonce)
        self.nonces.resync()

    # ---- storage helpers -------------------------------------------------------------

    def _receipt(self, tx_hash: str) -> Any:
        try:
            return self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None

    def _row(self, transfer_id: str) -> Any:
        with self.engine.connect() as conn:
            return conn.execute(select(transfers).where(transfers.c.id == transfer_id)).first()

    def _attempts(self, transfer_id: str) -> list[Any]:
        with self.engine.connect() as conn:
            return conn.execute(
                select(attempts).where(attempts.c.transfer_id == transfer_id).order_by(attempts.c.attempt)
            ).all()

    def _last_attempt(self, transfer_id: str) -> Any:
        return self._attempts(transfer_id)[-1]

    def _attempt_count(self, transfer_id: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(attempts).where(attempts.c.transfer_id == transfer_id)
            ).scalar_one()

    def _set(self, transfer_id: str, status: Status, *, conn=None, detail: str | None = None,
             error: str | None = None) -> None:
        if conn is None:
            with self.engine.begin() as c:
                return self._set(transfer_id, status, conn=c, detail=detail, error=error)
        values: dict[str, Any] = {"status": status.value}
        if error is not None:
            values["error"] = error
        conn.execute(update(transfers).where(transfers.c.id == transfer_id).values(**values))
        self._transition(conn, transfer_id, status, detail)

    def _transition(self, conn, transfer_id: str, status: Status, detail: str | None = None) -> None:
        seq = conn.execute(
            select(func.count()).select_from(transitions).where(transitions.c.transfer_id == transfer_id)
        ).scalar_one()
        conn.execute(insert(transitions).values(
            transfer_id=transfer_id, seq=seq + 1, status=status.value,
            block_number=self._block_number(), detail=detail,
        ))

    # ---- test hooks (crash simulation) -------------------------------------------------

    def _after_write_ahead(self, transfer_id: str) -> None:
        """Called after the signed attempt is stored, before it is broadcast."""

    def _after_broadcast(self, transfer_id: str) -> None:
        """Called after the broadcast, before its outcome is stored."""
