"""TAPI-10..TAPI-12: restarts and crashes lose nothing and pay nothing twice.

A "restart" is a new TransferService (new nonce manager, new engine) over the same
database, as a new process would be. Crashes are raised from the two hooks around
the broadcast: after the write-ahead attempt is stored, and after it is sent.
"""

from __future__ import annotations

import allure
import pytest

from lib.api_server import ApiServer
from lib.builders import USDC
from lib.naive_transfer import NaiveRestartService
from lib.transfers import get, post, statuses, wait_for_attempts
from transfer_api import TransferService, make_engine
from transfer_api.app import create_app

pytestmark = allure.feature("Transfer API: restart")


class SimulatedCrash(RuntimeError):
    pass


class CrashBeforeBroadcast(TransferService):
    def _after_write_ahead(self, transfer_id):
        raise SimulatedCrash("killed after storing the signed tx, before sending it")


class CrashAfterBroadcast(TransferService):
    def _after_broadcast(self, transfer_id):
        raise SimulatedCrash("killed after sending, before recording it")


def _restart_and_settle(make_service, db_url, chain, cls=TransferService):
    service = make_service(cls, engine=make_engine(db_url))
    service.tick()
    wait_for_attempts(service)
    chain.mine()
    service.tick()
    return service


@pytest.mark.req("TAPI-10")
def test_crash_before_broadcast_then_restart_sends_the_stored_tx_once(system, api, make_service, hot_wallet, db_url):
    """TAPI-10: the signed transaction is in the database but never reached the node.
    The restarted worker broadcasts those exact bytes: same hash, same nonce."""
    a, chain, token = system.accounts, system.chain, system.token
    nonce_before = chain.nonce(hot_wallet.address)
    transfer = post(api, "crash-1", a.payee.address, str(12 * USDC)).json()

    with pytest.raises(SimulatedCrash):
        make_service(CrashBeforeBroadcast).tick()
    stalled = get(api, transfer["id"])
    assert stalled["status"] == "signed" and len(stalled["attempts"]) == 1
    assert chain.nonce(hot_wallet.address) == nonce_before  # nothing reached the node

    _restart_and_settle(make_service, db_url, chain)

    final = get(api, transfer["id"])
    assert final["status"] == "confirmed"
    assert final["tx_hash"] == stalled["attempts"][0]  # the stored bytes, not a new signature
    assert token.balance_of(a.payee.address) == 12 * USDC
    assert chain.nonce(hot_wallet.address) == nonce_before + 1


@pytest.mark.req("TAPI-11")
@pytest.mark.parametrize("cls", [
    TransferService,
    pytest.param(NaiveRestartService, marks=pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="F-21: re-signing after a crash pays the recipient twice"), id="NaiveRestartService"),
])
def test_crash_after_broadcast_then_restart_pays_exactly_once(system, api, make_service, hot_wallet, db_url, cls):
    """TAPI-11: the worker dies after the node accepted the transaction but before the
    database says so. The restarted worker must recognise its own transaction ("nonce
    too low", and a receipt exists for the stored hash) and not send the payment again."""
    a, chain, token = system.accounts, system.chain, system.token
    nonce_before = chain.nonce(hot_wallet.address)
    transfer = post(api, "crash-2", a.payee.address, str(12 * USDC)).json()
    with pytest.raises(SimulatedCrash):
        make_service(CrashAfterBroadcast).tick()
    wait_for_attempts(make_service())
    assert get(api, transfer["id"])["status"] == "signed"
    assert token.balance_of(a.payee.address) == 12 * USDC  # it did go through

    _restart_and_settle(make_service, db_url, chain, cls)

    assert token.balance_of(a.payee.address) == 12 * USDC  # ...and only once
    assert chain.nonce(hot_wallet.address) == nonce_before + 1
    assert get(api, transfer["id"])["status"] == "confirmed"


@pytest.mark.req("TAPI-11")
def test_naive_restart_re_signs_and_pays_twice(system, api, make_service, hot_wallet, db_url):
    """TAPI-11 (F-21): pins *why* the naive run fails. It signs a second transaction
    with the next nonce, and both are valid: the payee receives 24, not 12."""
    a, chain, token = system.accounts, system.chain, system.token
    nonce_before = chain.nonce(hot_wallet.address)
    transfer = post(api, "crash-3", a.payee.address, str(12 * USDC)).json()
    with pytest.raises(SimulatedCrash):
        make_service(CrashAfterBroadcast).tick()

    _restart_and_settle(make_service, db_url, chain, NaiveRestartService)

    view = get(api, transfer["id"])
    assert token.balance_of(a.payee.address) == 24 * USDC
    assert chain.nonce(hot_wallet.address) == nonce_before + 2
    assert len(view["attempts"]) == 2 and view["status"] == "confirmed"  # and it looks fine


@pytest.mark.req("TAPI-12")
def test_server_restart_keeps_accepted_transfers_and_replays(system, make_service, hot_wallet, db_url):
    """TAPI-12: stop the whole server with a transfer accepted but not sent; start a new
    server and service on the same database. The transfer is still there, a replay of
    the request finds it, and the new worker sends it once."""
    a, chain, token = system.accounts, system.chain, system.token
    first = ApiServer(create_app(make_service())).start()
    transfer = post(first, "restart-1", a.payee.address, str(7 * USDC)).json()
    first.stop()

    service = make_service(engine=make_engine(db_url))
    second = ApiServer(create_app(service)).start()
    try:
        replay = post(second, "restart-1", a.payee.address, str(7 * USDC))
        assert replay.status_code == 200 and replay.json()["id"] == transfer["id"]
        service.tick()
        wait_for_attempts(service)
        chain.mine()
        service.tick()
        assert statuses(get(second, transfer["id"]))[-1] == "confirmed"
    finally:
        second.stop()
    assert token.balance_of(a.payee.address) == 7 * USDC
