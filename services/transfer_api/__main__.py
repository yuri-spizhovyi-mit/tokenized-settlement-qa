"""Serve the transfer API: `PYTHONPATH=services python -m transfer_api --help`.

The hot wallet's key is read from the TRANSFER_API_PRIVATE_KEY environment variable,
never from a flag (flags end up in shell history and process lists).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import uvicorn
from eth_account import Account
from web3 import Web3

from . import TransferService, create_schema, make_engine
from .app import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default="http://127.0.0.1:8545")
    parser.add_argument("--db", default="sqlite:///transfer-api.db")
    parser.add_argument("--token", required=True)
    parser.add_argument("--confirmations", type=int, default=2)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "contracts" / "out")
    args = parser.parse_args()

    key = os.environ.get("TRANSFER_API_PRIVATE_KEY")
    if not key:
        parser.error("set TRANSFER_API_PRIVATE_KEY to the hot wallet's private key")
    w3 = Web3(Web3.HTTPProvider(args.rpc))
    abi = json.loads((args.out_dir / "SettlementToken.sol" / "SettlementToken.json").read_text())["abi"]
    engine = make_engine(args.db)
    create_schema(engine)
    service = TransferService(
        w3, engine, w3.eth.contract(address=Web3.to_checksum_address(args.token), abi=abi),
        Account.from_key(key), confirmations=args.confirmations,
    )
    uvicorn.run(create_app(service, worker_interval=1.0), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
