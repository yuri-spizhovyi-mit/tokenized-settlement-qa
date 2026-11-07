# CLAUDE.md

## What this is

`tokenized-settlement-qa` is a portfolio project for a Digital Asset QA
Engineer role. It is a small tokenised settlement system (`SettlementToken` +
`Escrow`) whose **real product is its test suite**. The contracts stay small
on purpose. The tests, the traceability between requirements and tests, and
the evidence they produce are what get reviewed.

The full roadmap, milestones and definition of done are in
[`docs/plan.md`](docs/plan.md). Read it before starting a PR.

## Layout

```text
contracts/          Foundry project
  src/              SettlementToken, Escrow only: production code
  test/unit/        one behaviour per test
  test/fuzz/        property-based tests
  test/invariant/   handler + ghost-variable invariant suites
  test/fork/        mainnet fork tests (CI only, needs MAINNET_RPC_URL)
  test/security/    exploit + regression tests
  test/fixtures/    DELIBERATELY VULNERABLE contracts (vault/, PR 5). Never deploy, never import from src/
  lib/              forge-std, openzeppelin-contracts (git submodules, pinned tags)
services/           Python services
  indexer/          reorg-aware indexer (PR 7): db (schema, Uint256), indexer, queries, CLI
  transfer_api/     transfer API (PR 8): FastAPI app, service (worker tick), nonces, db, CLI
tests/              Python (3.12) suites against Anvil: integration/ (PR 6), indexing/ and
                    reconciliation/ (PR 7), transfers/ (PR 8), load/ (PR 9); ui/ later
  load/             sut.py (system under test), locustfile.py, run_load.py (smoke|nightly),
                    profile_worker.py; results in load-results/ (gitignored)
  conftest.py       session Anvil + deployment, per-test evm_snapshot/evm_revert, indexer DB
  lib/              anvil, artifacts, chain, errors, clients (Token/Escrow), builders, eip712,
                    nonces, activity, indexing, reconcile, transfers, api_server,
                    naive_indexer and naive_transfer (DELIBERATELY BROKEN)
requirements.txt    exact-pinned Python deps (web3 7.16.0); pytest.ini at the repo root
docs/               plan.md, decisions.md, findings.md, slither-triage.md, performance-report.md
                    (+ performance/ evidence), test strategy, RTM, reports
scripts/            cloud-setup.sh
.github/workflows/  contracts.yml, integration.yml, nightly.yml (load test, 03:17 UTC)
```

## Setup

In a Claude cloud session, run this first:

```bash
bash scripts/cloud-setup.sh && source ~/.bashrc
```

`foundryup` and `binaries.soliditylang.org` are blocked there. The script
installs pinned Foundry and solc 0.8.28 from GitHub release binaries (checking
their SHA-256), sets Foundry to `offline = true` in `~/.foundry/foundry.toml`,
installs Slither from PyPI into a venv, and initialises the submodules. It is
safe to re-run.

CI does not use the script. It uses `foundry-rs/foundry-toolchain`. If you
bump the Foundry version, change it in both places.

**Use the pinned Foundry version (v1.8.7) locally too.** `.gas-snapshot` is only
reproducible with the forge version that wrote it. With forge v1.7.1 or older,
`forge snapshot --check` fails on 91 of 92 entries, even though every test
passes. Outside the cloud session, install the pinned version with:

```bash
foundryup --install v1.8.7
```

Bumping Foundry therefore means three places: `scripts/cloud-setup.sh`,
`.github/workflows/contracts.yml`, and a regenerated `contracts/.gas-snapshot`
in the same commit.

Day-to-day:

```bash
cd contracts
forge build --sizes
forge test -vvv
forge snapshot --check --match-path 'test/unit/*'   # CI fails if unit-test gas moved
FOUNDRY_PROFILE=ci forge test   # CI's settings: 10,000 fuzz runs, 512 x 256 invariant calls
# Slither, exactly as CI's gate runs it (fails on High):
forge build --build-info --skip './test/**' --skip './script/**'
slither . --ignore-compile --config-file slither.config.json --fail-high
```

## Working rules

- **One branch per PR.** Use branch `feature/<slug>` (the slugs are in
  `docs/plan.md`), open the PR into `main`, and keep each PR to one
  milestone.
- CI must be green before merge. Never skip, disable or loosen a test
  without a written reason in the PR.
- **Run the full suite before pushing** (`forge test -vvv` from
  `contracts/`, plus any Python suites the PR touches). Do not push red.
- Mainnet fork tests and long nightly runs execute in CI only. Fork tests skip
  themselves when `MAINNET_RPC_URL` is unset; CI runs them in a separate job only
  when that secret exists.
