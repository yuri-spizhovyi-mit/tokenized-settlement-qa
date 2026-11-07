"""Where does the transfer worker spend its time? (evidence for performance-report.md)

    .venv/bin/python tests/load/profile_worker.py [--transfers 200] [--db URL]

Accepts N transfers into a fresh service (automine on, so mining is never the wait),
then runs `tick()` under cProfile until all are confirmed, and counts JSON-RPC calls
and database statements per transfer. Prints the top functions by cumulative time.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO_ROOT / "tests"), str(REPO_ROOT / "services")]

from sqlalchemy import event  # noqa: E402
from web3 import Web3  # noqa: E402

import transfer_api  # noqa: E402
from lib import artifacts  # noqa: E402
from lib.accounts import Accounts  # noqa: E402
from lib.anvil import Anvil  # noqa: E402
from lib.chain import Chain  # noqa: E402
from lib.clients import TokenClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transfers", type=int, default=200)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    artifacts.ensure_built()
    anvil = Anvil.start()
    w3 = Web3(Web3.HTTPProvider(anvil.url))
    chain, acc = Chain(w3), Accounts.from_mnemonic()
    token = TokenClient(chain, chain.deploy(artifacts.load("SettlementToken"), acc.admin, acc.admin.address))
    token.grant_role(acc.admin, token.MINTER_ROLE, acc.minter.address)
    token.grant_role(acc.admin, token.ALLOWLIST_ROLE, acc.compliance.address)
    for who in (acc.relayer.address, acc.payee.address):
        token.set_allowlisted(acc.compliance, who, True)
    token.mint(acc.minter, acc.relayer.address, 10**18)

    db = args.db or f"sqlite:///{tempfile.mkdtemp()}/profile.db"
    engine = transfer_api.make_engine(db)
    transfer_api.drop_schema(engine)
    transfer_api.create_schema(engine)
    service = transfer_api.TransferService(w3, engine, token.contract, acc.relayer, confirmations=0)
    for i in range(args.transfers):
        service.accept(f"k{i}", acc.payee.address, "1")

    rpc_calls: Counter = Counter()
    original = w3.provider._make_request

    def counting(method, request_data):
        rpc_calls[method] += 1
        return original(method, request_data)

    # The transport call, below web3's request-caching decorator on make_request.
    w3.provider._make_request = counting
    statements = Counter()
    event.listen(engine, "before_cursor_execute",
                 lambda conn, cursor, stmt, *a: statements.update([stmt.split()[0].upper()]))

    profiler = cProfile.Profile()
    started = time.perf_counter()
    ticks = 0
    profiler.enable()
    while ticks < 50:
        service.tick()
        ticks += 1
        with engine.connect() as conn:
            from sqlalchemy import func, select

            from transfer_api.db import transfers
            done = conn.execute(select(func.count()).select_from(transfers).where(
                transfers.c.status == "confirmed")).scalar_one()
        if done == args.transfers:
            break
        time.sleep(0.05)
    profiler.disable()
    elapsed = time.perf_counter() - started

    n = args.transfers
    print(f"{n} transfers confirmed: {done}/{n} in {elapsed:.2f} s over {ticks} ticks "
          f"= {done / elapsed:.1f} transfers/s ({'sqlite' if db.startswith('sqlite') else 'postgres'})")
    print(f"JSON-RPC calls: {sum(rpc_calls.values())} total, {sum(rpc_calls.values()) / n:.1f} per transfer")
    for method, count in rpc_calls.most_common():
        print(f"  {method:32s} {count:6d}  {count / n:5.1f}/transfer")
    print(f"SQL statements: {sum(statements.values())} total, {sum(statements.values()) / n:.1f} per transfer: "
          f"{dict(statements)}")
    buffer = io.StringIO()
    pstats.Stats(profiler, stream=buffer).sort_stats("cumulative").print_stats(r"transfer_api|web3/providers|sqlalchemy/engine/base", 18)
    print(buffer.getvalue())
    anvil.stop()


if __name__ == "__main__":
    main()
