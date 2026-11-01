"""Thin wrapper around web3 for sending, deploying, snapshots and time travel.

Every send waits for the receipt and asserts `status == 1`. A transaction that
reverts during gas estimation is raised as a decoded `Revert` (see errors.py), so a
test asserts *which* custom error it got, never just "it reverted".
"""

from __future__ import annotations

from typing import Any

from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction
from web3.exceptions import ContractCustomError, ContractLogicError
from web3.types import TxReceipt

from .artifacts import Artifact
from .errors import ErrorRegistry


class ReceiptFailed(AssertionError):
    """A transaction was mined with status 0 when success was expected."""

    def __init__(self, receipt: TxReceipt):
        super().__init__(f"tx {receipt['transactionHash'].hex()} mined with status 0")
        self.receipt = receipt


class Chain:
    def __init__(self, w3: Web3):
        self.w3 = w3
        self.errors = ErrorRegistry()

    # ---- deploy and send ---------------------------------------------------------

    def deploy(self, artifact: Artifact, deployer: LocalAccount, *args: Any) -> Contract:
        self.errors.register(artifact.abi)
        factory = self.w3.eth.contract(abi=artifact.abi, bytecode=artifact.bytecode)
        receipt = self._wait(factory.constructor(*args).transact({"from": deployer.address}))
        if receipt["status"] != 1:
            raise ReceiptFailed(receipt)
        return self.w3.eth.contract(address=receipt["contractAddress"], abi=artifact.abi)

    def send(self, fn: ContractFunction, sender: LocalAccount, **tx: Any) -> TxReceipt:
        """Send `fn` from `sender` and require success.

        Raises `Revert` if the node rejects it at gas estimation (nothing is sent and
        no nonce is used), and `ReceiptFailed` if it is mined with status 0.
        """
        receipt = self.send_allow_failure(fn, sender, **tx)
        if receipt["status"] != 1:
            raise ReceiptFailed(receipt)
        return receipt

    def send_allow_failure(self, fn: ContractFunction, sender: LocalAccount, **tx: Any) -> TxReceipt:
        """Send and return the receipt whatever its status (pass `gas=` to force inclusion)."""
        try:
            tx_hash = fn.transact({"from": sender.address, **tx})
        except (ContractCustomError, ContractLogicError) as exc:
            raise self.errors.decode_exception(exc) from exc
        return self._wait(tx_hash)

    def call(self, fn: ContractFunction, sender: LocalAccount | None = None, block: Any = "latest") -> Any:
        """eth_call `fn`; a revert is raised as a decoded `Revert`."""
        try:
            return fn.call({"from": sender.address} if sender else {}, block_identifier=block)
        except (ContractCustomError, ContractLogicError) as exc:
            raise self.errors.decode_exception(exc) from exc

    def _wait(self, tx_hash: bytes) -> TxReceipt:
        # Never get_transaction_receipt right after sending: Anvil mines
        # asynchronously, so the receipt may not exist yet (docs/findings.md, F-13).
        return self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

    # ---- isolation -----------------------------------------------------------------

    def snapshot(self) -> str:
        return self._rpc("evm_snapshot")

    def revert(self, snapshot_id: str) -> None:
        # Anvil snapshots are single-use: reverting consumes the id.
        assert self._rpc("evm_revert", snapshot_id) is True, f"evm_revert({snapshot_id}) failed"

    # ---- time ----------------------------------------------------------------------

    def now(self) -> int:
        return self.w3.eth.get_block("latest")["timestamp"]

    def increase_time(self, seconds: int) -> int:
        """Coarse travel: move the clock forward and mine a block. Returns the new
        timestamp. Not exact: Anvil keeps adding wall-clock time (F-12)."""
        self._rpc("evm_increaseTime", seconds)
        self._rpc("evm_mine")
        return self.now()

    def set_next_block_timestamp(self, timestamp: int) -> None:
        """Exact travel: the *next* mined block gets exactly `timestamp`.

        Caveat (F-14): eth_estimateGas ignores this and simulates at the latest block's
        time, so a time-dependent call can fail estimation and never be sent. Use
        `send_at` / `call_at`, which handle it."""
        self._rpc("evm_setNextBlockTimestamp", timestamp)

    def mine_at(self, timestamp: int) -> None:
        """Mine an empty block with exactly `timestamp`; it becomes `latest`."""
        self._rpc("evm_mine", timestamp)

    def call_at(self, timestamp: int, fn: ContractFunction, sender: LocalAccount) -> Any:
        """Simulate `fn` in a block whose timestamp is exactly `timestamp`.

        Mines a block at that second, then eth_calls against it, so the EVM sees
        block.timestamp == timestamp. A revert is raised as a decoded `Revert`."""
        self.mine_at(timestamp)
        return self.call(fn, sender, block="latest")

    def send_at(
        self, timestamp: int, fn: ContractFunction, sender: LocalAccount, gas: int = 500_000
    ) -> TxReceipt:
        """Send `fn` so it is mined in a block with exactly `timestamp`, and require
        success. Uses an explicit gas limit: estimation would run at the wrong time (F-14)."""
        self.set_next_block_timestamp(timestamp)
        receipt = self.send_allow_failure(fn, sender, gas=gas)
        if receipt["status"] != 1:
            raise ReceiptFailed(receipt)
        mined_at = self.w3.eth.get_block(receipt["blockNumber"])["timestamp"]
        assert mined_at == timestamp, f"mined at {mined_at}, wanted {timestamp}"
        return receipt

    def _rpc(self, method: str, *params: Any) -> Any:
        response = self.w3.provider.make_request(method, list(params))
        if "error" in response:
            raise RuntimeError(f"{method} failed: {response['error']}")
        return response["result"]

    # ---- block control ---------------------------------------------------------------

    def send_in_one_block(
        self, calls: list[tuple[ContractFunction, LocalAccount]], gas: int = 500_000
    ) -> list[TxReceipt]:
        """Send several calls and mine them together in one block, in list order, and
        require every one to succeed.

        Automine is off while they queue, so each send uses an explicit gas limit:
        estimation would run against `latest`, without the earlier calls of the same
        block (an `open` would fail estimation before its `approve` is mined)."""
        self._rpc("evm_setAutomine", False)
        try:
            hashes = [fn.transact({"from": sender.address, "gas": gas}) for fn, sender in calls]
            self._rpc("evm_mine")
        finally:
            self._rpc("evm_setAutomine", True)
        receipts = [self._wait(h) for h in hashes]
        for receipt in receipts:
            if receipt["status"] != 1:
                raise ReceiptFailed(receipt)
        assert len({r["blockNumber"] for r in receipts}) == 1, "calls were split across blocks"
        return receipts

    def mine(self, blocks: int = 1) -> None:
        for _ in range(blocks):
            self._rpc("evm_mine")

    def anvil_reorg(self, depth: int, txs: list[tuple[dict[str, Any], int]] = ()) -> None:
        """Replace the last `depth` blocks with new ones at the same heights. `txs` are
        (transaction request, block offset within the new blocks) pairs. The chain's
        height does not change."""
        self._rpc("anvil_reorg", depth, [[tx, offset] for tx, offset in txs])

    def block_hash(self, number: int) -> str:
        return self.w3.eth.get_block(number)["hash"].to_0x_hex()

    # ---- helpers ---------------------------------------------------------------------

    def eth_balance(self, address: str) -> int:
        return self.w3.eth.get_balance(Web3.to_checksum_address(address))

    def nonce(self, address: str, block: str = "latest") -> int:
        return self.w3.eth.get_transaction_count(Web3.to_checksum_address(address), block)