- After an intended gas change, regenerate with
  `forge snapshot --match-path 'test/unit/*'` (fuzz, invariant and fork gas can't be
  pinned; see PROP-D2 in `docs/decisions.md`).
- The indexer uses SQLite in sessions and Postgres in CI (`INDEXER_DATABASE_URL`).
  Amounts are `Uint256` columns and are summed in Python ints, never floats and never SQL
  `SUM` (F-16, IDX-D3).

## Test conventions

- **Names read as specifications:** `test_<Behaviour>` for the happy path and
  `test_RevertWhen_<Condition>` for failure paths, for example
  `test_RevertWhen_SenderNotAllowlisted`. Fuzz tests use `testFuzz_...`, and
  invariants use `invariant_...`.
- Each new test names the requirement or vulnerability class it covers, in
  its NatSpec or in the RTM.
- **Assert exact custom errors.** Use
  `vm.expectRevert(abi.encodeWithSelector(Err.selector, arg1, arg2))`, never a
  bare `vm.expectRevert()` or a string match. A revert for the wrong reason is
  a failing test.
- **Assert state, not just balances.** After an action, check every piece of
  state it should change, and the state it must not change: escrow status
  enums, roles, allowances, paused flags, `totalSupply`, emitted events
  (`vm.expectEmit`), and counterparties' balances.
- Every PR description says what the tests *found* and explains one test as a
  teaching example.
- A behaviour choice the spec leaves open (a boundary, who may call what, what
  input is rejected) gets a test **and** an entry in `docs/decisions.md`.
- **Fuzz:** shape inputs with `bound()`; use `vm.assume` only to exclude specific
  addresses.
- **Invariants:** the handler catches reverts, so run with `fail_on_revert = true`,
  and check the call summary to confirm runs reach live states (`docs/findings.md`,
  F-02 to F-04).
- Findings, planted or real, go in `docs/findings.md` with their evidence.
- **Security:** every Slither result gets a row in `docs/slither-triage.md` (fixed,
  false positive with reason, or accepted risk with owner) before merge. A
  `forge-lint: disable-next-line` needs a reason on the same or previous line.
- Signed messages are tested for every replay: same signature twice, other chain,
  other contract, expired, other deal (SEC-11 to SEC-15).

## Python conventions (tests/)

- **Python 3.12, deps exact-pinned in `requirements.txt`.** Run with `.venv/bin/pytest`
  from the repo root. A venv is required; the system Python is externally managed.
- **Deploy from `contracts/out/` via `lib/artifacts.py`.** Never paste an ABI or
  bytecode into Python. CI runs `forge build` before pytest.
- **Tests use service objects** (`TokenClient`, `EscrowClient`) and `DealBuilder`, not
  raw `contract.functions`. Every write goes through `Chain.send`, which waits for
  the receipt and asserts `status == 1`, and returns the *decoded event*.
- **Assert reverts with `expect_revert("Name", *args)`.** It decodes the custom error
  from the ABIs of every deployed contract. Never assert a bare "it reverted".
- **Exact-second time travel:** use `Chain.call_at(t, …)` for reverts and `send_at(t, …)` /
  `refund(..., at=t)` for successes. Never `evm_setNextBlockTimestamp` followed by a
  normal `transact`: gas estimation ignores the pinned time (F-14). `increase_time`
  is coarse only (F-12).
- **Wait for receipts** (`wait_for_transaction_receipt`); never fetch them right after
  sending (F-13).
- **Indexer tests compare against the node**, not against a hand-written list:
  `chain_event_keys` (one `eth_getLogs`) is the reference. Reorg tests run against
  `Indexer` and `NaiveIndexer`; the naive run is `xfail(strict=True)` (F-15).
- **Reconcile at a pinned block hash**, never at `latest` (IDX-D2).
- **Transfer API tests drive the worker by hand:** the app runs on a real port with no
  background worker; tests call `service.tick()` and `wait_for_attempts()` (F-13), so
  every status change happens when the test says. Crashes are simulated with the
  `_after_write_ahead` / `_after_broadcast` hooks. Test module basenames must be unique
  across `tests/` (pytest's default import mode).
- **Load tests gate on correctness, not speed** (LOAD-D2): after the load, every accepted
  transfer must be final, the API's total must equal what the hot wallet paid, and the
  indexer must reconcile. Locust runs in its own process (gevent). Report numbers from
  `run_load.py`, and keep before/after evidence in `docs/performance/`.
- **Every test has `@pytest.mark.req("INT-xx")`** (or `IDX-xx`, `REC-xx`, `TAPI-xx`, `LOAD-xx`) (module-level `pytestmark` is fine).
  It becomes an Allure `requirement` label for the RTM.
- **Concurrency tests assert on chain state** (balances, nonces), not only on
  exceptions, since a node can drop a transaction without an error (F-11).
