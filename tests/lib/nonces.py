"""Local nonce management for signing raw transactions (book Ch 1.3).

Two strategies, so a test can show the difference:

- `NaiveNonceManager` asks the node for `getTransactionCount(addr, "pending")` before
  every send. That's correct only if sends never overlap: two workers that ask
  before either one's transaction reaches the node get the *same* nonce, and only
  one of their transactions can ever be mined.
- `LocalNonceManager` asks the node once, then hands out consecutive nonces under a
  lock. Concurrent workers get distinct nonces, whatever order the sends arrive in.
"""

from __future__ import annotations

import threading

from web3 import Web3


class NaiveNonceManager:
    def __init__(self, w3: Web3, address: str):
        self._w3 = w3
        self._address = address

    def next(self) -> int:
        return self._w3.eth.get_transaction_count(self._address, "pending")


class LocalNonceManager:
    def __init__(self, w3: Web3, address: str):
        self._next = w3.eth.get_transaction_count(address, "pending")
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            nonce = self._next
            self._next += 1
            return nonce
