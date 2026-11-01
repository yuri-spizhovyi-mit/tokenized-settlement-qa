"""Decode Solidity custom errors from revert data, by name and arguments.

The registry is built from the ABIs of every contract deployed in the session, so an
error raised in the token and bubbled up through the escrow (SafeERC20 forwards the
token's revert data) still decodes to `AccountNotAllowlisted(account)`.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from eth_abi import decode
from eth_utils import keccak, to_checksum_address
from hexbytes import HexBytes

ERROR_STRING = bytes.fromhex("08c379a0")  # Error(string)
PANIC = bytes.fromhex("4e487b71")  # Panic(uint256)


def _canonical_type(item: dict) -> str:
    """ABI input -> canonical type string, expanding tuples."""
    if item["type"].startswith("tuple"):
        inner = ",".join(_canonical_type(c) for c in item["components"])
        return f"({inner}){item['type'][len('tuple'):]}"
    return item["type"]


@dataclass(frozen=True)
class ErrorDef:
    name: str
    types: tuple[str, ...]
    arg_names: tuple[str, ...]

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(self.types)})"

    @property
    def selector(self) -> bytes:
        return keccak(text=self.signature)[:4]


class Revert(Exception):
    """A decoded revert: `name` and positional `args` (addresses checksummed)."""

    def __init__(self, name: str, args: tuple, raw: bytes):
        self.name = name
        self.args_ = args
        self.raw = raw
        super().__init__(f"{name}{args}")

    def __eq__(self, other: object) -> bool:  # convenience for assertions
        return isinstance(other, Revert) and (self.name, self.args_) == (other.name, other.args_)

    __hash__ = None  # type: ignore[assignment]


class ErrorRegistry:
    def __init__(self) -> None:
        self._by_selector: dict[bytes, ErrorDef] = {}

    def register(self, abi: list) -> None:
        for item in abi:
            if item.get("type") != "error":
                continue
            definition = ErrorDef(
                name=item["name"],
                types=tuple(_canonical_type(i) for i in item["inputs"]),
                arg_names=tuple(i["name"] for i in item["inputs"]),
            )
            self._by_selector[definition.selector] = definition

    def decode(self, data: bytes | str) -> Revert:
        raw = bytes(HexBytes(data))
        selector, payload = raw[:4], raw[4:]
        if selector == ERROR_STRING:
            return Revert("Error", decode(["string"], payload), raw)
        if selector == PANIC:
            return Revert("Panic", decode(["uint256"], payload), raw)
        definition = self._by_selector.get(selector)
        if definition is None:
            return Revert(f"Unknown(0x{selector.hex()})", (payload,), raw)
        values = decode(list(definition.types), payload)
        normalised = tuple(
            to_checksum_address(v) if t == "address" else v
            for t, v in zip(definition.types, values)
        )
        return Revert(definition.name, normalised, raw)

    def decode_exception(self, exc: Exception) -> Revert:
        """web3 raises ContractCustomError / ContractLogicError with `.data`."""
        data = getattr(exc, "data", None)
        if isinstance(data, dict):  # some providers nest it
            data = data.get("data")
        if not data:
            return Revert("NoData", (str(exc),), b"")
        return self.decode(data)


@contextmanager
def expect_revert(name: str, *args: Any) -> Iterator[None]:
    """Assert the block raises exactly `name(*args)`. Addresses compare checksummed."""
    expected = tuple(to_checksum_address(a) if _looks_like_address(a) else a for a in args)
    try:
        yield
    except Revert as got:
        assert got.name == name, f"expected {name}{expected}, got {got.name}{got.args_}"
        assert got.args_ == expected, f"expected {name}{expected}, got {got.name}{got.args_}"
    else:
        raise AssertionError(f"expected revert {name}{expected}, but the call succeeded")


def _looks_like_address(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("0x") and len(value) == 42
