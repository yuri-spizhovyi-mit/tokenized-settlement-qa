# How to run everything

Every suite, locally and as CI runs it. For what each suite proves, see
[`test-strategy.md`](test-strategy.md).

## Contracts (Foundry)

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

## Python suites (Anvil)

The integration suite (`tests/integration/`) drives the contracts over JSON-RPC
with web3.py, against a local Anvil node that the test session starts and stops
itself. It deploys from `contracts/out/`, so build the contracts first.

You need Python **3.12**, plus Foundry v1.8.7 (which provides `anvil`) on your `PATH`:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
(cd contracts && forge build)
.venv/bin/pytest                      # all Python suites incl. the load smoke run: ~2 min
.venv/bin/pytest tests/integration    # one suite
.venv/bin/pytest -k signed_release    # one group
```

The indexer and reconciliation suites (`tests/indexing/`, `tests/reconciliation/`)
use a fresh SQLite file per test by default. To run them against Postgres, as CI does:

```bash
INDEXER_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/indexer .venv/bin/pytest
```

The transfer API (`services/transfer_api/`) serves `POST /transfers` (with an
`Idempotency-Key` header) and `GET /transfers/{id}`. The hot wallet's key comes from an
environment variable, never a flag:

```bash
TRANSFER_API_PRIVATE_KEY=0x... PYTHONPATH=services .venv/bin/python -m transfer_api \
  --rpc http://127.0.0.1:8545 --token 0x... --db sqlite:///transfer-api.db
```

## UI tests (Playwright)

`ui/` is a one-page transfer UI that talks only to an EIP-1193 wallet. `tests/ui/`
drives it in Chromium through a scriptable mock wallet (approve or reject, recorded
requests) against Anvil. Failed tests keep a trace:

```bash
.venv/bin/python -m playwright install chromium   # once, outside cloud sessions
.venv/bin/pytest tests/ui --tracing retain-on-failure --output test-results
.venv/bin/python -m playwright show-trace test-results/<test>/trace.zip
```

## Load tests (Locust)

`tests/load/run_load.py` starts the whole system (Anvil with 1-second blocks, the
transfer API, the indexer), drives it with Locust, then drains and checks that nothing
was lost. It writes `load-results/report.md`; see `docs/performance-report.md`.

```bash
.venv/bin/python tests/load/run_load.py --profile smoke     # 8 users, 15 s (also in pytest)
.venv/bin/python tests/load/run_load.py --profile nightly   # 40 users, 3 min (nightly CI job)
.venv/bin/python tests/load/profile_worker.py               # where the worker's time goes
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

CI runs all of them in `.github/workflows/integration.yml` (job
`integration (pytest + anvil)`, with a Postgres 16 service container) and publishes the report to GitHub Pages from `main`.

## Traceability and the evidence pack

```bash
.venv/bin/python scripts/rtm.py            # regenerate docs/rtm.md and docs/rtm.csv
.venv/bin/python scripts/rtm.py --check    # what CI's traceability job runs
```

The evidence pack is built by `.github/workflows/release.yml` (on a `v*` tag, or run it
by hand from the Actions tab). To build it locally, produce the result files the
workflow lists in `scripts/evidence_pack.py`'s docstring, then:

```bash
.venv/bin/python scripts/evidence_pack.py --results results --out dist --version local
```

## Cloud sessions (Claude Code on the web)

In a Claude cloud session, where `foundryup` is blocked, run
`bash scripts/cloud-setup.sh` instead. It installs the same pinned version from
GitHub release binaries.
