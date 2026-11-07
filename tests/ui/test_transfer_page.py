"""UI-01..UI-08: the transfer page against a real chain, through a mock EIP-1193 wallet.

Every test asserts what the person sees *and* what happened on chain (balances,
nonce) *and* what the page asked the wallet for. A UI that says "rejected" while a
transaction was sent anyway would pass a screen-only test.
"""

from __future__ import annotations

import re

import allure
import pytest
from playwright.sync_api import expect

from conftest import connect, fill
from lib.builders import USDC

pytestmark = allure.feature("UI: transfer page")


@pytest.mark.req("UI-01")
def test_connect_shows_account_and_exact_balance(app, wallet, funded):
    """UI-01: after connecting, the page shows the wallet's account and its balance,
    formatted exactly from base units (1,000,000 USDC)."""
    connect(app)
    expect(app.locator("#account")).to_have_text(funded.accounts.payer.address)
    expect(app.locator("#balance")).to_have_text("1,000,000")
    expect(app.locator(".symbol").first).to_have_text(funded.token.contract.functions.symbol().call())
    assert wallet.methods()[0] == "eth_requestAccounts"


@pytest.mark.req("UI-02")
def test_transfer_confirms_and_moves_exactly_the_amount(app, wallet, funded):
    """UI-02: a normal transfer is simulated, signed once, mined, and reported as
    confirmed. Balances move by exactly 125.5 USDC; the form clears the amount."""
    a, token = funded.accounts, funded.token
    connect(app)
    fill(app, a.payee.address, "125.5")
    app.get_by_role("button", name="Send", exact=True).click()

    expect(app.locator("#status")).to_have_text(re.compile(r"^Confirmed in block \d+: sent 125\.5 to 0x"))
    assert token.balance_of(a.payee.address) == 125_500_000
    assert token.balance_of(a.payer.address) == 1_000_000 * USDC - 125_500_000
    assert wallet.methods().count("eth_sendTransaction") == 1 and len(wallet.sent) == 1
    assert wallet.methods().index("eth_call") < wallet.methods().index("eth_sendTransaction")  # simulated first
    expect(app.get_by_label("Amount")).to_have_value("")
    expect(app.locator("#balance")).to_have_text("999,874.5")


@pytest.mark.req("UI-03")
def test_rejected_signature_sends_nothing_and_says_so(app, wallet, funded):
    """UI-03: the user clicks Reject in the wallet (EIP-1193 code 4001). The page says
    nothing was sent; no transaction exists, the nonce and both balances are unchanged,
    the form keeps its values, and Send works again."""
    a, chain, token = funded.accounts, funded.chain, funded.token
    nonce_before = chain.nonce(a.payer.address)
    connect(app)
    fill(app, a.payee.address, "40")
    wallet.mode = "reject"

    app.get_by_role("button", name="Send", exact=True).click()

    expect(app.locator("#error")).to_have_text("You rejected the transaction in your wallet. Nothing was sent.")
    expect(app.locator("#status")).to_be_hidden()
    assert wallet.methods().count("eth_sendTransaction") == 1 and wallet.sent == []
    assert chain.nonce(a.payer.address) == nonce_before
    assert token.balance_of(a.payee.address) == 0
    expect(app.get_by_label("Amount")).to_have_value("40")
    expect(app.get_by_label("Recipient address")).to_have_value(a.payee.address)
    expect(app.get_by_role("button", name="Send", exact=True)).to_be_enabled()

    wallet.mode = "approve"  # the user changes their mind: the same form now goes through
    app.get_by_role("button", name="Send", exact=True).click()
    expect(app.locator("#status")).to_have_text(re.compile("^Confirmed"))
    assert token.balance_of(a.payee.address) == 40 * USDC


