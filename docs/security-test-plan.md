# Security test plan

Threats to the settlement system, and the tests and controls that cover each one (book
Ch 9–10, 13). Every test ID is traced in [`rtm.md`](rtm.md). Static analysis is triaged in
[`slither-triage.md`](slither-triage.md).

## Assets and trust boundaries

| Asset | Held by | Who may move it |
|---|---|---|
| Token balances | `SettlementToken` | allowlisted holders; `MINTER_ROLE` mints and burns |
| Escrowed funds | `Escrow` | the arbiter (release); anyone after the deadline (refund, to the payer only) |
| Governance | `DEFAULT_ADMIN_ROLE` on both contracts | the admin, through a two-day two-step transfer |
| Hot-wallet funds | transfer API's key | the API's worker only |
| Indexed truth | indexer database | the indexer; read by reconciliation and reports |

Trust boundaries: the chain ↔ off-chain services (reorgs, dropped transactions);
client ↔ API (retries, concurrency); user ↔ wallet ↔ page (rejection, wrong network,
misleading amounts).

## Threats and coverage

| # | Threat (STRIDE) | Control | Tests | Evidence |
|---|---|---|---|---|
| T1 | **Elevation**: a role-less account mints, pauses or edits the allowlist | AccessControl, one role per duty | TOK-02..04 role matrix (every function × every role), PROP-06 fuzz | |
| T2 | **Elevation**: governance bricked by renounce or a mistaken handover | AccessControlDefaultAdminRules, 2-day delay | SEC-05, SEC-06 | issue #3, F-08 |
| T3 | **Tampering**: compliance bypass through `transferFrom` or the escrow | `_update` checks sender, spender and recipient | TOK-05, TOK-06, ESC-08, PROP-12 (real USDC blacklist) | issue #5 |
| T4 | **Tampering**: re-entrancy drains a contract | checks-effects-interactions; `nonReentrant` | SEC-01 exploit (planted), SEC-02/03 regressions, SEC-04 | F-09 |
| T5 | **Spoofing / replay**: a signed release reused | EIP-712 domain + per-arbiter nonce + deadline | SEC-11 same signature twice, SEC-12 other chain, SEC-13 other contract, SEC-14 expired / altered deadline, SEC-15 other deal, SEC-16 non-arbiter / rotated-out / malleated; INT-06/07 from Python | |
| T6 | **Spoofing**: an old arbiter's key after rotation | rotation consumes the outgoing arbiter's nonce | SEC-07..09, SEC-16 | F-07 |
| T7 | **Tampering**: funds paid twice or settled twice | terminal states | ESC-06, PROP-10 invariant | F-01 (planted `memory` bug caught) |
| T8 | **Denial of service**: pause or delisting strands funds | refund path survives delisting; unpause restores | ESC-08, ESC-09, TOK-07 | |
| T9 | **Repudiation / integrity off chain**: the indexer reports a fork that no longer exists | parent-hash and tip checks; reconciliation at a pinned hash | IDX-01..03, REC-01..06 | F-15, F-18 |
| T10 | **Tampering off chain**: amounts corrupted by storage types | exact `Uint256` column; BigInt in the UI | IDX-07, TAPI-04, UI-04/10 | F-16, F-22 |
| T11 | **Duplicate payment by the service** | idempotency key UNIQUE; write-ahead signed bytes | TAPI-05..07, TAPI-10/11 | F-21 |
| T12 | **Denial of service**: one nonce gap stalls the hot wallet | re-sync after refusal; gap filler | INT-10, TAPI-13..16 | F-11, F-19 |
| T13 | **Denial of service**: unbounded backlog under load | 503 backpressure; bounded worker batches | TAPI-17/18, LOAD-01..03 | F-23 |
| T14 | **Spoofing at the UI**: the user signs something other than what they meant | simulate first; exact large-value confirmation; wrong-network block | UI-04, UI-06, UI-07, UI-09 | F-28 |
| T15 | **Repudiation at the UI**: "rejected" shown but sent, or double sent | 4001 handling; in-flight guard | UI-03, UI-08 | F-27 |
| T16 | **Information disclosure**: secrets in logs or the repo | RPC URL only as a CI secret; hot-wallet key only from an environment variable; GitGuardian on every PR | CI configuration (fork job never prints the URL) | F-06 |

## Static analysis

- Slither runs on every PR and **fails on High** in the gated scope (`src/`).
- Every result, in the gated scope and in the deliberately vulnerable fixtures, has a
  verdict and an owner in `slither-triage.md`. The fixtures' intended High
  (`reentrancy-eth`) is reproducible with the documented command.
- forge-lint runs with every build. Each suppression carries a reason on the line
  above.

## Deliberately vulnerable code

The planted re-entrancy lives in `contracts/test/fixtures/vault/`, never in `src/`
(SEC-D1). A CI step fails if a `DELIBERATELY VULNERABLE` banner appears under `src/`.

## Not covered

- Formal verification of the contracts.
- Economic attacks (MEV, front-running of `refund`): refund always pays the payer
  (ESC-D1), which limits the gain from front-running to gas.
- The key custody of the admin, arbiter and hot wallet.
- Penetration testing of a deployed API (TLS, auth): the API has no authentication in
  this project. That is the first thing a production deployment adds.
