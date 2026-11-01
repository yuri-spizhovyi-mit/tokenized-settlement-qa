"""EIP-712 signed release, built in Python independently of the contract.

The domain and struct are defined here from the spec (name "Escrow", version "1",
chain id, verifying contract; Release(uint256 id,uint256 nonce,uint256 deadline)),
not read back from the contract. The tests then check that Python and Solidity
agree on the typehash and domain separator (INT-07): if either side drifts, signed
releases from real clients break, and that test is what says so.
"""

from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_account.signers.local import LocalAccount
from eth_utils import keccak

RELEASE_TYPE = "Release(uint256 id,uint256 nonce,uint256 deadline)"
RELEASE_TYPEHASH = keccak(text=RELEASE_TYPE)

_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Release": [
        {"name": "id", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
    ],
}


def typed_release(chain_id: int, escrow: str, deal_id: int, nonce: int, deadline: int) -> dict:
    return {
        "types": _TYPES,
        "primaryType": "Release",
        "domain": {
            "name": "Escrow",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": escrow,
        },
        "message": {"id": deal_id, "nonce": nonce, "deadline": deadline},
    }


def domain_separator(chain_id: int, escrow: str) -> bytes:
    """The EIP-712 domain separator as eth_account computes it (the `\\x19\\x01` header's
    first 32 bytes)."""
    signable = encode_typed_data(full_message=typed_release(chain_id, escrow, 0, 0, 0))
    return bytes(signable.header)


def sign_release(
    signer: LocalAccount, chain_id: int, escrow: str, deal_id: int, nonce: int, deadline: int
) -> bytes:
    """r || s || v, the 65-byte form `Escrow.releaseWithSig` expects."""
    message = typed_release(chain_id, escrow, deal_id, nonce, deadline)
    signed = Account.sign_message(encode_typed_data(full_message=message), signer.key)
    return bytes(signed.signature)
