"""TAPI-01..TAPI-04: status progression, revert after accept, and the API contract."""

from __future__ import annotations

import allure
import httpx
import pytest

from lib.builders import USDC
from lib.transfers import get, post, statuses, wait_for_attempts

pytestmark = allure.feature("Transfer API: status")


@pytest.mark.req("TAPI-01")
def test_status_progresses_accepted_signed_submitted_mined_confirmed(system, api, service, hot_wallet):
    """TAPI-01: each worker step moves the transfer exactly one stage further, and GET
    shows it. With 1 confirmation it stays `mined` until one more block is on top."""
    a, chain, token = system.accounts, system.chain, system.token
    nonce_before = chain.nonce(hot_wallet.address)
    payee_before = token.balance_of(a.payee.address)

    created = post(api, "order-1", a.payee.address, str(250 * USDC))
    assert created.status_code == 202
    transfer = created.json()
    assert (transfer["status"], transfer["amount"], transfer["tx_hash"]) == ("accepted", str(250 * USDC), None)
    assert token.balance_of(a.payee.address) == payee_before  # accepting moves nothing

    service.tick()  # sign and broadcast (Anvil automines)
    assert get(api, transfer["id"])["status"] in ("submitted", "mined")
    wait_for_attempts(service)
    service.tick()  # sees the receipt
    view = get(api, transfer["id"])
    assert view["status"] == "mined"
    assert view["nonce"] == nonce_before and view["attempts"] == [view["tx_hash"]]
    receipt = chain.w3.eth.get_transaction_receipt(view["tx_hash"])
    assert (receipt["status"], receipt["blockNumber"]) == (1, view["block_number"])
    assert token.balance_of(a.payee.address) == payee_before + 250 * USDC

    service.tick()  # no new block: still only 0 confirmations
    assert get(api, transfer["id"])["status"] == "mined"
    chain.mine()
    service.tick()
    view = get(api, transfer["id"])
    assert statuses(view) == ["accepted", "signed", "submitted", "mined", "confirmed"]
    assert chain.nonce(hot_wallet.address) == nonce_before + 1


@pytest.mark.req("TAPI-02")
def test_revert_after_accept_is_reported_as_failed_with_decoded_error(system, api, service, hot_wallet):
    """TAPI-02: the request is valid when accepted, then compliance delists the payee
    before the worker sends. The transaction is mined with status 0: the transfer is
    `failed` with the decoded custom error, no tokens moved, and the nonce is used."""
    a, chain, token = system.accounts, system.chain, system.token
    transfer = post(api, "order-2", a.payee.address, str(40 * USDC)).json()
    assert transfer["status"] == "accepted"
    token.set_allowlisted(a.compliance, a.payee.address, False)
    wallet_before, nonce_before = token.balance_of(hot_wallet.address), chain.nonce(hot_wallet.address)

    service.tick()
    wait_for_attempts(service)
    service.tick()

    view = get(api, transfer["id"])
    assert statuses(view) == ["accepted", "signed", "submitted", "failed"]
    assert view["error"] == f"AccountNotAllowlisted('{a.payee.address}',)"
    assert chain.w3.eth.get_transaction_receipt(view["tx_hash"])["status"] == 0
    assert token.balance_of(hot_wallet.address) == wallet_before
    assert token.balance_of(a.payee.address) == 0
    assert chain.nonce(hot_wallet.address) == nonce_before + 1  # a failed tx still uses its nonce


@pytest.mark.req("TAPI-03")
def test_request_that_would_revert_now_is_rejected_and_stores_nothing(system, api, service):
    """TAPI-03: a stranger isn't allowlisted, so the simulation reverts at accept time:
    422 with the decoded error, no transfer stored, and the key stays free."""
    a = system.accounts
    response = post(api, "order-3", a.stranger.address, str(USDC))
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error": "WouldRevert", "name": "AccountNotAllowlisted", "args": [a.stranger.address],
    }
    service.tick()
    assert service._by_key("order-3") is None

    system.token.set_allowlisted(a.compliance, a.stranger.address, True)
    assert post(api, "order-3", a.stranger.address, str(USDC)).status_code == 202  # key reusable


@pytest.mark.req("TAPI-04")
@pytest.mark.parametrize("body, reason", [
    ({"to": "0x1234", "amount": "1"}, "not an address"),
    ({"to": None, "amount": "1"}, "missing address"),
    ({"amount": 1_000_000}, "amount as a JSON number"),
    ({"amount": "1.5"}, "fractional amount"),
    ({"amount": "-5"}, "negative amount"),
    ({"amount": "0"}, "zero amount"),
    ({"amount": str(2**256)}, "above uint256"),
    ({"amount": "1", "memo": "x"}, "unknown field"),
], ids=lambda v: v if isinstance(v, str) else None)
def test_malformed_requests_are_422(system, api, body, reason):
    """TAPI-04: amounts are decimal *strings* of base units (TAPI-D3): a JSON number is
    refused rather than silently converted, and so is anything outside 1..2^256-1."""
    payload = {"to": system.accounts.payee.address, **body}
    response = httpx.post(f"{api.url}/transfers", json=payload, headers={"Idempotency-Key": "k"}, timeout=30)
    assert response.status_code == 422, reason


@pytest.mark.req("TAPI-04")
def test_missing_idempotency_key_is_422_and_unknown_id_is_404(system, api):
    a = system.accounts
    no_key = httpx.post(f"{api.url}/transfers", json={"to": a.payee.address, "amount": "1"}, timeout=30)
    assert no_key.status_code == 422
    assert httpx.get(f"{api.url}/transfers/nope").status_code == 404


@pytest.mark.req("TAPI-04")
def test_amount_above_2_pow_53_round_trips_exactly(system, api, service, hot_wallet):
    """TAPI-04: 2^53 + 1 base units in, the same string out, and on chain exactly."""
    a = system.accounts
    amount = 2**53 + 1
    system.token.mint(a.minter, hot_wallet.address, amount)
    transfer = post(api, "big", a.payee.address, str(amount)).json()
    service.tick()
    wait_for_attempts(service)
    assert get(api, transfer["id"])["amount"] == str(amount)
    assert system.token.balance_of(a.payee.address) == amount
