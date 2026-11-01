"""Locust users for the load runs (book Ch 11). Started by run_load.py, which sets
LOAD_ENV to the SUT's env.json.

- `ApiWriteUser`: POST /transfers with a fresh Idempotency-Key, to a random user. All
  of these go through the API's single hot wallet.
- `ApiReadUser`: GET /transfers/{id} for transfers already created.
- `ChainWriteUser`: **per-user accounts**. Each Locust user owns one funded account
  and its own nonce counter, and signs `transfer` locally. Load generators that
  share a sender would measure their own nonce collisions (F-11), not the system.
- `ChainReadUser`: `balanceOf` over JSON-RPC, the read path a wallet or explorer uses.

Chain calls are reported to Locust as request type "rpc", so they appear in the same
stats table as the HTTP calls.
"""

from __future__ import annotations

import itertools
import json
import os
import random
import time
import uuid
from pathlib import Path

from eth_account import Account
from locust import HttpUser, User, between, events, task
from web3 import Web3

ENV = json.loads(Path(os.environ["LOAD_ENV"]).read_text())
ABI = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "out" / "SettlementToken.sol"
                  / "SettlementToken.json").read_text())["abi"]
USERS = [Account.from_key(k) for k in ENV["user_keys"]]
_next_account = itertools.count()
CREATED_IDS: list[str] = []  # transfer ids seen by this Locust process
GWEI = 10**9


def _recipient() -> str:
    return random.choice(USERS).address


class ApiWriteUser(HttpUser):
    """A well-behaved client: on 503 it waits Retry-After and resends the *same*
    request with the *same* key (TAPI-D8), which idempotency makes safe."""

    host = ENV["api_url"]
    weight = 3
    wait_time = between(0.05, 0.2)
    pending: tuple[str, dict] | None = None

    @task
    def create_transfer(self):
        if self.pending is None:
            self.pending = (str(uuid.uuid4()), {"to": _recipient(), "amount": str(random.randint(1, 10**6))})
        key, body = self.pending
        with self.client.post("/transfers", json=body, headers={"Idempotency-Key": key},
                              name="POST /transfers", catch_response=True) as response:
            if response.status_code in (200, 202):
                if response.status_code == 202:
                    CREATED_IDS.append(response.json()["id"])
                self.pending = None
                response.success()
            elif response.status_code == 503:
                response.success()  # expected backpressure, counted separately below
                events.request.fire(request_type="backpressure", name="503 Retry-After", response_time=0,
                                    response_length=0, exception=None, context={})
                time.sleep(float(response.headers.get("Retry-After", "1")))
            else:
                response.failure(f"{response.status_code}: {response.text[:200]}")


class ApiReadUser(HttpUser):
    host = ENV["api_url"]
    weight = 3
    wait_time = between(0.05, 0.2)

    @task
    def read_transfer(self):
        if not CREATED_IDS:
            self.client.get("/health", name="GET /health")
            return
        self.client.get(f"/transfers/{random.choice(CREATED_IDS)}", name="GET /transfers/{id}")


class _RpcUser(User):
    abstract = True

    def on_start(self):
        self.w3 = Web3(Web3.HTTPProvider(ENV["rpc_url"], request_kwargs={"timeout": 30}))
        self.token = self.w3.eth.contract(address=ENV["token"], abi=ABI)

    def _timed(self, name: str, fn):
        started = time.perf_counter()
        error = None
        try:
            return fn()
        except Exception as exc:  # reported to Locust as a failure
            error = exc
        finally:
            events.request.fire(request_type="rpc", name=name, response_length=0, exception=error,
                                response_time=(time.perf_counter() - started) * 1000, context={})


class ChainWriteUser(_RpcUser):
    weight = 2
    wait_time = between(0.05, 0.2)

    def on_start(self):
        super().on_start()
        self.account = USERS[next(_next_account) % len(USERS)]
        self.nonce = self.w3.eth.get_transaction_count(self.account.address, "pending")
        self.fee_checked = 0.0

    def _max_fee(self) -> int:
        if time.monotonic() - self.fee_checked > 5:  # re-price every 5 s, not every send
            self.max_fee = 2 * self.w3.eth.get_block("latest")["baseFeePerGas"] + GWEI
            self.fee_checked = time.monotonic()
        return self.max_fee

    @task
    def send_transfer(self):
        tx = self.token.functions.transfer(_recipient(), random.randint(1, 10**6)).build_transaction({
            "from": self.account.address, "nonce": self.nonce, "gas": 100_000, "chainId": ENV["chain_id"],
            "maxFeePerGas": self._max_fee(), "maxPriorityFeePerGas": GWEI,
        })
        raw = self.account.sign_transaction(tx).raw_transaction
        if self._timed("eth_sendRawTransaction", lambda: self.w3.eth.send_raw_transaction(raw)) is not None:
            self.nonce += 1
        else:  # refused: re-sync, as the API's NonceManager does (F-19)
            self.nonce = self.w3.eth.get_transaction_count(self.account.address, "pending")


class ChainReadUser(_RpcUser):
    weight = 2
    wait_time = between(0.05, 0.2)

    @task
    def read_balance(self):
        holder = _recipient()
        self._timed("eth_call balanceOf", lambda: self.token.functions.balanceOf(holder).call())
