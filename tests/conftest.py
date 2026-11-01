"""Shared pytest fixtures for the integration suite.

Lifecycle:
- session: one Anvil node, one deployment of SettlementToken + Escrow from
  contracts/out/, roles granted, escrow allowlisted;
- every test: `evm_snapshot` before and `evm_revert` after (autouse), so each test
  starts from that same deployed state and nothing leaks between tests.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

import pytest

# Plain `assert`s in the shared helpers get pytest's detailed failure messages too.
pytest.register_assert_rewrite("lib")

from sqlalchemy import Engine  # noqa: E402
from web3 import Web3  # noqa: E402

from indexer import Indexer, create_schema, drop_schema, make_engine  # noqa: E402

from lib import artifacts  # noqa: E402
from lib.accounts import Accounts  # noqa: E402
from lib.anvil import Anvil  # noqa: E402
from lib.chain import Chain  # noqa: E402
from lib.clients import EscrowClient, TokenClient  # noqa: E402


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


# ---- indexer database (PR 7) ---------------------------------------------------------
#
# Sessions use SQLite, one file per test. CI sets INDEXER_DATABASE_URL to its Postgres
# service container; there every test drops and recreates the schema.


@pytest.fixture
def db_url(tmp_path) -> str:
    return os.environ.get("INDEXER_DATABASE_URL") or f"sqlite:///{tmp_path / 'indexer.db'}"


@pytest.fixture
def engine(db_url: str) -> Engine:
    eng = make_engine(db_url)
    drop_schema(eng)  # a previous test (or run) on the same Postgres database
    create_schema(eng)
    yield eng
    drop_schema(eng)
    eng.dispose()


@pytest.fixture
def make_indexer(system: System, engine: Engine) -> Callable[..., Indexer]:
    """Build an indexer (or a subclass, e.g. NaiveIndexer) over this test's database.
    Pass `engine=` for a second engine on the same database: a restarted process."""

    def build(cls: type[Indexer] = Indexer, *, confirmations: int = 0, start_block: int = 0, **kw) -> Indexer:
        return cls(
            system.chain.w3,
            kw.pop("engine", engine),
            system.token.contract,
            system.escrow.contract,
            start_block=start_block,
            confirmations=confirmations,
        )

    return build


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
