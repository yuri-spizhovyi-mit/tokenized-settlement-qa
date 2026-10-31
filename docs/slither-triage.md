# Slither triage

Every Slither result on `contracts/src/`, with a verdict:

- **Fixed:** the code changed.
- **False positive:** the detector is wrong here, and the reason is given.
- **Accepted risk:** real, deliberate, with an owner who revisits it.

Run with Slither 0.11.6, the version pinned in CI and in `scripts/cloud-setup.sh`:

```bash
cd contracts
forge build --build-info --skip './test/**' --skip './script/**'
slither . --ignore-compile --config-file slither.config.json --fail-high   # what CI runs
slither . --ignore-compile --filter-paths "lib/|test/"                     # incl. src/vulnerable
```

## The CI gate

- **What it does:** the `slither` job in `.github/workflows/contracts.yml` uses
  `crytic/slither-action` with `fail-on: high`, scoped by `contracts/slither.config.json`.
- **What it excludes:** `lib/` (third-party), `test/`, and `src/vulnerable/`. The
  vulnerable vault *must* contain a High reentrancy finding: it's the exploit under
  test (SEC-01). Gating on it would make the gate fail on the very thing the tests
  prove. Its findings are still triaged below.
- **Proof that it bites:** with `src/vulnerable/` back in scope, `--fail-high` exits
  255 on `reentrancy-eth` (PR 5, run locally).

## Results in the gated scope (5 total; 0 High; gate passes)

| # | Detector | Severity | Location | Verdict | Reason / owner |
|---|---|---|---|---|---|
| 1 | `timestamp` | Low | `Escrow.refund`: `block.timestamp < deal.deadline` | **Accepted risk** | Deadlines are days long; validator skew is seconds. ESC-D2. Owner: Escrow maintainer; revisit if a deadline ever becomes sub-minute. |
| 2 | `timestamp` | Low | `Escrow.open`: `deadline <= block.timestamp` | **Accepted risk** | Same reasoning; rejects past deadlines. ESC-D6. Owner: Escrow maintainer. |
| 3 | `timestamp` | Low | `Escrow.releaseWithSig`: `block.timestamp > deadline` | **Accepted risk** | Signature deadlines are minutes to hours long; a few seconds of skew can't extend one meaningfully. SEC-D3. Owner: Escrow maintainer. |
| 4 | `unused-return` | Medium | `Escrow.releaseWithSig`: `ECDSA.tryRecover` | **False positive** | `recovered` and `err` are both checked: any error or a non-arbiter signer reverts with `InvalidSignature`. Only the third value, `errArg` (diagnostic detail), is unused, on purpose. Covered by SEC-11 to SEC-16, including the malleated-signature test. |
| 5 | `naming-convention` | Informational | `Escrow.DOMAIN_SEPARATOR()` | **False positive** | `DOMAIN_SEPARATOR` is the name EIP-2612 and wallets expect. Renaming it would break the interface. |

## Results outside the gate: `src/vulnerable/` (6 total; 2 High)

These contracts are test fixtures, deliberately vulnerable and never deployable
(SEC-D1). No deploy script exists for them.

| # | Detector | Severity | Location | Verdict | Reason |
|---|---|---|---|---|---|
| 6 | `reentrancy-eth` | **High** | `VulnerableVault.withdraw` | **Accepted, intended** | This *is* the bug (book Ch 9.1). `test_Exploit_ReentrancyDrainsVulnerableVault` drains 10 ETH with a 1 ETH stake. |
| 7 | `reentrancy-eth` | **High** | `GuardedVault.withdraw` | **False positive** | The call-before-effect order is kept on purpose; `nonReentrant` is the fix under test. Slither doesn't model the guard. `test_RevertWhen_ReentrantCallHitsGuard` asserts the inner call reverts with `ReentrancyGuardReentrantCall`. |
| 8 | `reentrancy-events` | Low | `VulnerableVault.withdraw` | **Accepted, intended** | Part of the same vulnerable pattern. |
| 9 | `low-level-calls` | Informational | `VulnerableVault.withdraw` | **Accepted, intended** | A plain ETH send, which is what makes re-entrancy possible. |
| 10 | `low-level-calls` | Informational | `CeiVault.withdraw` | **False positive** | `.call{value:}` is the recommended way to send ETH; the result is checked. |
| 11 | `low-level-calls` | Informational | `GuardedVault.withdraw` | **False positive** | Same. |

## forge-lint (compiler-side) findings, for completeness

`forge build` runs forge-lint. Every warning below is suppressed at its line with a
`forge-lint: disable-next-line(...)` comment stating the reason, so the build stays
warning-free:

| Lint | Location | Verdict |
|---|---|---|
| `block-timestamp` | `Escrow.open`, `refund`, `releaseWithSig` | Accepted risk: same as #1 to #3 |
| `reentrancy-events` | `Escrow._release` (`emit Released`) | **False positive:** on the signed path, the only earlier call is `ecrecover`, a precompile `staticcall` that can't re-enter |
| `reentrancy-eth`, `reentrancy-events` | `VulnerableVault.withdraw` | Intended (#6, #8) |
| `reentrancy-eth`, `reentrancy-events` | `GuardedVault.withdraw` | False positive (#7) |
| `locked-ether` | `VaultBase` | **False positive:** every subclass's `withdraw` sends ETH out |

## What Slither did *not* find

- **Nothing High or Medium that is real** in `SettlementToken` or `Escrow`.
- **Nothing on the CEI ordering in `Escrow`.** That's consistent with the
  F-01 / M4 regressions: Escrow writes state before every token call.
- **Not the rotate-back signature revival (F-07).** It's a logic flaw, not a pattern
  Slither detects; the tests caught it.
