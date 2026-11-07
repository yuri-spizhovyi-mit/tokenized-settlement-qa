"""The system under test for load runs, as its own process (book Ch 11).

    python tests/load/sut.py --out DIR --accounts 20 [--block-time 1] [--db URL]

It starts Anvil, deploys SettlementToken + Escrow from contracts/out/, funds a hot
wallet and one account per simulated user, then serves:
- the transfer API with its background worker (one hot wallet, TAPI-D5);
- the indexer, syncing in a loop;
- a sampler that writes `samples.jsonl` every 0.5 s: chain head, indexer tip and lag,
  transfer backlog by status, and how long a full-balance query takes (IDX-D4).

It writes `env.json` (URLs, addresses, user keys) for Locust, then waits for a `stop`
file in DIR. On stop it **drains**: it waits until every accepted transfer is final,
lets the indexer catch up, reconciles the database with the chain at the indexer's
tip, and writes `results.json`. Load is only meaningful if nothing was lost.

Locust runs in a separate process: it monkey-patches threading with gevent, which
would interfere with the API's threads.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO_ROOT / "tests"), str(REPO_ROOT / "services")]

from eth_account import Account  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from web3 import Web3  # noqa: E402

import transfer_api  # noqa: E402
from indexer import Indexer, queries  # noqa: E402
from indexer import create_schema as create_index_schema  # noqa: E402
from indexer import drop_schema as drop_index_schema  # noqa: E402
from indexer import make_engine as make_index_engine  # noqa: E402
from lib import artifacts  # noqa: E402
from lib.accounts import Accounts  # noqa: E402
from lib.anvil import DEFAULT_MNEMONIC, Anvil  # noqa: E402
from lib.api_server import ApiServer  # noqa: E402
from lib.chain import Chain  # noqa: E402
from lib.clients import EscrowClient, TokenClient  # noqa: E402
from lib.reconcile import reconcile  # noqa: E402
from transfer_api.app import create_app  # noqa: E402
from transfer_api.db import transfers  # noqa: E402

USDC = 10**6
USER_FUNDS = 1_000_000 * USDC
HOT_WALLET_FUNDS = 100_000_000 * USDC
FIRST_USER_INDEX = 10  # indexes 0-9 are the named test roles (lib/accounts.py)
FINAL = {"confirmed", "failed"}


def user_accounts(n: int) -> list:
    Account.enable_unaudited_hdwallet_features()
    return [
        Account.from_mnemonic(DEFAULT_MNEMONIC, account_path=f"m/44'/60'/0'/0/{FIRST_USER_INDEX + i}")
        for i in range(n)
    ]


class Sampler(threading.Thread):
    def __init__(self, w3, indexer, engine, path: Path, interval: float = 0.5):
        super().__init__(daemon=True)
        self.w3, self.indexer, self.engine = w3, indexer, engine
        self.path, self.interval = path, interval
        self.stop_event = threading.Event()
        self.t0 = time.monotonic()

    def run(self) -> None:
        with self.path.open("a") as out:
            while not self.stop_event.wait(self.interval):
                try:
                    out.write(json.dumps(self.sample()) + "\n")
                    out.flush()
                except Exception as exc:  # a sample is best-effort; keep sampling
                    out.write(json.dumps({"t": time.monotonic() - self.t0, "error": repr(exc)}) + "\n")

    def sample(self) -> dict:
        head = self.w3.eth.block_number
        tip = self.indexer.tip()
        with self.engine.connect() as conn:
            by_status = dict(conn.execute(
                select(transfers.c.status, func.count()).group_by(transfers.c.status)
            ).all())
        started = time.perf_counter()
        holders = len(queries.balances(self.indexer.engine))
        balances_ms = (time.perf_counter() - started) * 1000
        return {
            "t": round(time.monotonic() - self.t0, 3),
            "head": head,
            "indexer_tip": tip,
            "lag_blocks": None if tip is None else head - tip,
            "transfers": by_status,
            "balances_query_ms": round(balances_ms, 2),
            "holders": holders,
        }


class Loop(threading.Thread):
    """Call `fn` every `interval` seconds until stopped; count and time the calls."""

    def __init__(self, fn, interval: float):
        super().__init__(daemon=True)
        self.fn, self.interval = fn, interval
        self.stop_event = threading.Event()
        self.calls, self.errors, self.busy_s = 0, 0, 0.0

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            started = time.perf_counter()
            try:
                self.fn()
            except Exception as exc:
                self.errors += 1
                print(f"{self.fn.__qualname__} failed: {exc!r}", flush=True)
            self.calls += 1
            self.busy_s += time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--accounts", type=int, default=20, help="per-user funded accounts")
    parser.add_argument("--block-time", type=float, default=1.0)
    parser.add_argument("--db", default=None, help="SQLAlchemy URL (default: SQLite in --out)")
    parser.add_argument("--drain-timeout", type=float, default=120.0)
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    db_url = args.db or f"sqlite:///{out / 'load.db'}"

    artifacts.ensure_built()
    anvil = Anvil.start(accounts=FIRST_USER_INDEX + args.accounts)
    w3 = Web3(Web3.HTTPProvider(anvil.url, request_kwargs={"timeout": 30}))
    chain = Chain(w3)
    acc = Accounts.from_mnemonic()
    users = user_accounts(args.accounts)

    # ---- deploy and fund, with automine (fast) ----------------------------------------
    token = TokenClient(chain, chain.deploy(artifacts.load("SettlementToken"), acc.admin, acc.admin.address))
    escrow = EscrowClient(chain, chain.deploy(
        artifacts.load("Escrow"), acc.admin, token.address, acc.arbiter.address, acc.escrow_admin.address,
    ), token)
    token.grant_role(acc.admin, token.MINTER_ROLE, acc.minter.address)
    token.grant_role(acc.admin, token.ALLOWLIST_ROLE, acc.compliance.address)
    for who in [acc.relayer.address, escrow.address] + [u.address for u in users]:
        token.set_allowlisted(acc.compliance, who, True)
    token.mint(acc.minter, acc.relayer.address, HOT_WALLET_FUNDS)
    for u in users:
        token.mint(acc.minter, u.address, USER_FUNDS)
    start_block = 0

    # ---- services -----------------------------------------------------------------------
    index_engine = make_index_engine(db_url)
    drop_index_schema(index_engine)
    create_index_schema(index_engine)
    indexer = Indexer(w3, index_engine, token.contract, escrow.contract, start_block=start_block)
    api_engine = transfer_api.make_engine(db_url)
    transfer_api.drop_schema(api_engine)
    transfer_api.create_schema(api_engine)
    service = transfer_api.TransferService(w3, api_engine, token.contract, acc.relayer, confirmations=1)
    server = ApiServer(create_app(service)).start()
    worker = Loop(service.tick, 0.2)
    index_loop = Loop(indexer.sync, 0.25)
    indexer.sync()  # catch up on the setup blocks first: lag samples should measure load

    # ---- from here on, real block times ---------------------------------------------
    chain._rpc("evm_setAutomine", False)
    chain._rpc("evm_setIntervalMining", int(args.block_time))
    sampler = Sampler(w3, indexer, api_engine, out / "samples.jsonl")
    for thread in (worker, index_loop, sampler):
        thread.start()

    (out / "env.json").write_text(json.dumps({
        "api_url": server.url,
        "rpc_url": anvil.url,
        "chain_id": w3.eth.chain_id,
        "token": token.address,
        "user_keys": [u.key.hex() for u in users],  # Anvil's public dev mnemonic
    }, indent=2))
    print(f"SUT ready: api={server.url} rpc={anvil.url}", flush=True)

    stop_file = out / "stop"
    load_started = time.monotonic()
    while not stop_file.exists():
        time.sleep(0.2)
    load_seconds = time.monotonic() - load_started

    # ---- drain: every accepted transfer must become final ---------------------------
    drain_started = time.monotonic()
    while time.monotonic() - drain_started < args.drain_timeout:
        with api_engine.connect() as conn:
            open_ = conn.execute(select(func.count()).select_from(transfers).where(
                transfers.c.status.not_in(FINAL))).scalar_one()
        if open_ == 0:
            break
        time.sleep(0.5)
    drain_seconds = time.monotonic() - drain_started
    for thread in (worker, sampler):
        thread.stop_event.set()
    server.stop()

    # ---- the indexer catches up, then reconcile at its tip ----------------------------
    chain._rpc("evm_mine")
    catch_up_started = time.monotonic()
    while indexer.tip() != w3.eth.block_number and time.monotonic() - catch_up_started < 60:
        time.sleep(0.25)
    index_loop.stop_event.set()
    index_loop.join(timeout=10)
    pin = indexer.tip()
    holders = [acc.relayer.address] + [u.address for u in users]
    mismatches = [str(m) for m in reconcile(token, escrow, index_engine, pin, holders)]

    with api_engine.connect() as conn:
        statuses = Counter(dict(conn.execute(
            select(transfers.c.status, func.count()).group_by(transfers.c.status)).all()))
        api_paid = sum(r.amount for r in conn.execute(
            select(transfers.c.amount).where(transfers.c.status == "confirmed")).all())
    hot_wallet_spent = HOT_WALLET_FUNDS - token.contract.functions.balanceOf(acc.relayer.address).call(
        block_identifier=pin)

    results = {
        "load_seconds": round(load_seconds, 1),
        "drain_seconds": round(drain_seconds, 1),
        "drained": drain_seconds < args.drain_timeout,
        "transfers_by_status": dict(statuses),
        "api_confirmed_amount": str(api_paid),
        "hot_wallet_spent_on_chain": str(hot_wallet_spent),
        "api_amount_matches_chain": api_paid == hot_wallet_spent,
        "worker": {"ticks": worker.calls, "errors": worker.errors, "busy_s": round(worker.busy_s, 1)},
        "indexer": {"syncs": index_loop.calls, "errors": index_loop.errors, "busy_s": round(index_loop.busy_s, 1)},
        "gap_fills": len(service.gap_fills),
        "final_block": w3.eth.block_number,
        "indexed_events": len(queries.ordered_events(index_engine)),
        "reconciled_at_block": pin,
        "reconciliation_mismatches": mismatches,
        "db": "postgres" if db_url.startswith("postgres") else "sqlite",
    }
    (out / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2), flush=True)
    anvil.stop()


if __name__ == "__main__":
    main()
