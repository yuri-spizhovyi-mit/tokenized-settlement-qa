"""Service objects for SettlementToken and Escrow.

Tests talk to these, not to raw web3 contract objects. Every state-changing method
sends through `Chain.send`, which waits for the receipt and asserts status == 1, and
returns the *decoded event* the call is meant to emit, not the raw receipt. A call
that should emit an event but doesn't is a failure here, not in every test.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from eth_account.signers.local import LocalAccount
from web3.contract import Contract
from web3.logs import DISCARD
from web3.types import TxReceipt

from .chain import Chain


class DealState(IntEnum):
    FUNDED = 0
    RELEASED = 1
    REFUNDED = 2


@dataclass(frozen=True)
class Deal:
    payer: str
    payee: str
    amount: int
    deadline: int
    state: DealState


@dataclass(frozen=True)
class Event:
    """One decoded log: the event name, its arguments, and where it was mined."""

    name: str
    args: dict[str, Any]
    emitter: str
    tx_hash: str
    block_number: int

    def __getitem__(self, key: str) -> Any:
        return self.args[key]


def _events(contract: Contract, receipt: TxReceipt, name: str) -> list[Event]:
    logs = getattr(contract.events, name)().process_receipt(receipt, errors=DISCARD)
    return [
        Event(
            name=name,
            args=dict(log["args"]),
            emitter=log["address"],
            tx_hash=receipt["transactionHash"].to_0x_hex(),
            block_number=receipt["blockNumber"],
        )
        for log in logs
    ]


def _one(contract: Contract, receipt: TxReceipt, name: str) -> Event:
    found = _events(contract, receipt, name)
    assert len(found) == 1, f"expected exactly one {name} from {contract.address}, got {len(found)}"
    return found[0]


class TokenClient:
    def __init__(self, chain: Chain, contract: Contract):
        self.chain = chain
        self.contract = contract
        self.address = contract.address
        self.MINTER_ROLE = contract.functions.MINTER_ROLE().call()
        self.PAUSER_ROLE = contract.functions.PAUSER_ROLE().call()
        self.ALLOWLIST_ROLE = contract.functions.ALLOWLIST_ROLE().call()

    # ---- reads ---------------------------------------------------------------------
    def balance_of(self, account: str) -> int:
        return self.contract.functions.balanceOf(account).call()

    def total_supply(self) -> int:
        return self.contract.functions.totalSupply().call()

    def allowance(self, owner: str, spender: str) -> int:
        return self.contract.functions.allowance(owner, spender).call()

    def is_allowlisted(self, account: str) -> bool:
        return self.contract.functions.isAllowlisted(account).call()

    def paused(self) -> bool:
        return self.contract.functions.paused().call()

    # ---- writes (return decoded events) --------------------------------------------
    def grant_role(self, admin: LocalAccount, role: bytes, account: str) -> Event:
        receipt = self.chain.send(self.contract.functions.grantRole(role, account), admin)
        return _one(self.contract, receipt, "RoleGranted")

    def set_allowlisted(self, compliance: LocalAccount, account: str, allowed: bool) -> Event:
        fn = self.contract.functions.setAllowlisted(account, allowed)
        return _one(self.contract, self.chain.send(fn, compliance), "AllowlistUpdated")

    def mint(self, minter: LocalAccount, to: str, amount: int) -> Event:
        receipt = self.chain.send(self.contract.functions.mint(to, amount), minter)
        return _one(self.contract, receipt, "Transfer")

    def burn(self, minter: LocalAccount, amount: int) -> Event:
        receipt = self.chain.send(self.contract.functions.burn(amount), minter)
        return _one(self.contract, receipt, "Transfer")

    def approve(self, owner: LocalAccount, spender: str, amount: int) -> Event:
        receipt = self.chain.send(self.contract.functions.approve(spender, amount), owner)
        return _one(self.contract, receipt, "Approval")

    def transfer(self, sender: LocalAccount, to: str, amount: int) -> Event:
        receipt = self.chain.send(self.contract.functions.transfer(to, amount), sender)
        return _one(self.contract, receipt, "Transfer")

    def pause(self, pauser: LocalAccount) -> Event:
        return _one(self.contract, self.chain.send(self.contract.functions.pause(), pauser), "Paused")

    def unpause(self, pauser: LocalAccount) -> Event:
        receipt = self.chain.send(self.contract.functions.unpause(), pauser)
        return _one(self.contract, receipt, "Unpaused")


@dataclass(frozen=True)
class Opened:
    deal_id: int
    event: Event
    transfer: Event  # the token's Transfer(payer -> escrow), decoded from the same receipt


class EscrowClient:
    def __init__(self, chain: Chain, contract: Contract, token: TokenClient):
        self.chain = chain
        self.contract = contract
        self.address = contract.address
        self.token = token

    # ---- reads ---------------------------------------------------------------------
    def deal(self, deal_id: int) -> Deal:
        payer, payee, amount, deadline, state = self.contract.functions.deals(deal_id).call()
        return Deal(payer, payee, amount, deadline, DealState(state))

    def next_id(self) -> int:
        return self.contract.functions.nextId().call()

    def arbiter(self) -> str:
        return self.contract.functions.arbiter().call()

    def nonces(self, owner: str) -> int:
        return self.contract.functions.nonces(owner).call()

    def release_typehash(self) -> bytes:
        return self.contract.functions.RELEASE_TYPEHASH().call()

    def domain_separator(self) -> bytes:
        return self.contract.functions.DOMAIN_SEPARATOR().call()

    # ---- writes (return decoded events) --------------------------------------------
    def open(self, payer: LocalAccount, payee: str, amount: int, deadline: int) -> Opened:
        receipt = self.chain.send(self.contract.functions.open(payee, amount, deadline), payer)
        event = _one(self.contract, receipt, "Opened")
        transfer = _one(self.token.contract, receipt, "Transfer")
        return Opened(deal_id=event["id"], event=event, transfer=transfer)

    def release(self, caller: LocalAccount, deal_id: int) -> Event:
        receipt = self.chain.send(self.contract.functions.release(deal_id), caller)
        return _one(self.contract, receipt, "Released")

    def refund(self, caller: LocalAccount, deal_id: int, *, at: int | None = None) -> Event:
        """Refund; with `at=`, mined in a block with exactly that timestamp."""
        fn = self.contract.functions.refund(deal_id)
        receipt = self.chain.send_at(at, fn, caller) if at is not None else self.chain.send(fn, caller)
        return _one(self.contract, receipt, "Refunded")

    def release_with_sig(
        self, submitter: LocalAccount, deal_id: int, deadline: int, signature: bytes,
        *, at: int | None = None,
    ) -> Event:
        fn = self.contract.functions.releaseWithSig(deal_id, deadline, signature)
        receipt = self.chain.send_at(at, fn, submitter) if at is not None else self.chain.send(fn, submitter)
        return _one(self.contract, receipt, "Released")

    def set_arbiter(self, escrow_admin: LocalAccount, new_arbiter: str) -> Event:
        fn = self.contract.functions.setArbiter(new_arbiter)
        return _one(self.contract, self.chain.send(fn, escrow_admin), "ArbiterChanged")
