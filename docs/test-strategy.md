# Test strategy

`tokenized-settlement-qa` is a tokenised settlement system: an allowlisted, pausable
ERC-20 (`SettlementToken`), an `Escrow` with arbiter and signed releases, a reorg-aware
**indexer**, an idempotent **transfer API**, and a one-page **UI**. This document says
what is tested, at which level, against which risks, and when a change may ship. The
book chapters are from *Testing the Chain*.

## 1. What can go wrong (risk register)

Testing effort follows risk. A risk is **high** when money can be lost, frozen,
double-paid or misreported, and nobody would notice from an error.

| Risk | Example | Where it is tested |
|---|---|---|
| Unauthorised money movement | a non-arbiter releases a deal; a role-less account mints | role matrix (TOK-02..04), PROP-03/06, SEC-07..09 |
| Compliance bypass | `transferFrom` from an unlisted account; an unlisted escrow | TOK-05, ESC-08, PROP-12 (real USDC blacklist) |
| Funds stuck or paid twice | a refund after release; a replayed signature | ESC-06, PROP-10, SEC-11..16 |
| Wrong amounts | float rounding above 2^53; SQLite REAL above 2^63 | PROP-01/02/11, IDX-07, TAPI-04, UI-04/10 |
| Silent off-chain divergence | an indexer that keeps an abandoned fork | IDX-01..03, REC-01..06 |
| Lost or duplicated payments in services | nonce races, crash between broadcast and record | INT-10, TAPI-07/10/11/13..16 |
| Degradation under load | confirmations stall behind a backlog | LOAD-01..03, TAPI-17/18 |
| Users misled by the UI | "rejected" while a transaction went out; the wrong button | UI-03, UI-06, UI-08 |
| Governance bricked | the admin renounces by accident | SEC-05/06 |

Every risk maps to requirements in [`requirements.csv`](requirements.csv), and every
requirement to at least one test ([`rtm.md`](rtm.md), generated and checked in CI).

## 2. Test levels

| Level | Tooling | Location | What it proves | Runs |
|---|---|---|---|---|
| Unit | Foundry | `contracts/test/unit/` | one behaviour per test: exact custom errors, every state change and non-change, events | every PR |
| Fuzz | Foundry, 10,000 runs in CI | `contracts/test/fuzz/` | properties over the input space, shaped with `bound()` | every PR |
| Invariant | Foundry handler + ghost variables, 512 × 256 calls | `contracts/test/invariant/` | escrow balance = Funded deals; supply conserved; settled is terminal | every PR |
| Fork | Foundry against mainnet USDC at a pinned block | `contracts/test/fork/` | real 6-decimal token, real blacklist behaviour | every PR, when the secret exists |
| Security | Foundry exploits + regressions; Slither | `contracts/test/security/`, `slither.config.json` | the planted re-entrancy is caught and fixed; signature replay matrix; no High finding | every PR |
| Integration | pytest + web3.py on Anvil | `tests/integration/` | the system as a client sees it: deployment from artifacts, JSON-RPC, receipts, time, nonces | every PR |
| Indexer / reconciliation | pytest, SQLite and Postgres | `tests/indexing/`, `tests/reconciliation/` | reorgs, restarts, ordering, precision; DB = chain at a pinned block | every PR |
| Service (API) | pytest + live uvicorn | `tests/transfers/` | idempotency under concurrency, crash recovery, stuck and refused transactions | every PR |
| Load | Locust in its own process | `tests/load/` | throughput and lag, *and* nothing lost under load | smoke every PR; 40 users × 3 min nightly |
| UI | Playwright + mock EIP-1193 wallet | `tests/ui/` | what the user sees, what the wallet was asked, what happened on chain | every PR |
| Acceptance | people, scripted | [`uat-scripts.md`](uat-scripts.md) | a business user can complete each flow; each script names its automated twin | before a release |

## 3. Principles

These are applied in every suite. The details are in `CLAUDE.md`.

- **Assert the exact reason.** Custom errors are asserted by selector *and* arguments
  (Solidity) or decoded by name *and* arguments (Python). A revert for the wrong reason
  fails the test.
- **Assert state, not messages.** Balances, nonces, deal states and supply are checked
  after every action. Messages and HTTP codes come second (F-11, UI-03).
- **Prove the test can fail.** Planted bugs (F-01, SEC-01), strict-xfail broken variants
  (NaiveIndexer, CountingNonceManager, NaiveRestartService) and mutation checks (F-27)
  show that each important test turns red when its guard is removed.
- **Pin what is non-deterministic.** Exact-second time travel (F-12, F-14), pinned fork
  block, per-test chain snapshots, block-hash-pinned reconciliation, receipts waited for
  rather than fetched (F-13).
- **Open choices are written down.** Every behaviour the specification left open has a
  test and an entry in [`decisions.md`](decisions.md).

## 4. Environments

| Environment | Chain | Database | Where |
|---|---|---|---|
| Developer / cloud session | Anvil (automine; interval mining for load) | SQLite file per test | `scripts/cloud-setup.sh`, `.venv` |
| CI | Anvil; mainnet fork via `MAINNET_RPC_URL` | Postgres 16 service | `.github/workflows/*.yml` |
| Release | as CI, `FOUNDRY_PROFILE=ci` | Postgres 16 | `.github/workflows/release.yml` |

Toolchains are pinned: Foundry v1.8.7, solc 0.8.28, OpenZeppelin 5.x, Slither 0.11.4,
Python 3.12 with exact pins, Playwright 1.63.0. A pin moves in its own PR, with the
gas snapshot regenerated when Foundry moves.

## 5. Entry and exit criteria

**A PR may start** when its milestone and "done when" are in [`plan.md`](plan.md) and `main`
is green.

**A PR may merge** when:
- every CI job is green: `contracts` (build, tests, gas snapshot, Slither, fork), `integration` (Python suites, load smoke, traceability), `ui`;
- every new test names its requirement;
- every new open choice has a decision;
- every new Slither result is triaged;
- findings are recorded with evidence;
- the PR description says what the tests found and explains one test.

**A release may ship** when `release.yml` builds the evidence pack. The pack refuses
to build on any of these:
- a failing or erroring test;
- production contracts below 100% coverage;
- a High Slither finding in the gated scope;
- a requirement without a test, or a test citing an unknown requirement;
- a missing result file.

The nightly load job must be green on the release commit.

## 6. Defects and findings

Every defect is a finding in [`findings.md`](findings.md), with its evidence (a failing
sequence or test output) and outcome. That covers product bugs, planted bugs proven
caught, and bugs in the tests or harness themselves (F-02, F-03, F-26, F-28). A
finding is closed by a test that fails without the fix. A behaviour accepted as-is is
a decision, not a closed finding.

## 7. What is not tested, and why

- **Real wallets and browsers other than Chromium.** The mock wallet implements the
  EIP-1193 surface the page uses. Firefox and WebKit would be a matrix in `ui.yml`.
- **Mainnet deployment and operations.** There are no deploy scripts by design
  (SEC-D1). Key management is out of scope.
- **Multi-replica transfer API.** One worker per hot wallet is a design decision
  (TAPI-D5). Sharding is the scaling path named in the performance report.
- **A reorg that removes a mined API transfer.** The indexer handles reorgs. The API
  reports submission status (TAPI-D6).
