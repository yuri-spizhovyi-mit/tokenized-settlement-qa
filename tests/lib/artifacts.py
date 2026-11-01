"""Load contract ABIs and bytecode from Foundry's build output (contracts/out/)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "contracts"
OUT_DIR = CONTRACTS_DIR / "out"


@dataclass(frozen=True)
class Artifact:
    name: str
    abi: list
    bytecode: str


def ensure_built() -> None:
    """Run `forge build` if the artifacts are missing (CI builds explicitly first)."""
    if not (OUT_DIR / "Escrow.sol" / "Escrow.json").exists():
        subprocess.run(["forge", "build"], cwd=CONTRACTS_DIR, check=True)


@cache
def load(contract: str, source: str | None = None) -> Artifact:
    """Read `out/<source>/<contract>.json`. `source` defaults to `<contract>.sol`."""
    path = OUT_DIR / (source or f"{contract}.sol") / f"{contract}.json"
    data = json.loads(path.read_text())
    bytecode = data["bytecode"]["object"]
    if not bytecode or bytecode == "0x":
        raise ValueError(f"{contract} has no bytecode (abstract or interface?): {path}")
    return Artifact(name=contract, abi=data["abi"], bytecode=bytecode)
