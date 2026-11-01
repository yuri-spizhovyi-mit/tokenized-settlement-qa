"""Named, funded accounts derived from Anvil's default mnemonic.

Each test role gets its own key, matching the Foundry fixtures: governance, minting,
pausing, compliance, arbitration and escrow administration are separate duties.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_account import Account
from eth_account.signers.local import LocalAccount

from .anvil import DEFAULT_MNEMONIC

Account.enable_unaudited_hdwallet_features()

ROLES = (
    "admin",        # token DEFAULT_ADMIN_ROLE (governance)
    "minter",       # MINTER_ROLE
    "pauser",       # PAUSER_ROLE
    "compliance",   # ALLOWLIST_ROLE
    "arbiter",      # Escrow arbiter, signs EIP-712 releases
    "escrow_admin", # Escrow DEFAULT_ADMIN_ROLE, rotates the arbiter
    "payer",
    "payee",
    "relayer",      # submits signed releases; holds no role
    "stranger",     # never allowlisted, holds no role
)


@dataclass(frozen=True)
class Accounts:
    admin: LocalAccount
    minter: LocalAccount
    pauser: LocalAccount
    compliance: LocalAccount
    arbiter: LocalAccount
    escrow_admin: LocalAccount
    payer: LocalAccount
    payee: LocalAccount
    relayer: LocalAccount
    stranger: LocalAccount

    @classmethod
    def from_mnemonic(cls, mnemonic: str = DEFAULT_MNEMONIC) -> Accounts:
        keys = {
            role: Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{i}")
            for i, role in enumerate(ROLES)
        }
        return cls(**keys)

    def name_of(self, address: str) -> str:
        for role in ROLES:
            if getattr(self, role).address.lower() == address.lower():
                return role
        return address
