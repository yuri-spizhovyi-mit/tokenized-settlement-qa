# tokenized-settlement-qa — Project Plan

Repo: https://github.com/yuri-spizhovyi-mit/tokenized-settlement-qa

Portfolio project for a Digital Asset QA Engineer role. A small tokenised
settlement system (SettlementToken + Escrow) whose real product is its test
suite. Companion to the book *Testing the Chain*; chapter references below
point to it.

## Working rules

- One Claude session per PR, branch `feature/<slug>`, PR into `main`.
- CI must be green before merge. Nothing skipped without a written reason.
- Each new test names its requirement or vulnerability class.
- PR description says what the tests *found*, plus one test explained as a
  teaching example.
- Cloud sessions: run `scripts/cloud-setup.sh` first. `foundryup` and
  `binaries.soliditylang.org` are blocked there; Foundry and solc 0.8.28 come
  from GitHub release binaries, Slither from pip. CI uses the normal actions.
- Mainnet fork tests and long nightly runs execute in CI only
  (secret `MAINNET_RPC_URL`).
- Sessions use SQLite for the indexer; CI uses Postgres.

## Layout

```text
contracts/        Foundry: src/, test/{unit,fuzz,invariant,fork,security}
services/         indexer/, transfer-api/
tests/            integration/, reconciliation/, ui/, load/
docs/             plan.md, test-strategy.md, rtm.csv, security-test-plan.md,
                  slither-triage.md, performance-report.md
scripts/          cloud-setup.sh
.github/workflows contracts.yml, integration.yml, ui.yml, nightly.yml
```

## Milestones

| PR | Branch | Scope | Done when | Book | Week | Status |
|---|---|---|---|---|---|---|
| 1 | scaffold | Layout, CLAUDE.md, cloud-setup.sh, Foundry + OZ 5.x, contracts.yml | `forge test` green locally and in CI | Ch 6 | 1 | Merged: #1 |
| 2 | token | SettlementToken: ERC-20, AccessControl, Pausable, allowlist in `_update` | Role-by-function matrix tested, incl. `transferFrom` bypass | Ch 5–6 | 1–2 | Merged: #2 |
| 3 | escrow | Escrow contract + unit suite | Every row of the Ch 4.8 table is a test; `forge snapshot --check` in CI | Ch 4, 6 | 2 | Merged: #6 |
| 4 | properties | Fuzz, invariant handler + ghost vars, USDC fork tests | Invariant catches planted `memory` bug; fork job in CI | Ch 7 | 3 | Merged: #8 |
| 5 | security | Vulnerable vault, exploit + regression tests, EIP-712 release, Slither gate | 5 replay tests pass; Slither fails CI on high; triage log committed | Ch 9–10 | 4 | Merged: #9 |
| 6 | integration | pytest + web3.py on Anvil: snapshot fixtures, EscrowClient, DealBuilder | Integration CI green; Allure report on GitHub Pages | Ch 8 | 5 | In progress |
| 7 | indexer | Reorg-aware indexer, reconciliation suite | Reorg test fails on naive, passes on fixed; balances and totalSupply reconcile | Ch 3 | 5–6 |  |
| 8 | transfer-api | FastAPI service: idempotency keys, status | Status progression, idempotency under concurrency, revert after accept, stuck tx, restart | Ch 8 | 6 |  |
| 9 | load | Locust read + write, per-user accounts, indexer-lag sampler | `performance-report.md` names the bottleneck; nightly job | Ch 11 | 7 |  |
| 10 | ui | Minimal transfer page, mock EIP-1193 wallet, Playwright suite | Large-value and rejected-signature tests pass; traces on failure | Ch 12 | 7 |  |
| 11 | process | Test strategy, RTM, security plan, UAT scripts, evidence pack | Every requirement traces to a test; release workflow builds the pack | Ch 13–14 | 8 |  |
| 12 | readme | Architecture figure, findings, badges, how to run | A reviewer gets the story in 2 minutes | App A | 8 |  |

PR numbers in the Status column are GitHub pull request numbers. They differ from the
milestone numbers because follow-up issues share the same counter.

## Definition of done (every PR)

- CI green.
- Tests assert exact custom errors and state changes, not only balances.
- Test names read as specifications (`test_RevertWhen_<Condition>`).
- Yuri can explain one test from the PR aloud in under two minutes.
