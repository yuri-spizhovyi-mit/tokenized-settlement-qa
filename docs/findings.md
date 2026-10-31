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

No real defect in `SettlementToken` or `Escrow` was found by the fuzz or invariant
suites in PR 4. Each has been re-run at 10,000 fuzz runs and 512 × 256 invariant
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
