# Findings

What the test suites found: planted bugs they were proven against, real defects, and
defects in the test harness itself. Each entry gives the evidence (the failing call
sequence or test output) and the outcome.

| ID | Found by | Kind | Status |
|---|---|---|---|
| F-01 | invariants (also unit + fuzz) | planted bug: `Deal memory` in `release` | proven caught; not committed |
| F-02 | invariants, `fail_on_revert` | harness bug: `vm.prank` consumed by a view call | fixed in handler |
| F-03 | invariants, `fail_on_revert` | harness bug: overflow on `uint256.max` seed | fixed in handler |
| F-04 | invariant call summary | harness gap: runs mostly frozen by token controls | fixed by steering |
| F-05 | CI dry run | `forge snapshot --check` breaks on fuzz/invariant/fork entries | snapshot scoped to unit tests |
| F-06 | first executed fork job | RPC cache configured twice, one keyed per commit | toolchain cache off in fork job |
| F-07 | design review + mutant S6 | rotating the arbiter back would revive its old signatures | `setArbiter` consumes the outgoing arbiter's nonce |
| F-08 | issue #3 regression | the old "admin renounces" test now reverts, as intended | test rewritten to the deliberate path |
| F-09 | exploit test SEC-01 | planted re-entrancy drains 10 ETH with a 1 ETH stake | fixed twice (CEI, nonReentrant), regressions proven |

No real defect in `SettlementToken` or `Escrow` was found by the fuzz, invariant or
fork suites in PR 4. All 7 USDC fork tests passed on their first execution in CI.
PR 5 found one real design flaw in its own new code before it shipped (F-07), and
fixed issue #3's flaw (F-08). Each has been re-run at 10,000 fuzz runs and 512 × 256 invariant
calls under the `ci` profile.

---

## F-01: `Deal memory` in `release` lets one deal be paid out repeatedly

**Source:** *Testing the Chain*, Ch 4.3. Planted on a throwaway local branch, never
committed.

**The bug:** a one-word change in `Escrow.release`:

```diff
-        Deal storage deal = _funded(id);
+        Deal memory deal = _funded(id);
```

`deal.state = State.Released` then writes to a memory copy. The transfer to the payee
still happens, but the stored deal stays `Funded`, so it can be released again.

**Shrunk failing sequence (2 calls)**, from `forge test --match-path 'test/invariant/*'`:

```
[FAIL: escrow balance != sum(Funded)] invariant_EscrowBalanceEqualsSumOfFundedDeals
    [Sequence] (original: 28, shrunk: 2)
        calldata=open(uint256,uint256,uint256,uint256) args=[0, 11811, 11482, 10425]
        calldata=release(uint256) args=[3615]
[FAIL: settled deal changed state: 1 != 2] invariant_SettledDealsNeverChangeState
    [Sequence] (original: 28, shrunk: 2)
        (same two calls)
[FAIL: totalReleased != payees received] invariant_GhostTotalsMatchCounterpartyBalances
    [Sequence] (original: 25, shrunk: 6)
        togglePause(); togglePause(); open(0,0,201,0); open(0,0,3522,0);
        release(14166408); resettle(0, true)
[PASS] invariant_TrackedBalancesSumToTotalSupply
```

The sequence shrinks to different arguments on each run, because the fuzz seed is
random. The shape is always the same: one `open`, one `release`.

