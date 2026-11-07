"""Fixtures for the Playwright suite: a static server for ui/, and a page wired to a
MockWallet for the funded `payer` account.

Run with: .venv/bin/pytest tests/ui   (CI: .github/workflows/ui.yml)
Traces are kept for failed tests only (--tracing retain-on-failure, set in ui.yml
and in the local command in README); open one with `playwright show-trace <zip>`.
"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lib.artifacts import REPO_ROOT
from lib.builders import USDC
from lib.wallet import INIT_SCRIPT, MockWallet

UI_DIR = REPO_ROOT / "ui"
LARGE_VALUE_TOKENS = 10_000  # the page's confirmation threshold in these tests


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture(scope="session")
def ui_url():
    handler = functools.partial(_QuietHandler, directory=str(UI_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def funded(system):
    """payer holds 1,000,000 USDC; payer and payee are allowlisted; stranger is not."""
    a, token = system.accounts, system.token
    for account in (a.payer, a.payee):
        token.set_allowlisted(a.compliance, account.address, True)
    token.mint(a.minter, a.payer.address, 1_000_000 * USDC)
    return system


@pytest.fixture
def wallet(funded):
    return MockWallet(funded.chain.w3, funded.accounts.payer)


@pytest.fixture
def app(page, ui_url, wallet, funded):
    """The transfer page, connected to the mock wallet."""
    page.expose_function("__wallet", wallet.handle)
    page.add_init_script(INIT_SCRIPT)
    page.goto(f"{ui_url}/index.html?token={funded.token.address}&chainId={funded.chain.w3.eth.chain_id}"
              f"&largeValue={LARGE_VALUE_TOKENS}")
    return page


def connect(page) -> None:
    page.get_by_role("button", name="Connect wallet").click()
    page.locator("#transfer").wait_for(state="visible")


def fill(page, recipient: str, amount: str) -> None:
    page.get_by_label("Recipient address").fill(recipient)
    page.get_by_label("Amount").fill(amount)
