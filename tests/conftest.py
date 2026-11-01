"""Shared pytest fixtures for the integration suite.

Lifecycle:
- session: one Anvil node, one deployment of SettlementToken + Escrow from
  contracts/out/, roles granted, escrow allowlisted;
- every test: `evm_snapshot` before and `evm_revert` after (autouse), so each test
  starts from that same deployed state and nothing leaks between tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from web3 import Web3

from lib import artifacts
from lib.accounts import Accounts
from lib.anvil import Anvil
from lib.chain import Chain
from lib.clients import EscrowClient, TokenClient


@dataclass(frozen=True)
class System:
    """Everything a test needs: the chain, named accounts and the two clients."""

    chain: Chain
    accounts: Accounts
    token: TokenClient
    escrow: EscrowClient


@pytest.fixture(scope="session")
def anvil() -> Anvil:
    artifacts.ensure_built()
    node = Anvil.start()
    yield node
    node.stop()


@pytest.fixture(scope="session")
def w3(anvil: Anvil) -> Web3:
    return Web3(Web3.HTTPProvider(anvil.url, request_kwargs={"timeout": 30}))


@pytest.fixture(scope="session")
def accounts() -> Accounts:
    return Accounts.from_mnemonic()


@pytest.fixture(scope="session")
def chain(w3: Web3) -> Chain:
    return Chain(w3)


@pytest.fixture(scope="session")
def system(chain: Chain, w3: Web3, accounts: Accounts) -> System:
    # The fixture's keys must be the node's unlocked dev accounts.
    assert [a.lower() for a in w3.eth.accounts[:10]] == [
        getattr(accounts, r).address.lower() for r in accounts.__dataclass_fields__
    ], "named accounts don't match Anvil's dev accounts"

    token_contract = chain.deploy(artifacts.load("SettlementToken"), accounts.admin, accounts.admin.address)
    token = TokenClient(chain, token_contract)
    escrow_contract = chain.deploy(
        artifacts.load("Escrow"),
        accounts.admin,
        token.address,
        accounts.arbiter.address,
        accounts.escrow_admin.address,
    )
    escrow = EscrowClient(chain, escrow_contract, token)

    token.grant_role(accounts.admin, token.MINTER_ROLE, accounts.minter.address)
    token.grant_role(accounts.admin, token.PAUSER_ROLE, accounts.pauser.address)
    token.grant_role(accounts.admin, token.ALLOWLIST_ROLE, accounts.compliance.address)
    token.set_allowlisted(accounts.compliance, escrow.address, True)
    return System(chain=chain, accounts=accounts, token=token, escrow=escrow)


@pytest.fixture(autouse=True)
def isolate(chain: Chain, system: System):
    """Per-test isolation: every test sees the session's freshly deployed state."""
    snapshot = chain.snapshot()
    yield
    chain.revert(snapshot)


@pytest.fixture(autouse=True)
def _requirement_label(request: pytest.FixtureRequest):
    """`@pytest.mark.req("INT-05")` becomes an Allure label and tag, so the report can
    be filtered by requirement (traceability for the RTM in PR 11)."""
    marker = request.node.get_closest_marker("req")
    if marker:
        import allure

        for requirement in marker.args:
            allure.dynamic.label("requirement", requirement)
            allure.dynamic.tag(requirement)
    yield
