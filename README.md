# tokenized-settlement-qa

A small tokenised settlement system (`SettlementToken` + `Escrow`) whose real
product is its test suite. It's a portfolio project for a Digital Asset QA
Engineer role, and a companion to *Testing the Chain*.

- Roadmap and milestones: [`docs/plan.md`](docs/plan.md)
- Behaviour decisions and the tests that pin them: [`docs/decisions.md`](docs/decisions.md)
- What the tests found, with evidence: [`docs/findings.md`](docs/findings.md)
- Contributor and agent rules: [`CLAUDE.md`](CLAUDE.md)

The full README (architecture, findings, badges) lands with PR 12.

## Run the tests

You need [Foundry](https://getfoundry.sh) **v1.8.7**, the version pinned in CI:

```bash
foundryup --install v1.8.7
git submodule update --init
cd contracts
forge build --sizes
forge test -vvv
forge snapshot --check --match-path 'test/unit/*'
```

`FOUNDRY_PROFILE=ci forge test` runs CI's settings: 10,000 fuzz runs per property and
512 × 256 invariant calls. Fork tests against mainnet USDC need `MAINNET_RPC_URL`;
without it they are skipped.

> **The Foundry version matters.** `contracts/.gas-snapshot` records gas per test
> and is checked in CI. Gas accounting differs between forge releases. With
> forge v1.7.1 or older, `forge snapshot --check` fails on 91 of 92 entries even
> though every test passes. Use v1.8.7, or regenerate the snapshot only as part
> of a deliberate Foundry upgrade.

## Run the integration tests (Python + Anvil)

The integration suite (`tests/integration/`) drives the contracts over JSON-RPC
with web3.py, against a local Anvil node that the test session starts and stops
itself. It deploys from `contracts/out/`, so build the contracts first.

You need Python **3.12**, plus Foundry v1.8.7 (which provides `anvil`) on your `PATH`:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
(cd contracts && forge build)
.venv/bin/pytest                      # integration + indexer + reconciliation, about 50 s
.venv/bin/pytest tests/integration    # one suite
.venv/bin/pytest -k signed_release    # one group
```

The indexer and reconciliation suites (`tests/indexing/`, `tests/reconciliation/`)
use a fresh SQLite file per test by default. To run them against Postgres, as CI does:

```bash
INDEXER_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/indexer .venv/bin/pytest
```

The indexer itself (`services/indexer/`) also runs on its own against any node:

```bash
PYTHONPATH=services .venv/bin/python -m indexer --rpc http://127.0.0.1:8545 \
  --token 0x... --escrow 0x... --db sqlite:///indexer.db --confirmations 2
```

For the Allure report locally (needs Java 17+ and Node):

```bash
.venv/bin/pytest --alluredir=allure-results
npx allure-commandline@2.46.1 serve allure-results
```

CI runs all three suites in `.github/workflows/integration.yml` (job
`integration (pytest + anvil)`, with a Postgres 16 service container) and publishes the report to GitHub Pages from `main`.

## Cloud sessions

In a Claude cloud session, where `foundryup` is blocked, run
`bash scripts/cloud-setup.sh` instead. It installs the same pinned version from
GitHub release binaries.
