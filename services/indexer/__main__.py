"""Run the indexer against a node: `python -m indexer --help` (with `services` on PYTHONPATH).

ABIs come from `forge build` output (contracts/out/), never pasted into Python.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from web3 import Web3

from . import Indexer, create_schema, make_engine

REPO_ROOT = Path(__file__).resolve().parents[2]


def _abi(out_dir: Path, name: str) -> list:
    return json.loads((out_dir / f"{name}.sol" / f"{name}.json").read_text())["abi"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default="http://127.0.0.1:8545")
    parser.add_argument("--db", default="sqlite:///indexer.db", help="SQLAlchemy URL")
    parser.add_argument("--token", required=True)
    parser.add_argument("--escrow", required=True)
    parser.add_argument("--start-block", type=int, default=0)
    parser.add_argument("--confirmations", type=int, default=2)
    parser.add_argument("--poll", type=float, default=1.0, help="seconds between syncs")
    parser.add_argument("--once", action="store_true", help="sync once and exit")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "contracts" / "out")
    args = parser.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    engine = make_engine(args.db)
    create_schema(engine)
    indexer = Indexer(
        w3,
        engine,
        w3.eth.contract(address=Web3.to_checksum_address(args.token), abi=_abi(args.out_dir, "SettlementToken")),
        w3.eth.contract(address=Web3.to_checksum_address(args.escrow), abi=_abi(args.out_dir, "Escrow")),
        start_block=args.start_block,
        confirmations=args.confirmations,
    )
    while True:
        result = indexer.sync()
        if result.processed or result.reorgs:
            print(f"tip={result.tip} +{result.processed} blocks, {result.reorgs} reorg(s), "
                  f"-{result.rolled_back} abandoned", flush=True)
        if args.once:
            return
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