@pytest.mark.req("UI-04")
def test_large_value_needs_a_second_confirmation_showing_every_digit(app, wallet, funded):
    """UI-04: 9,007,199,254.740993 USDC is 2^53 + 1 base units, past what a JS Number
    holds exactly. At or above the 10,000-token threshold the page stops and shows
    the exact amount and recipient. Cancel sends nothing; confirm sends exactly that
    many base units."""
    a, chain, token = funded.accounts, funded.chain, funded.token
    amount = 2**53 + 1
    token.mint(a.minter, a.payer.address, amount)
    connect(app)
    fill(app, a.payee.address, "9007199254.740993")

    app.get_by_role("button", name="Send", exact=True).click()
    expect(app.locator("#confirm")).to_be_visible()
    expect(app.locator("#confirm-amount")).to_have_text("9,007,199,254.740993")
    expect(app.locator("#confirm-recipient")).to_have_text(a.payee.address)
    expect(app.get_by_role("button", name="Send", exact=True)).to_be_disabled()
    assert "eth_sendTransaction" not in wallet.methods()

    app.get_by_role("button", name="Cancel").click()
    expect(app.locator("#status")).to_have_text("Cancelled. Nothing was sent.")
    assert wallet.sent == [] and token.balance_of(a.payee.address) == 0

    app.get_by_role("button", name="Send", exact=True).click()
    app.get_by_role("button", name="Yes, send it").click()
    expect(app.locator("#status")).to_have_text(re.compile(r"^Confirmed .* sent 9,007,199,254\.740993 to"))
    assert token.balance_of(a.payee.address) == amount  # exactly, not 2**53
    assert len(wallet.sent) == 1 and chain.nonce(a.payer.address) >= 1


@pytest.mark.req("UI-04")
def test_amount_just_below_threshold_needs_no_confirmation(app, wallet, funded):
    """UI-04: the boundary. 9,999.999999 is below 10,000 tokens: sent directly."""
    connect(app)
    fill(app, funded.accounts.payee.address, "9999.999999")
    app.get_by_role("button", name="Send", exact=True).click()
    expect(app.locator("#status")).to_have_text(re.compile("^Confirmed"))
    expect(app.locator("#confirm")).to_be_hidden()


@pytest.mark.req("UI-05")
@pytest.mark.parametrize("amount, message", [
    ("", "Enter an amount like 125 or 125.50"),
    ("abc", "Enter an amount like 125 or 125.50"),
    ("-5", "Enter an amount like 125 or 125.50"),
    ("1e6", "Enter an amount like 125 or 125.50"),
    ("1,000", "Enter an amount like 125 or 125.50"),
    ("0", "Amount must be greater than zero"),
    ("0.000000", "Amount must be greater than zero"),
    ("1.0000001", "At most 6 decimal places"),
    ("1000001", "Insufficient balance: you have 1,000,000"),
])
def test_invalid_amounts_are_refused_before_the_wallet_is_asked(app, wallet, funded, amount, message):
    """UI-05: each bad amount gets a specific message, and the wallet is never asked to
    sign (UI-D2)."""
    connect(app)
    fill(app, funded.accounts.payee.address, amount)
    app.get_by_role("button", name="Send", exact=True).click()
    expect(app.locator("#error")).to_have_text(message)
    assert "eth_sendTransaction" not in wallet.methods()


@pytest.mark.req("UI-05")
@pytest.mark.parametrize("recipient", ["", "0x1234", "payee.eth", "0x" + "g" * 40])
def test_invalid_recipients_are_refused(app, wallet, funded, recipient):
    connect(app)
    fill(app, recipient, "1")
    app.get_by_role("button", name="Send", exact=True).click()
    expect(app.locator("#error")).to_have_text("Enter a recipient address: 0x followed by 40 hex characters")
    assert "eth_sendTransaction" not in wallet.methods()


@pytest.mark.req("UI-06")
def test_transfer_that_would_revert_is_explained_and_never_signed(app, wallet, funded):
    """UI-06: the stranger isn't allowlisted. The simulation reverts with
    AccountNotAllowlisted(stranger); the page explains it in words, and the wallet is
    never asked to sign a transaction that would fail (UI-D3)."""
    stranger = funded.accounts.stranger.address
    connect(app)
    fill(app, stranger, "1")
    app.get_by_role("button", name="Send", exact=True).click()
    expect(app.locator("#error")).to_have_text(f"Account {stranger.lower()} is not on the allowlist.")
    assert "eth_sendTransaction" not in wallet.methods()


