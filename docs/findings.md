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
| F-10 | first CI run of the Slither gate | the action's container can't install the pinned Slither 0.11.6 | Slither pinned to 0.11.4 in CI and locally |
| F-11 | integration test INT-10 | a naive nonce manager loses 7 of 8 concurrent transfers | local nonce manager, seeded once, locked |
| F-12 | Anvil probe while building INT-08 | `evm_increaseTime` can't hit an exact second | pin boundaries with `evm_setNextBlockTimestamp` / `evm_mine(ts)` |
| F-13 | Anvil probe while building the harness | a receipt fetched right after sending may not exist yet | always `wait_for_transaction_receipt` |
| F-14 | first run of INT-08 | `eth_estimateGas` ignores the pinned timestamp; a boundary test passed for the wrong reason | `call_at` / `send_at` helpers; the test asserts the block time |
| F-15 | reorg tests IDX-01..03, REC-06 | a naive indexer keeps an abandoned block's events and misses the replacement's | parent-hash check + rollback to the common ancestor; naive run is a strict xfail |
| F-16 | precision probe for IDX-07 | SQLite NUMERIC silently turns integers above 2^63 into REALs | `Uint256` column type: NUMERIC(78,0) on Postgres, TEXT on SQLite |
| F-17 | Anvil probe for the reconciliation pin | Anvil's `finalized` and `safe` tags are block 0 | pin = `latest - confirmations`, read by block hash |
| F-18 | IDX-02, with the tip check disabled | the parent-hash check alone misses a same-height reorg (`anvil_reorg`) | tip check when no new block is due |
| F-19 | TAPI-13 (issue #12) | a nonce manager that never re-syncs stalls every transaction after one refused send | `NonceManager.resync()` after any refusal; gap filler as a safety net |
| F-20 | Anvil probe for TAPI-09 | Anvil refuses underpriced sends and silently *drops* pool transactions a fee spike made underpriced | stuck = "no receipt for N blocks", whatever the cause; re-sign the same nonce |
| F-21 | TAPI-11 | re-signing a transfer after a crash pays the recipient twice | write-ahead attempts; a restart rebroadcasts the stored bytes |
| F-22 | first transfer-API run on Postgres | `time.time_ns()` in an `Integer` column: fine on SQLite, "integer out of range" on Postgres | `BigInteger`; CI keeps running Postgres |
| F-23 | nightly load run (PR 9) | the worker submits the whole backlog before tracking anything; the API accepts without limit; confirmations stall | bounded batch per tick; 503 backpressure; 4.0 → 12.9 confirmed/s |
| F-24 | `profile_worker.py` | 6 of 8 JSON-RPC calls per transfer are bookkeeping reads of the head | one chain snapshot per tick phase; 8.0 → 2.1 calls |
| F-25 | load-run sampler | the indexer's full-balance query grows with the event count: 0.7 → 162 ms over 15k events | reported, not fixed (IDX-D4 revisit; performance-report.md) |
| F-26 | first reading of the load report | two harness metric bugs: start-up catch-up reported as 52-block indexer lag; 503s counted as accepted transfers | warm-up excluded, created transfers counted; both runs re-rendered |

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

**Source:** `contracts/test/fixtures/vault/Vault.sol` (was `src/vulnerable/` until
issue #10), deliberately vulnerable (SEC-D1).

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

## F-10: the Slither gate's first CI run failed before analysing anything

**Found by:** the first run of the `slither` job on PR 5 (run 41238394466):

```
[-] SLITHERVER provided, installing slither-analyzer==0.11.6
ERROR: Ignored the following versions that require a different python version:
       0.11.5 Requires-Python >=3.10; 0.11.6 Requires-Python >=3.10
ERROR: No matching distribution found for slither-analyzer==0.11.6
```

**Cause:** `crytic/slither-action@v0.4.1` installs Slither inside a container whose
Python is older than 3.10. Slither 0.11.5 and later need 3.10. Local runs used
0.11.6 (from `scripts/cloud-setup.sh`), so the triage had been done on a version
CI can't run.

**Fix:** pin **0.11.4**, the newest the container can install, in both CI and
`scripts/cloud-setup.sh`, so local and CI results match exactly. Re-checked locally
with both versions:

| | 0.11.4 | 0.11.6 |
|---|---|---|
| Detectors | 100 | 102 |
| Gated results | 7 | 5 |
| `--fail-high` exit (gate) | 0 | 0 |
| Exit with the vault fixtures in scope (then in `src/vulnerable/`) | 255 | 255 |

The two extra 0.11.4 results are informational notes on OpenZeppelin's inherited
`Pausable` events (triage #12 and #13). The gate verdict is the same on both.

**Lesson:** pin the analyser version *and* run it where CI runs it. A tool version
that only works locally is a gate that has never run.

---

## F-11: a naive nonce manager loses transactions under concurrency (book Ch 1.3)

**Found by:** `tests/integration/test_nonces.py` (INT-10).

**Setup:** 8 workers send `transfer(payee, i + 1)` from the same account, each signing
a raw transaction locally. A barrier makes them all choose a nonce before any of them
sends, which is the same window a busy service hits.

**Naive manager**, `getTransactionCount(addr, "pending")` before each send. One run,
replayed outside pytest:

```
nonces chosen:   [0, 0, 0, 0, 0, 0, 0, 0]
accepted by node: 1   mined ok: 1   payee received: 4 of 36
  7x 'nonce too low'
  1x accepted
```

All eight read the same pending count, so all eight signed nonce 0. The node mined
the first to arrive (here worker 3, sending 4 units) and rejected the other seven.
Which one wins changes from run to run. **7 of 8 payments silently didn't happen**
at the application level: a service that logs "sent" when it signs, or that
swallows the RPC error, would report 36 units paid when 4 were.

**Local manager** (seed once from `pending`, then hand out under a lock): 8 distinct,
consecutive nonces; all 8 mined; the payee received exactly 36; the sender's nonce
is `start + 8`.

**Why the tests assert on state:** the test checks the *payee's balance*, not only
the exceptions. Depending on timing, a node can accept a duplicate, or replace one
pending transaction with another, *without* returning an error. Only the balance
shows that the money didn't move. Stable over 10 consecutive runs.

## F-12: `evm_increaseTime` can't land on an exact second

**Found by:** probing Anvil before writing the deadline tests (INT-08).

`evm_increaseTime(100)` followed by `evm_mine` moved the chain clock by **+102 s**.
The next transaction, sent 2.2 s of wall time later, was mined **+3 s** after that
block, not +1. Anvil adds the offset *on top of* wall-clock time, so the result
depends on how long the test took to get there. A deadline test built on
`evm_increaseTime` alone would be flaky at the boundary, or only "pass" because it
lands somewhere vaguely near the right time.

**Fix:** `Chain.increase_time` is for coarse travel only, and its test allows up to
30 s of drift. Exact seconds use `evm_setNextBlockTimestamp` or `evm_mine(timestamp)`
(see F-14). `evm_setNextBlockTimestamp(t + 50)` landed at exactly +50.

## F-13: the receipt may not exist straight after sending

**Found by:** the same Anvil probe. Calling `get_transaction_receipt(hash)` right
after `send_transaction` raised `TransactionNotFound`, even with automine on: Anvil
mines asynchronously. Every send in the harness goes through
`Chain._wait` → `wait_for_transaction_receipt`. On a real network this race is the
normal case, not an edge case.

## F-14: `eth_estimateGas` ignores the pinned block timestamp

**Found by:** the first run of the deadline tests. 3 tests failed with `TooEarly`.

`evm_setNextBlockTimestamp(deadline)` followed by a normal `transact()` never sent
anything. web3 first calls `eth_estimateGas`, and Anvil simulates that at the
*latest* block's time, not the pinned one, so the refund reverted during estimation
with `TooEarly(deadline)`.

**The worse half: a test passed for the wrong reason.**
`test_refund_one_second_before_deadline_reverts_too_early` was green. It pinned
`deadline - 1` and expected `TooEarly`, and got it, but from estimation at the
*latest* block's time, hours before `deadline - 1`. It would have stayed green with
a refund boundary off by an hour. That's CLAUDE.md's "a revert for the wrong reason
is a failing test", but with the right error name and argument and the wrong
*cause*.

**Fix** (`tests/lib/chain.py`):
- **`call_at(t, fn)`:** `evm_mine(t)`, then `eth_call` against `latest`, so the EVM
  sees exactly `block.timestamp == t`. The test also asserts `chain.now() == t`.
- **`send_at(t, fn)`:** pins the next block to `t` and sends with an **explicit gas
  limit**, so no estimation runs. It then asserts the receipt's block was mined at
  `t`.

Both boundary tests now prove the second they claim.

## F-15: a naive indexer keeps the abandoned block after a reorg (book Ch 3)

**Found by:** `tests/indexing/test_reorg.py` (IDX-01, IDX-02 run against both
implementations; IDX-03 pins the wrong state) and `tests/reconciliation` (REC-06).

**Setup (IDX-01):** fund the payer with 1,000 USDC, index. `evm_snapshot`. Block H:
payer → payee 100. Index it. `evm_revert`. Block H' at the same height: payer →
stranger 7. Mine H+1 on top of H'. Sync again.

**`NaiveIndexer`** (`tests/lib/naive_indexer.py`) is the real indexer with
`check_parent = False`. It resumes at H+1, the next number after its tip, and never
looks at height H again:

```
result:          processed=1, reorgs=0, rolled_back=0
stored hash[H]:  0x1594...5530   (the abandoned block; chain has 0x28c1...2a14)
balances:        payer 900, payee 100       (chain: payer 993, stranger 7)
```

The database describes a fork that no longer exists. The payee's 100 USDC was
never paid, and the stranger's 7 doesn't exist for the indexer. Nothing errors.

**The nastier half (REC-06):** the naive database has the *right* hash for H+1,
because it stored H+1 without checking its parent. A reconciliation that only
compares the pinned block's hash passes. Only the per-holder balances show it:

```
balance[payer]:    chain=993000000 db=900000000
balance[payee]:    chain=0         db=100000000
balance[stranger]: chain=7000000   db=0
```

`totalSupply` still reconciles (1,000 either way), so a supply-only check passes too.

**Fix:** before storing block N, `Indexer` compares its `parentHash` with the stored
hash of N-1. On a mismatch it walks back to the newest height where the stored hash
equals the chain's (the common ancestor), deletes every block above it and its
events in one transaction, and re-indexes. Result after the same sequence:
`processed=2, reorgs=1, rolled_back=1`, balances payer 993 and stranger 7.

**How the suite keeps it proven:** the naive runs are `xfail(strict=True,
raises=AssertionError)`. If the naive indexer ever *passed*, the test could no longer
see a reorg, and the strict xfail turns CI red. IDX-03 asserts the exact wrong state,
so the xfail can't be passing for an unrelated reason. Its data assertions run
before the counters, so the failure is about what was stored.

## F-16: SQLite NUMERIC silently loses integers above 2^63

**Found by:** probing the storage type before writing IDX-07. Now pinned by
`test_sqlite_numeric_column_silently_loses_precision_above_2_pow_63`.

```
value inserted        stored as     read back                        exact?
2^53 + 1              integer       9007199254740993                 yes
2^63 + 1              real          9.223372036854776e+18            no
10^30 + 1             real          1e+30                            no
```

SQLite's NUMERIC affinity keeps a value as INTEGER only if it fits in 64 bits.
Anything bigger becomes an 8-byte float, with no error and no warning. Through
SQLAlchemy's `Numeric(78, 0)`, `2^63 + 1` comes back as `2^63`: off by one unit, the
kind of error a reconciliation catches only if it compares exactly. Token amounts
with 18 decimals pass 2^63 at about 9.2 tokens.

2^53 + 1 survives SQLite but not a float (JSON numbers in JavaScript, pandas,
`float()`), so IDX-07 tests both boundaries, plus 10^30 + 7 and 2^256 - 1.

**Fix:** `indexer.db.Uint256`: NUMERIC(78, 0) on Postgres and TEXT on SQLite, `int` in
Python. It refuses floats, bools, strings, negatives and values ≥ 2^256 at bind
time. All sums run in Python ints (`indexer.queries`), never in SQL, so SQLite and
Postgres give identical answers.

## F-17: Anvil's `finalized` tag is block 0

**Found by:** probing Anvil while choosing the reconciliation pin.
`eth_getBlockByNumber("finalized")` and `("safe")` both returned block 0 at height 3.
A dev chain has no consensus layer and so nothing finalises. Reconciling "at
finalized" there would compare the genesis block, and pass trivially.

**Fix:** the pin is the indexer's confirmed tip, `latest - confirmations` (2 in the
reconciliation suite). Every chain read is an `eth_call` pinned to that block's
**hash**, so the chain side can't silently move to another fork while we compare.
On a real network the pin would be the `finalized` block (IDX-D2).

## F-18: the parent-hash check alone misses a same-height reorg

**Found by:** IDX-02, run against a variant of `Indexer` with the tip check disabled
(a temporary local edit, not committed). Both parametrisations failed:

```
stored blocks {11: 0x0034..., 12: 0xd19b..., 13: 0xa268...}
chain blocks  {11: 0x0dac..., 12: 0xf37f..., 13: 0x4f1e...}
```

`anvil_reorg(3, ...)` replaces the last 3 blocks without changing the height. There
is no new block, so there is no `parentHash` to compare, and a parent-check-only
indexer reports "up to date" over three blocks that no longer exist. It would only
notice when the next block arrives, and on a quiet chain that can be a long time.
The same happens when `evm_revert` makes the chain *shorter* than the stored tip.

**Fix:** when there is no new block to process, `sync()` checks that the stored tip
is still the chain's block at that height (or exists at all). If not, it rolls back
the same way. With the check restored, IDX-02 passes at 0 and 1 confirmations.

## F-19: a nonce manager that never re-syncs stalls after one refused send (issue #12)

**Found by:** `tests/transfers/test_nonce_gaps.py`, TAPI-13. The `CountingNonceManager`
run is a strict xfail.

**Setup:** the send that gets nonce *k* is refused by the node (gas below intrinsic:
`intrinsic gas too low`). Three transfers of 1, 2 and 3 units follow.

**Counting manager** (PR 6's `LocalNonceManager`: seed once, count up, never look
back): the three get *k+1..k+3*. The node accepts all three, without an error, into
its *queued* set, because nonce *k* never arrives. One block later:

```
payee balance: 0 (expected 6)    wallet nonce: k (expected k+3)
eth_getTransactionCount(pending) == k     the three transactions: no receipt, ever
```

The service would report three successful sends. Nothing would ever settle, and
every later transfer would join the queue.

**Fix** (`services/transfer_api/nonces.py`): after any refusal, `resync()` resets the
counter to the node's `pending` count. That count excludes queued transactions, so
it points at the hole, and the next send fills it. After the refusal the three get
*k..k+2* and all are mined (TAPI-13, real manager). The same re-sync recovers "nonce
too low" after someone used the key elsewhere (TAPI-14). Inside the worker, the next
transfer in the same tick takes the refused nonce, so no gap ever forms (TAPI-15).

**Safety net:** a nonce can also be lost with *no* error at all (handed out, then the
process dies). The worker's gap filler finds an outstanding nonce above the chain's
with nothing executable pending, and fills each hole with a 0-value self-transfer
(TAPI-16). TAPI-15 runs both managers. With re-sync, 0 fillers are needed. Without
it, the filler rescues the transfer at the cost of 1 extra transaction.

## F-20: Anvil drops a pending transaction that a fee spike made underpriced

**Found by:** probing Anvil before writing the stuck-transaction test (TAPI-09).

- A send whose `maxFeePerGas` is below the next block's base fee is **refused**:
  `max fee per gas less than block base fee`. Geth would accept it and keep it pending.
- A transaction already in the pool (automine off), sent at 3 gwei, is **evicted**
  when `anvil_setNextBlockBaseFeePerGas(100 gwei)` and a block are mined. `txpool_content`
  is empty, `eth_getTransactionByHash` says not found, and the nonce is unused. No
  error reaches the sender.

So "stuck" can't mean "still pending at a low fee" on every node. On Anvil it means
"gone". A tracker that waits for a receipt, or one that only bumps fees on
transactions it can still see, waits forever.

**Fix:** the worker defines stuck as **no receipt for `stuck_after_blocks`**, whatever
the reason. It re-signs the same nonce at `max(1.25 x old fee, 2 x base fee + tip)`,
which replaces a pending one (geth) and re-sends a dropped one (Anvil). It checks
receipts for *every* attempt, so whichever one is mined counts. TAPI-09 asserts the
drop, the replacement, one payment and one nonce.

## F-21: re-signing after a crash pays the recipient twice

**Found by:** `tests/transfers/test_api_restart.py`, TAPI-11. The `NaiveRestartService`
run is a strict xfail, and `test_naive_restart_re_signs_and_pays_twice` pins the
wrong state.

**Setup:** the worker broadcasts a 12 USDC transfer and dies before recording that it
did. The transaction is mined. The database still says `signed`.

**Naive restart:** "it never got sent, sign it again". The new signature takes the
next nonce, so it is a different, equally valid transaction:

```
payee balance: 24 USDC (requested 12)    wallet nonce: +2    status: confirmed
```

Everything looks healthy: both transactions succeeded, and the transfer is
confirmed. Only the recipient's balance shows the double payment.

**Fix:** write-ahead. The signed raw transaction is stored in `attempts` *before*
broadcast. A restart rebroadcasts **those bytes**, so it can only ever be the same
transaction. If it was already mined, the node answers `nonce too low`, the worker
finds a receipt for the stored hash, and it marks the transfer submitted. TAPI-10 covers
the other crash window (stored but never sent): the restart sends the stored bytes,
the same hash, once.

## F-22: `Integer` holds `time.time_ns()` on SQLite but not on Postgres

**Found by:** the first run of the transfer API suites against Postgres. Every test
that accepted a transfer got HTTP 500, from `psycopg.errors.NumericValueOutOfRange:
integer out of range`. On SQLite all 29 had passed.

`transfers.created_seq` (FIFO order for the worker) stored `time.time_ns()`, about
1.8 x 10^18, in an `Integer` column. SQLite stores any integer up to 64 bits whatever
the declared type. Postgres `INTEGER` is 32-bit. **Fix:** `BigInteger`. This is the
case for running CI on the production database engine, not only the convenient one
(the plan's "SQLite in sessions, Postgres in CI").

The transfer API also met F-13 again: right after a broadcast, Anvil may not have
the receipt yet, so the same `tick()` can't always see it mined. The worker doesn't
wait; the next tick does. Tests use `wait_for_attempts`, which waits on the node.

## F-23: confirmations stall behind an unbounded backlog (load)

**Found by:** the first `nightly` load run (`tests/load/run_load.py`, 40 users, 180 s).
The run failed its correctness gate: after a 120 s drain, 5,153 transfers were still
`submitted`. Evidence: `docs/performance/nightly-before.md`.

The sampler shows the mechanism:

```
t=13 s   accepted 288    submitted 39    confirmed 34
t=65 s   accepted 1704   submitted 253   confirmed 140
t=130 s  accepted 3517   submitted 675   confirmed 140   <- no confirmation in 65 s
```

Two defects together:
- **Head-of-line blocking:** `tick()` submitted *every* accepted transfer before
  tracking any receipt. With thousands queued, one tick took minutes, so receipts
  and confirmations waited for the whole queue.
- **No admission control:** the API accepted 33.5 transfers/s while one hot wallet
  confirmed 4.0/s. Every client's wait grew with the queue, and "accepted" no longer
  meant "will settle".

**Fix (TAPI-D8):**
- `tick()` submits at most 25 transfers, then tracks and confirms (TAPI-18).
- Over 500 unsettled transfers, `POST /transfers` answers 503 with `Retry-After: 1`.
  The Locust client retries with the same key, which idempotency makes safe (TAPI-17).

After the fix the same load **passes**: 2,862 of 2,862 confirmed, 12.9 confirmed/s
(3.2×), backlog capped at 509, drain 17.8 s. The remaining limit is the serial worker
itself (performance-report.md).

## F-24: most of the worker's RPC calls were bookkeeping

**Found by:** `tests/load/profile_worker.py`, counting at web3's transport layer.

```
before: 8.0 JSON-RPC calls per transfer
  eth_blockNumber 5.0   eth_getBlockByNumber 1.0   eth_sendRawTransaction 1.0   eth_getTransactionReceipt 1.0
after:  2.1 per transfer
  eth_sendRawTransaction 1.0   eth_getTransactionReceipt 1.0   eth_getBlockByNumber 0.1
```

Each status row recorded the current block number with its own call, and each
signature read the base fee again. Only the send and the receipt lookup are
essential.

**Fix:** one snapshot of the head (number and base fee) per tick phase. It is
refreshed after any refused send, because a refusal can mean the base fee moved. That
keeps TAPI-15 (fee spike mid-tick) passing. The worker alone went from 20.9 to 27.7
transfers/s.

**A trap on the way:** the first counter patched `provider.make_request` on the
instance and counted **0** calls. web3 7 wraps `make_request` with a request-caching
decorator and calls through it, so the patch never ran. Counting at
`provider._make_request` (the transport) gave the real numbers. A zero is a result to
distrust, not to report.

## F-25: the indexer's full-balance query is O(events)

**Found by:** the load sampler, which times `queries.balances()` every 0.5 s.

0.7 ms at the start, **162 ms** at 15,221 events (before run) and 77 ms at 12,180
(after run). Balances are replayed from events on every read (IDX-D4). That's fine
for a reconciliation at a pinned block. It is not fine as an API read path at this
volume. **Not fixed:** the indexer kept up (lag max 2 blocks), and the trade-off is
recorded in performance-report.md.

## F-26: two metric bugs in the load report itself

**Found by:** reading the first before/after reports side by side.

- **Indexer lag max 52 blocks in *both* runs.** That's suspicious when the indexer
  never fell behind otherwise. The sampler showed it was a single sample at
  t = 0.5 s: the indexer catching up on the ~87 setup blocks, not load.
  - Fix: the SUT syncs the indexer before load starts.
  - Fix: the summary skips samples until the indexer first reaches the head.
  - Corrected: max 2.
- **"Accepted/s" counted every `POST`,** including 503 refusals and replays.
  - Fix: it counts transfers actually created.
  - Corrected: 15.9/s after, not 21.3.

Both runs were re-summarised from their raw files with the corrected code
(`run_load.rerender`). The published numbers come from the same data.
