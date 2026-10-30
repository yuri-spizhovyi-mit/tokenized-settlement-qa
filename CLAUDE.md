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
  src/              SettlementToken, Escrow (from PR 2 on)
  test/unit/        one behaviour per test
  test/fuzz/        property-based tests
  test/invariant/   handler + ghost-variable invariant suites
  test/fork/        mainnet fork tests (CI only, needs MAINNET_RPC_URL)
  test/security/    exploit + regression tests
  lib/              forge-std, openzeppelin-contracts (git submodules, pinned tags)
services/           indexer/, transfer-api/ (Python)
tests/              integration/, reconciliation/, ui/, load/
docs/               plan.md, decisions.md, test strategy, RTM, security plan, reports
scripts/            cloud-setup.sh
.github/workflows/  contracts.yml (more workflows land with later PRs)
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

Day-to-day:

```bash
cd contracts
forge build --sizes
forge test -vvv
forge snapshot --check   # CI fails if gas moved; run `forge snapshot` and commit if intended
```

## Working rules

- **One branch per PR.** Use branch `feature/<slug>` (the slugs are in
  `docs/plan.md`), open the PR into `main`, and keep each PR to one
  milestone.
- CI must be green before merge. Never skip, disable or loosen a test
  without a written reason in the PR.
- **Run the full suite before pushing** (`forge test -vvv` from
  `contracts/`, plus any Python suites the PR touches). Do not push red.
- Mainnet fork tests and long nightly runs execute in CI only.
- The indexer uses SQLite in sessions and Postgres in CI.

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