@pytest.mark.req("UI-06")
def test_revert_after_simulation_is_reported_as_failed_on_chain(app, wallet, funded):
    """UI-06: the simulation passes, then compliance delists the payee before the
    transaction is mined (the wallet's before_send hook). It's mined with status 0, and
    the page says "Failed on chain" with the decoded reason, not "Confirmed"."""
    a, token = funded.accounts, funded.token
    wallet.before_send = lambda tx: token.set_allowlisted(a.compliance, a.payee.address, False)
    connect(app)
    fill(app, a.payee.address, "3")
    app.get_by_role("button", name="Send", exact=True).click()
    expect(app.locator("#status")).to_have_text(re.compile(
        rf"^Failed on chain in block \d+: Account {a.payee.address.lower()} is not on the allowlist\.$"))
    assert len(wallet.sent) == 1 and token.balance_of(a.payee.address) == 0


@pytest.mark.req("UI-07")
def test_wrong_network_blocks_sending(page, ui_url, funded):
    """UI-07: the wallet is on chain 1, the page expects 31337: a clear message, and no
    transfer form at all, so nothing can be signed for the wrong chain."""
    from lib.wallet import INIT_SCRIPT, MockWallet

    wallet = MockWallet(funded.chain.w3, funded.accounts.payer, chain_id=1)
    page.expose_function("__wallet", wallet.handle)
    page.add_init_script(INIT_SCRIPT)
    page.goto(f"{ui_url}/index.html?token={funded.token.address}&chainId=31337")
    page.get_by_role("button", name="Connect wallet").click()
    expect(page.locator("#network")).to_have_text(
        "Wrong network: your wallet is on chain 1, this page needs chain 31337.")
    expect(page.locator("#transfer")).to_be_hidden()
    expect(page.get_by_role("button", name="Send", exact=True)).to_have_count(0)
    assert "eth_sendTransaction" not in wallet.methods()


@pytest.mark.req("UI-08")
def test_double_click_sends_exactly_one_transaction(app, wallet, funded):
    """UI-08: an impatient double click on Send produces one transaction, not two."""
    a, chain, token = funded.accounts, funded.chain, funded.token
    nonce_before = chain.nonce(a.payer.address)
    connect(app)
    fill(app, a.payee.address, "7")
    app.get_by_role("button", name="Send", exact=True).dblclick()
    expect(app.locator("#status")).to_have_text(re.compile("^Confirmed"))
    assert len(wallet.sent) == 1
    assert chain.nonce(a.payer.address) == nonce_before + 1
    assert token.balance_of(a.payee.address) == 7 * USDC


@pytest.mark.req("UI-03")
def test_rejected_connection_is_reported(app, wallet):
    """UI-03: rejecting the *connection* request is reported too, and nothing else is
    asked of the wallet."""
    wallet.mode = "reject"
    app.get_by_role("button", name="Connect wallet").click()
    expect(app.locator("#error")).to_have_text("Connection request rejected in your wallet.")
    assert wallet.methods() == ["eth_requestAccounts"]


@pytest.mark.req("UI-08")
def test_two_submits_in_the_same_tick_send_one_transaction(app, wallet, funded):
    """UI-08: a disabled button stops a second *click*, but not a second form
    submission (Enter key, a script, an extension). Two submits in the same event-loop
    tick must still produce one transaction: the page's own in-flight guard, not the
    button, is what stops the second one. (Mutation M2 in docs/findings.md, F-27.)"""
    a, chain, token = funded.accounts, funded.chain, funded.token
    nonce_before = chain.nonce(a.payer.address)
    connect(app)
    fill(app, a.payee.address, "5")
    app.evaluate("() => { const f = document.getElementById('transfer'); f.requestSubmit(); f.requestSubmit(); }")
    expect(app.locator("#status")).to_have_text(re.compile("^Confirmed"))
    app.wait_for_timeout(1000)  # give a second, unwanted transaction time to appear
    assert len(wallet.sent) == 1 and wallet.methods().count("eth_sendTransaction") == 1
    assert chain.nonce(a.payer.address) == nonce_before + 1
    assert token.balance_of(a.payee.address) == 5 * USDC
