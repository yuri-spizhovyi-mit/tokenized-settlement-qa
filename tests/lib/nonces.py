"""Local nonce management for signing raw transactions (book Ch 1.3).

Two strategies, so a test can show the difference:

- `NaiveNonceManager` asks the node for `getTransactionCount(addr, "pending")` before
  every send. That's correct only if sends never overlap: two workers that ask
  before either one's transaction reaches the node get the *same* nonce, and only
  one of their transactions can ever be mined.
- `LocalNonceManager` asks the node once, then hands out consecutive nonces under a
  lock. Concurrent workers get distinct nonces, whatever order the sends arrive in.
  Since PR 8 it is the transfer API's `NonceManager`, which also re-syncs after a
  rejected send (issue #12, F-19).
"""

from __future__ import annotations

from web3 import Web3

from transfer_api.nonces import NonceManager


class NaiveNonceManager:
    def __init__(self, w3: Web3, address: str):
        self._w3 = w3
        self._address = address

    def next(self) -> int:
        return self._w3.eth.get_transaction_count(self._address, "pending")


# The production manager: seed once, lock, and re-sync after a rejected send.
LocalNonceManager = NonceManager
