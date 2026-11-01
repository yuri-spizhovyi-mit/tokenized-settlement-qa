"""TAPI-05..TAPI-08: idempotency keys, sequential and under concurrency.

The concurrency tests go over real HTTP to a live server: FastAPI runs each request
on its own thread, so ten requests really do race through validation and into the
database. The UNIQUE constraint on `idempotency_key` decides the winner (TAPI-D2).
As in INT-10, the assertions are on chain state as well as on responses (F-11).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import allure
import httpx
import pytest
from sqlalchemy import func, select

from lib.builders import USDC
from lib.transfers import get, post, statuses, wait_for_attempts
from transfer_api.db import transfers

pytestmark = allure.feature("Transfer API: idempotency")

CLIENTS = 10


def _settle(service, chain) -> None:
    service.tick()
    wait_for_attempts(service)
    chain.mine()
    service.tick()


def _row_count(service) -> int:
    with service.engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(transfers)).scalar_one()


@pytest.mark.req("TAPI-05")
def test_replay_with_same_key_and_body_returns_the_same_transfer(system, api, service, hot_wallet):
    """TAPI-05: a retried request (the client never saw the first response) gets 200
    and the *same* transfer, before and after it is sent. The payee is paid once."""
    a, chain, token = system.accounts, system.chain, system.token
    first = post(api, "pay-77", a.payee.address, str(10 * USDC))
    again = post(api, "pay-77", a.payee.address, str(10 * USDC))
    assert (first.status_code, again.status_code) == (202, 200)
    assert again.json()["id"] == first.json()["id"]

    _settle(service, chain)
    after = post(api, "pay-77", a.payee.address, str(10 * USDC))
    assert after.status_code == 200 and after.json()["status"] == "confirmed"
    assert after.json()["id"] == first.json()["id"]
    assert _row_count(service) == 1
    assert token.balance_of(a.payee.address) == 10 * USDC


@pytest.mark.req("TAPI-06")
@pytest.mark.parametrize("change", ["amount", "recipient"])
def test_same_key_with_a_different_request_is_409(system, api, service, change):
    """TAPI-06: reusing a key for a different request is a client bug. It must not
    return the old transfer as if it were the new one, nor create a second one."""
    a = system.accounts
    system.token.set_allowlisted(a.compliance, a.stranger.address, True)
    assert post(api, "pay-88", a.payee.address, str(10 * USDC)).status_code == 202
    to, amount = (a.payee.address, str(11 * USDC)) if change == "amount" else (a.stranger.address, str(10 * USDC))

    response = post(api, "pay-88", to, amount)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "IdempotencyKeyReused"
    assert _row_count(service) == 1


@pytest.mark.req("TAPI-07")
def test_concurrent_requests_with_one_key_create_one_transfer_and_pay_once(system, api, service, hot_wallet):
    """TAPI-07: 10 clients send the same request with the same key at the same moment.
    Exactly one gets 202, nine get 200, all see the same id; one row, one transaction,
    and the payee's balance moves once."""
    a, chain, token = system.accounts, system.chain, system.token
    nonce_before = chain.nonce(hot_wallet.address)
    barrier = threading.Barrier(CLIENTS)

    def client(_):
        barrier.wait()
        return post(api, "same-key", a.payee.address, str(5 * USDC))

    with ThreadPoolExecutor(CLIENTS) as pool:
        responses = list(pool.map(client, range(CLIENTS)))

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] * (CLIENTS - 1) + [202]
    assert len({r.json()["id"] for r in responses}) == 1
    assert _row_count(service) == 1

    _settle(service, chain)
    assert token.balance_of(a.payee.address) == 5 * USDC
    assert chain.nonce(hot_wallet.address) == nonce_before + 1


@pytest.mark.req("TAPI-08")
def test_concurrent_distinct_requests_all_confirm_with_consecutive_nonces(system, api, service, hot_wallet):
    """TAPI-08: 10 different requests at once. Every one is confirmed, with nonces
    start..start+9 (no collision, no gap), and the payee receives exactly the sum."""
    a, chain, token = system.accounts, system.chain, system.token
    start = chain.nonce(hot_wallet.address)
    barrier = threading.Barrier(CLIENTS)

    def client(i):
        barrier.wait()
        return post(api, f"order-{i}", a.payee.address, str(i + 1))

    with ThreadPoolExecutor(CLIENTS) as pool:
        responses = list(pool.map(client, range(CLIENTS)))
    assert [r.status_code for r in responses] == [202] * CLIENTS

    _settle(service, chain)
    views = [get(api, r.json()["id"]) for r in responses]
    assert [v["status"] for v in views] == ["confirmed"] * CLIENTS
    assert sorted(v["nonce"] for v in views) == list(range(start, start + CLIENTS))
    assert all(statuses(v) == ["accepted", "signed", "submitted", "mined", "confirmed"] for v in views)
    assert token.balance_of(a.payee.address) == sum(range(1, CLIENTS + 1))
    assert chain.nonce(hot_wallet.address) == start + CLIENTS


@pytest.mark.req("TAPI-07")
def test_overlapping_ticks_in_one_process_send_each_transfer_once(system, api, service, hot_wallet):
    """TAPI-07: two threads of one process tick at once (the background worker and a
    manual tick, say). The tick lock serialises them: each transfer is sent once.
    Several *processes* per hot wallet are out of scope by design (TAPI-D5)."""
    a, chain, token = system.accounts, system.chain, system.token
    for i in range(5):
        assert post(api, f"w-{i}", a.payee.address, str(USDC)).status_code == 202
    start = chain.nonce(hot_wallet.address)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: service.tick(), range(2)))
    wait_for_attempts(service)
    assert token.balance_of(a.payee.address) == 5 * USDC
    assert chain.nonce(hot_wallet.address) == start + 5
    assert httpx.get(f"{api.url}/health").status_code == 200
