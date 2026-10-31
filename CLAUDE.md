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
docs/               plan.md, decisions.md, findings.md, test strategy, RTM, security plan, reports
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
- **Fuzz:** shape inputs with `bound()`; use `vm.assume` only to exclude specific
  addresses.
- **Invariants:** the handler catches reverts, so run with `fail_on_revert = true`,
  and check the call summary to confirm runs reach live states (`docs/findings.md`,
  F-02 to F-04).
- Findings, planted or real, go in `docs/findings.md` with their evidence.
