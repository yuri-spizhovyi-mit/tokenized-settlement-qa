"""TAPI-17, TAPI-18: the API sheds load instead of queueing without limit (F-23).

Found by the load run (PR 9): the API accepted ~25 transfers/s while one hot wallet
settled ~2/s, so the backlog and every client's wait grew without bound.
"""

from __future__ import annotations

import allure
import pytest

from lib.api_server import ApiServer
from lib.builders import USDC
from lib.transfers import get, post, wait_for_attempts
from transfer_api.app import create_app
from transfer_api.db import attempts

pytestmark = allure.feature("Transfer API: backpressure")


@pytest.fixture
def small_api(make_service):
    service = make_service(max_backlog=2)
    server = ApiServer(create_app(service)).start()
    yield server, service
    server.stop()


@pytest.mark.req("TAPI-17")
def test_full_backlog_returns_503_and_the_same_key_succeeds_later(system, small_api, hot_wallet):
    """TAPI-17: with 2 transfers unsettled, a third request gets 503 + Retry-After and
    nothing is stored. A replay of an accepted key still answers 200. Once the backlog
    settles, retrying the refused request with the *same* key is accepted, and every
    transfer is paid exactly once."""
    a, chain, token = system.accounts, system.chain, system.token
    api, service = small_api
    first = post(api, "bp-1", a.payee.address, str(USDC))
    assert post(api, "bp-2", a.payee.address, str(USDC)).status_code == 202

    refused = post(api, "bp-3", a.payee.address, str(USDC))
    assert refused.status_code == 503
    assert refused.headers["Retry-After"] == "1"
    assert refused.json()["detail"]["error"] == "Busy"
    assert service._by_key("bp-3") is None
    assert post(api, "bp-1", a.payee.address, str(USDC)).json()["id"] == first.json()["id"]  # replay: 200

    service.tick()
    wait_for_attempts(service)
    chain.mine()
    service.tick()
    assert service.backlog() == 0

    retried = post(api, "bp-3", a.payee.address, str(USDC))
    assert retried.status_code == 202
    service.tick()
    wait_for_attempts(service)
    chain.mine()
    service.tick()
    assert get(api, retried.json()["id"])["status"] == "confirmed"
    assert token.balance_of(a.payee.address) == 3 * USDC


@pytest.mark.req("TAPI-18")
def test_a_tick_submits_a_bounded_batch_so_tracking_is_not_starved(system, make_service, hot_wallet):
    """TAPI-18: with 7 transfers waiting and a batch of 3, one tick signs exactly the 3
    oldest. Without the bound, a long backlog kept the worker submitting for minutes
    before it looked at a single receipt (F-23)."""
    a = system.accounts
    service = make_service(max_submit_per_tick=3, max_backlog=None)
    ids = [service.accept(f"batch-{i}", a.payee.address, str(i + 1))[0]["id"] for i in range(7)]

    service.tick()

    with service.engine.connect() as conn:
        signed = {row.transfer_id for row in conn.execute(attempts.select())}
    assert signed == set(ids[:3])
    assert [service.get(i)["status"] for i in ids[3:]] == ["accepted"] * 4
