"""DELIBERATELY BROKEN transfer-API variants, test fixtures only (like NaiveIndexer).

- `CountingNonceManager`: seeds once and counts up, and never re-syncs. A rejected
  send leaves a hole, and every later transaction queues behind it (F-19, issue #12).
- `NaiveRestartService`: after a crash between signing and recording the broadcast,
  it signs the transfer *again* with a fresh nonce instead of rebroadcasting the stored
  bytes. If the first one did reach the node, the recipient is paid twice (F-21).
"""

from transfer_api import NonceManager, TransferService


class CountingNonceManager(NonceManager):
    def resync(self, floor=None):  # never re-syncs: the bug under test
        return self.peek()


class NaiveRestartService(TransferService):
    resume_mode = "resign"
