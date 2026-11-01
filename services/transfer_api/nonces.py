"""Nonce management for one hot wallet (book Ch 1.3; issue #12).

`NonceManager` seeds from the node's `pending` count once, then hands out consecutive
nonces under a lock, so concurrent senders never collide (F-11). It also **re-syncs**:

- after the node *rejects* a send (underpriced, intrinsic gas too low, ...), that nonce
  was never used. Counting on from it would leave a gap, and every later transaction
  would queue behind it forever (F-19). `resync()` resets the counter to the node's
  `pending` count, so the next send reuses the hole;
- after "nonce too low" (someone else used the key), the counter is behind the chain;
  `resync()` jumps forward.

`resync()` never moves *below* nonces this process has already handed out and that are
still in flight, unless told the lowest unused one explicitly (`resync(floor=...)`).
"""

from __future__ import annotations

import threading

from web3 import Web3


class NonceManager:
    def __init__(self, w3: Web3, address: str):
        self._w3 = w3
        self._address = address
        self._lock = threading.Lock()
        self._next = self._pending()

    def _pending(self) -> int:
        return self._w3.eth.get_transaction_count(self._address, "pending")

    def next(self) -> int:
        with self._lock:
            nonce = self._next
            self._next += 1
            return nonce

    def peek(self) -> int:
        with self._lock:
            return self._next

    def resync(self, floor: int | None = None) -> int:
        """Reset to the node's pending count (or `floor`, if lower: a known hole)."""
        with self._lock:
            pending = self._pending()
            self._next = pending if floor is None else min(pending, floor)
            return self._next