**Replaying the 2-call sequence** (the handler's actions, called directly):

| After | deal 0 state | escrow balance | sum of Funded deals | payee0 balance |
|---|---|---|---|---|
| `open(0, 11811, 11482, 10425)` | Funded | 11,482 | 11,482 | 0 |
| `release(3615)` | **Funded** (should be Released) | **0** | **11,482** | 11,482 |

So the escrow owes 11,482 base units it no longer holds.

**Impact, replayed with two deals:**
- deal 0: 100 STK, payer0 → payee0
- deal 1: 400 STK, payer1 → payee1

The arbiter calls `release(0)` five times:

| | |
|---|---|
| payee0 received | **500 STK**, five times its deal |
| escrow left | 0 |
| deal 0, deal 1 state | both still Funded |
| `release(1)` | reverts with `ERC20InsufficientBalance(escrow, 0, 400e6)` |

Deal 1's payee is never paid, and its payer can't be refunded either: the escrow is
empty. This is cross-deal theft. Any repeated release by the arbiter, or by anyone
who compromises the arbiter key (see #7), drains other deals.

**Which invariants caught it, and why the fourth didn't:**
- **PROP-07** (escrow balance == sum of Funded): the money left, but the deal still
  claims it.
- **PROP-10** (terminal states never change): the handler saw a successful release,
  but storage says Funded.
- **PROP-08** (ghost released == payees received): a re-settle attempt paid the payee
  a second time.
- **PROP-09** (holders sum to totalSupply) still passes, correctly. The bug moves
  value between accounts but creates none, so a supply-only invariant would miss
  this whole class of bug.

**Also caught by:** 6 unit tests (`deal.state: 0 != 1`, and the second release
reverting with `ERC20InsufficientBalance` instead of `WrongState(Released)`), and the
fuzz property PROP-02 (`deal.state: 0 != 1`). The unit suite catches it because
CLAUDE.md requires asserting state, not only balances: a balance-only unit test of
`release` passes with this bug. What the invariant adds is detection without a test
written for this scenario, and the cross-deal consequence above, which no unit test
here exercises.

---

## F-02: `vm.prank` was consumed by a view call (handler bug)

**Found by:** the first invariant run failed on **call 1** with
`AccessControlUnauthorizedAccount(handler, PAUSER_ROLE)`.

**Cause:**

```solidity
vm.prank(pauser);
if (token.paused()) token.unpause();   // prank applies to paused(), a view
else token.pause();                    // runs as the handler, which has no role
```

`vm.prank` applies to the next external call, and a `view` call counts.

**Fix:** read the state first, then prank.

**Lesson:** `fail_on_revert = true` turned a silent problem into a loud one. With it
off, as it is by default in `foundry.toml`, every `togglePause` would have reverted
quietly. The "pause integration" would then never have run, and the invariants would
have passed against a token that was never paused.

## F-03: overflow on the fuzzer's boundary values (handler bug)

**Found by:** the second run failed with `panic: arithmetic underflow or overflow
(0x11)`. It looked like an invariant failure, but replaying the shrunk sequence put
the panic in the handler's `_pickFunded`.

**Cause:** `(dealSeed + i) % n` overflows when the fuzzer passes
`dealSeed = type(uint256).max`. The fuzzer deliberately favours boundary values.

**Fix:** reduce the seed first, `start = dealSeed % n`, then `(start + i) % n`.

## F-04: the first handler checked invariants against a mostly frozen system

**Found by:** the `afterInvariant` call summary. In a sampled run, 19 of 21 `open`
calls and every `release` were rejected. Pause and delisting toggles left the token
paused, or a participant unlisted, most of the time. Every invariant passed, but
mostly over states where nothing could move.

**Fix:**
- Weight the selectors (open ×3, release ×2, refund ×2).
- Add a `restoreControls` action (×2) that unpauses and re-lists everyone.
- Have `refund` warp to the deadline half the time.

A sampled run after the fix: 9 opens, 4 releases and 1 refund succeeded, with
freezes still interleaved.

**Lesson:** a green invariant suite is only evidence if the handler reaches the
states that matter. Always look at the call summary, not just the PASS line.

## F-05: `forge snapshot --check` can't include fuzz, invariant or fork tests

**Found by:** running CI's unfiltered snapshot check against this branch. It fails
with `No matching snapshot entry found for "EscrowFuzzTest::testFuzz_…"`, and with
entries added, fuzz gas is a mean over random inputs and changes with the run count
(256 locally, 10,000 under `ci`).

**Fix:** generate and check the snapshot with `--match-path 'test/unit/*'`. Recorded
as PROP-D2 in `docs/decisions.md`.

## F-06: the fork job cached Foundry's RPC state twice

**Found by:** the first fork job that actually executed (run 41232780712, attempt 2,
after the `MAINNET_RPC_URL` repository secret was added). All 7 fork tests passed:

```
Ran 7 tests for test/fork/Escrow.usdc.fork.t.sol:EscrowUsdcForkTest
[PASS] test_Fork_RefundsAmountAboveUint64() (gas: 383764)
[PASS] test_Fork_ReleasesOneBaseUnit() (gas: 310553)
[PASS] test_Fork_ReleasesSixDecimalAmountExactly() (gas: 333976)
[PASS] test_Fork_RevertWhen_EscrowBlacklistedFreezesPayouts() (gas: 360699)
[PASS] test_Fork_RevertWhen_PayeeBlacklistedBeforeRelease() (gas: 418556)
[PASS] test_Fork_RevertWhen_PayerBlacklistedAtOpen() (gas: 201827)
[PASS] test_Fork_UsdcHasSixDecimals() (gas: 29829)
Suite result: ok. 7 passed; 0 failed; 0 skipped
```

The log showed two caches of the same `~/.foundry/cache/rpc` directory:

| Cache | Key | Problem |
|---|---|---|
| `foundry-toolchain@v1` (`cache: true` by default) | `linux-foundry-chain-fork-fork-<merge sha>` | a new entry on every commit |
| the workflow's own `actions/cache` step | `foundry-rpc-mainnet-21000000` | none: tied to the pinned block, valid across commits |

The two restores race for one directory, and the per-commit entries pile up against
the repository's cache quota.

**Fix:** `cache: false` on the toolchain step in the fork job. The pinned-block cache
is now the only one, and its key is bumped together with `FORK_BLOCK`.

**Secret handling, checked in the same log:** every occurrence of `MAINNET_RPC_URL`
is masked as `***`, and the only URLs in the whole job log are `github.com` and
`github.blog`. The RPC host does not appear, not even in forge output.

**Also learned:** before the secret was a *repository* secret, the gate job logged
`MAINNET_RPC_URL: ` (empty) on both a re-run and a fresh run, and the fork job was
skipped. An empty value in the gate's `env:` block means Actions can't see the
secret. A secret that exists shows as `***`.

---

## F-07: rotating the arbiter back would revive its old signatures

**Found by:** design review while adding `releaseWithSig` to a rotatable arbiter.
Proven with mutant S6 in PR 5.

**The flaw:** nonces are kept per arbiter address. A signature the arbiter issued
but nobody submitted stays valid for as long as its nonce is current. Rotating the
arbiter out hides that signature, because the signer is no longer `arbiter`.
Rotating the *same* key back in makes it valid again. That's realistic: a key
suspected compromised is rotated out, and then rotated back once cleared. Any
signature an attacker collected in between would come back to life.

**Fix:** `setArbiter` calls `_useNonce(previous)`, so every outstanding signature of
the outgoing arbiter is void for good.

**Evidence:** `test_RevertWhen_SignatureFromRotatedOutArbiter` rotates away and
back, then submits the old signature: `InvalidSignature`. Removing the
`_useNonce(previous)` line (mutant S6) makes exactly that test fail. Slither does not
detect this (`docs/slither-triage.md`).

## F-08: the test that documented the admin-renounce flaw now fails, as intended

**Found by:** moving `SettlementToken` to `AccessControlDefaultAdminRules` (issue #3).

PR 2's `test_RevertWhen_AdminActsAfterRenouncing` showed the flaw: an admin's
one-call `renounceRole` left the token with no admin. With PR 5 that call reverts:

```
[FAIL: AccessControlEnforcedDefaultAdminDelay(0)] test_RevertWhen_AdminActsAfterRenouncing()
```

The schedule in the error is 0 because no transfer was scheduled. The test now uses
the only remaining path: `beginDefaultAdminTransfer(address(0))`, then wait past the
2-day delay, then renounce. It still proves compliance keeps working after
governance is given up. The one-call renounce is tested on its own in
`test_RevertWhen_AdminRenouncesWithoutScheduledTransfer`.

## F-09: re-entrancy drain (planted, book Ch 9.1)

**Source:** `contracts/src/vulnerable/Vault.sol`, deliberately vulnerable (SEC-D1).

**Exploit** (`test_Exploit_ReentrancyDrainsVulnerableVault`):
1. Five depositors put in 2 ETH each.
2. The attacker deposits 1 ETH and calls `withdraw`.
3. The vault sends ETH *before* zeroing the balance, so `receive()` re-enters
   `withdraw` and is paid the same 1 ETH again.
4. This repeats until the vault is empty: 10 re-entries, and the attacker leaves
   with 11 ETH.

Afterwards the ledger still shows each depositor owed 2 ETH, and an honest
`withdraw` reverts with `TransferFailed`: the vault is insolvent.

**Fixes, each proven by the same attack:**

| Fix | Effect of the attack | Mutant that undoes the fix |
|---|---|---|
| `CeiVault` (checks-effects-interactions) | the re-entrant call hits `NothingToWithdraw`; the whole attack reverts | V2 (zeroing after the call) fails the CEI regression |
| `GuardedVault` (`nonReentrant`, vulnerable order kept) | the inner call reverts with `ReentrancyGuardReentrantCall` | V1 (no modifier) fails 2 regressions |

Both fixed vaults still pay every honest depositor in full (SEC-04).
