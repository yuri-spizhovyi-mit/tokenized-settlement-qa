# Decisions

Behaviour choices the specification left open, and why they were made. Each entry
names the tests that pin it, so a change of mind shows up as a red test, not a
silent drift.

---

## ESC-D1: Refund has no caller restriction

**Status:** Accepted (PR 3)

**Decision:** Once `block.timestamp >= deadline`, anyone may call `Escrow.refund(id)`.
The funds always go to the deal's recorded `payer`, never to the caller.

**Why:**
- A refund only returns the payer's own money to them, so the caller has nothing
  to gain and the payer nothing to lose.
- If only the payer could refund, a payer who lost their key, or a contract payer
  with no refund call wired in, would leave funds locked forever. An open call lets
  a keeper, the payee or an operator unstick it.
- It matches the escrow in *Testing the Chain*, Ch 4.1 ("anyone refunds the payer
  after the deadline").

**Accepted risk:** A third party chooses *when*, after the deadline, the refund
happens. That only matters if the arbiter was about to release. The arbiter can
release at any time before the deadline, and after it the first valid call wins
(see ESC-D3).

**Tests:** `test_Refund_AnyCallerRefundsToPayer`,
`test_Refund_ArbiterCanRefundAfterDeadline`,
`test_RevertWhen_StrangerRefundsBeforeDeadline`.

---

## ESC-D2: The deadline second is refundable (`>=`, not `>`)

**Status:** Accepted (PR 3)

**Decision:** Refund succeeds when `block.timestamp >= deadline`. At `deadline - 1`
it reverts with `TooEarly(deadline)`.

**Why:** "The deadline" reads as the moment the payer's right to a refund begins,
so the deadline second itself belongs to the payer. It also keeps the error's
argument meaningful: `TooEarly(deadline)` tells a client the exact first timestamp
at which a retry will succeed.

**Timestamp manipulation:** block producers can skew `block.timestamp` by a few
seconds. Escrow deadlines are measured in days, so this doesn't matter. Slither's
`timestamp` detector and forge-lint's `block-timestamp` both flag this comparison
and the one in `open` (ESC-D6). Both are intended: the lint is suppressed at each
line with a reference to this file, and the Slither results go in the PR 5 triage
log.

**Tests:** `test_RevertWhen_RefundOneSecondBeforeDeadline`,
`test_Refund_SucceedsExactlyAtDeadline`.

---

## ESC-D3: The arbiter may release after the deadline while the deal is Funded

**Status:** Accepted (PR 3)

**Decision:** `release` has no deadline check. Past the deadline, release and refund
are both valid, and whichever lands first settles the deal. The other then
reverts with `WrongState`.

**Why:** The deadline protects the payer from an arbiter who never acts. It isn't
meant to cancel a late but valid release. Adding a deadline check to `release`
would let a payer race the arbiter's transaction.

**Tests:** `test_Release_SucceedsAfterDeadlineIfStillFunded`,
`test_RevertWhen_ReleaseAfterRefund`, `test_RevertWhen_RefundAfterRelease`.

---

## ESC-D4: Reject zero amounts and unpayable payees at `open`

**Status:** Accepted (PR 3)

**Decision:** `open` reverts with:
- `ZeroAmount()` when `amount == 0`
- `InvalidPayee(payee)` when the payee is `address(0)` or the escrow itself

**Why:**
- **Zero amount:** a zero-value deal moves no money, but it would still emit
  `Opened` and later `Released`. That's a false settlement record for the indexer
  and the reconciliation suite (PRs 6 and 7).
- **Zero payee:** the token refuses transfers to `address(0)`, so `release` could
  never succeed. Every such deal would be a guaranteed refund wearing a deal's
  clothes.
- **Escrow as payee:** found by testing. With no check, `release` "pays" the escrow,
  marks the deal `Released`, and the funds stay in the contract with no path out:
  `refund` then reverts with `WrongState(Released)`. Rejecting it at `open` is the
  only safe place.

**Not rejected, on purpose:**
- `payee == payer`: odd but harmless; it acts as a time-locked self-transfer.
- A payee that isn't allowlisted: the escrow only knows `IERC20` and can't ask the
  token. The token blocks the payout, and the funds stay refundable (see ESC-D5).

**Tests:** `test_RevertWhen_OpenWithZeroAmount`, `test_RevertWhen_OpenWithZeroPayee`,
`test_RevertWhen_OpenWithEscrowAsPayee`.

---

## ESC-D5: The escrow defers to the token's allowlist and pause

**Status:** Accepted (PR 3, closes issue #5)

**Decision:** The escrow has no allowlist or pause of its own. `SettlementToken`
enforces both on every transfer, and a refused transfer reverts the whole escrow
call. Consequences:
- **The escrow contract must be allowlisted.** It is the spender and the recipient
  on `open`, and the sender on payouts. If it isn't, `open` reverts with
  `AccountNotAllowlisted(escrow)`.
- **Payee delisted:** `release` reverts. The deal stays `Funded` and the payer can
  refund after the deadline.
- **Escrow delisted mid-deal:** both `release` and `refund` revert. The funds are
  frozen in escrow until compliance re-lists it. That's intended: delisting the
  custodian is a compliance freeze.
- **Token paused:** `open`, `release` and `refund` all revert, and deals stay `Funded`.
  After unpause they settle normally.

**Tests:** `contracts/test/unit/Escrow.TokenControls.t.sol` (all of it).

---

## ESC-D6: `open` rejects a deadline that is not in the future

**Status:** Accepted (PR 3, review change)

**Decision:** `open` reverts with `DeadlineInPast(deadline)` when
`deadline <= block.timestamp`. The earliest valid deadline is `block.timestamp + 1`.

**Why reject rather than allow:**
- A deadline at or before now makes the deal refundable in the same block it opens
  (ESC-D2: refund needs `block.timestamp >= deadline`). The arbiter gets no window
  at all, so the "deal" is a round trip with extra steps and false `Opened` /
  `Refunded` records for the indexer.
- In practice it's almost always a client bug: a timestamp in the wrong unit, a
  stale quote, or a zero default. Failing loudly at `open` is
  cheaper than reconciling a phantom deal later.
- It matches ESC-D4: `open` rejects deals that can't do what a deal is for.

**Why `deadline == now` is rejected too:** the boundary follows from ESC-D2. Refund
is allowed *at* the deadline, so a deadline equal to now is already refundable.
Allowing it would make `now` and `now - 1` behave identically from the payer's
side, while being treated differently at `open`.

**Not enforced:** a minimum duration (e.g. one hour). One second is enough to stop
the same-block refund, and a business minimum belongs to the caller.

**Tests:** `test_RevertWhen_OpenWithDeadlineEqualToNow`,
`test_RevertWhen_OpenWithDeadlineOneSecondAgo`, `test_Open_AcceptsDeadlineOneSecondAhead`.
The `<=` → `<` mutant is caught only by the `== now` test, which is why that case
has its own test.

---

## PROP-D1: Fork tests assert USDC's revert strings

**Status:** Accepted (PR 4)

**Decision:** The USDC fork tests assert
`vm.expectRevert(bytes("Blacklistable: account is blacklisted"))`.

**Why:** CLAUDE.md requires exact custom errors, but USDC (FiatTokenV2_2) reverts
with `require` strings, and its error format belongs to Circle. Asserting the exact
string is the strictest check available. A bare `vm.expectRevert()` would still be
wrong, because a revert for another reason, such as a pause or an allowance, must
fail the test.

**Tests:** `contracts/test/fork/Escrow.usdc.fork.t.sol`.

---

## PROP-D2: The gas snapshot covers unit tests only

**Status:** Accepted (PR 4)

**Decision:** `.gas-snapshot` is generated and checked with
`--match-path 'test/unit/*'`.

**Why:** fuzz gas is a mean over random inputs and depends on the run count (256
locally, 10,000 under `ci`). Invariant entries report calls and reverts, not gas.
Fork tests are skipped without an RPC URL. None of them can be pinned, and including
them made `forge snapshot --check` fail (finding F-05). Unit tests have fixed inputs,
so their gas is a real regression signal.

---

## SEC-D1: Deliberately vulnerable code lives in `test/fixtures/` and is never deployable

**Status:** Accepted (PR 5); amended by issue #10 (moved out of `src/vulnerable/`)

**Decision:** `VulnerableVault` and its fixed versions (`CeiVault`, `GuardedVault`)
live in `contracts/test/fixtures/vault/`. A banner marks them as test fixtures.
`contracts/src/` holds only `SettlementToken` and `Escrow`. The Slither gate excludes
`test/` as a whole, so it has no special case for the fixtures. Their findings are
still triaged in `docs/slither-triage.md`, with a documented command that reproduces
them, including the intended High.

**Why:** they have to compile as real contracts (plain `.sol`, not `.t.sol`), so the
exploit runs against real bytecode, and forge-lint and Slither can still analyse
them. They also have to be impossible to mistake for product code. Under `src/`,
anyone reading the product saw exploit fixtures next to it, any tooling that globs
`src/` would pick them up, and the gate needed a path exception to stay green.

---

## SEC-D2: Two-day admin delay on both SettlementToken and Escrow

**Status:** Accepted (PR 5, issue #3)

**Decision:** both contracts use OpenZeppelin `AccessControlDefaultAdminRules` with
`ADMIN_DELAY = 2 days`:
- `DEFAULT_ADMIN_ROLE` has exactly one holder.
- It can't be granted, revoked or renounced in a single call.
- A handover is `beginDefaultAdminTransfer` → wait `> 2 days` →
  `acceptDefaultAdminTransfer` by the new admin. OZ requires
  `block.timestamp > schedule`, so accepting at the schedule itself reverts.

**Why:**
- **Two days** is long enough for monitoring, or another key holder, to notice a
  hostile or mistaken handover and cancel it. It's short enough for a planned
  rotation within a working week.
- **One-call renounce is gone**, which closes issue #3: it used to leave the token
  with no admin.

**Changed behaviour:** a zero admin in either constructor now reverts with OZ's
`AccessControlInvalidDefaultAdmin(address(0))`. `SettlementToken.InvalidAdmin` was
removed, because the base constructor runs first and the old check was unreachable.

**Tests:** `contracts/test/unit/SettlementToken.AdminRules.t.sol`,
`test_RevertWhen_AdminActsAfterRenouncing`, `test_RevertWhen_DeployedWithZeroAdmin`.

---

## SEC-D3: Signed release design

**Status:** Accepted (PR 5)

**Decision:**
- **What is signed:** `releaseWithSig(id, deadline, signature)` takes an EIP-712
  `Release(uint256 id,uint256 nonce,uint256 deadline)` signed by the current arbiter.
  The domain is name `"Escrow"`, version `"1"`, the chain id, and this contract.
- **Who submits:** anyone. Funds only ever go to the deal's payee.
- **Check order:** deadline, then signature, then the deal.
- **Deadline:** inclusive. A signature works at `deadline` and fails at
  `deadline + 1` with `SignatureExpired(deadline)`, the same convention as
  ERC-2612 `permit`.
- **Nonces:** keyed by arbiter address (OZ `Nonces`) and consumed on every successful
  signed release. **`setArbiter` also consumes the outgoing arbiter's nonce** (see F-07).
- **Malleability:** `ECDSA.tryRecover` rejects high-s and malformed signatures, and
  any recover error maps to `InvalidSignature`.

**Why the nonce, when a deal can only be released once anyway:** the nonce rejects
a replay *at the signature check*, before the deal is read. So a replayed signature
fails with `InvalidSignature`, not a state-dependent error, and the protection still
holds if a future signed action isn't one-shot.

**Tests:** `contracts/test/security/Escrow.signedRelease.t.sol` (SEC-10 to SEC-16).

---

## SEC-D4: The arbiter is a rotatable single address, not a role

**Status:** Accepted (PR 5, issue #7)

**Decision:** the escrow has one `arbiter` address, which the escrow's
`DEFAULT_ADMIN_ROLE` can change with `setArbiter`. The change emits
`ArbiterChanged(previous, new)` and rejects the zero address. Rotation takes effect
in the same block, and deals in flight are untouched.

**Why not an `ARBITER_ROLE`:** a role can have many holders, so several parties
could release the same deal. Escrow semantics need exactly one arbiter at a time.
The escrow admin gets no release power of its own: governance and settlement stay
separate duties, as on the token (TOK-04).

**Tests:** `contracts/test/unit/Escrow.Arbiter.t.sol` (SEC-07 to SEC-09).

---

## TOK-D1: Account freeze and forced transfer are out of scope

**Status:** Accepted (PR 6, closes #4)

**Decision:** `SettlementToken` has no account-level freeze separate from the
allowlist, and no forced transfer (seizure). There's also no `burnFrom` by the
issuer: `burn` only ever burns the minter's own balance (TOK-02).

**What covers the need today:** delisting. `setAllowlisted(account, false)` by
`ALLOWLIST_ROLE` blocks every movement in or out of the account: it can't send,
receive, or have an allowance spent (TOK-05, TOK-06). That's a full freeze, applied
in the same block, and the balance stays exactly where it is. The tests assert
"delisting must not seize funds" (`test_RevertWhen_SenderDelistedBeforeTransfer`).

**Why out of scope:**
- **The project's scope is the settlement flow.** Seizure is a different product:
  it needs a legal basis, a process, and evidence trails that this repo can't model
  honestly.
- **A seizure power is the largest trust assumption a token can carry.** It means
  any holder's balance can move without their consent. Adding it "for completeness"
  would weaken every other guarantee the tests prove (TOK-05: only allowlisted
  parties move value).
- **Delisting is reversible and leaves balances intact; seizure isn't and doesn't.**
  Shipping the reversible control first is the conservative order.

**What a regulated token would need to add it safely:**

| Element | Requirement |
|---|---|
| Separate roles | `FREEZER_ROLE` for account-level freeze, distinct from `ALLOWLIST_ROLE` (onboarding is not enforcement), and `ENFORCER_ROLE` for forced transfer, held by a different multisig than the freezer, so no single key can both freeze and seize. Both administered by `DEFAULT_ADMIN_ROLE`, with its two-day handover (SEC-D2). |
| Freeze semantics | `frozen[account]` checked in `_update` next to the allowlist. A frozen account keeps its listing, so unfreezing restores it without re-onboarding. Event `AccountFrozen(account, reason)`. |
| Forced transfer | `forceTransfer(from, to, amount, bytes32 caseRef)`, which bypasses the sender's consent but **not** the recipient's allowlisting. Event `ForcedTransfer(from, to, amount, caseRef)`, with `caseRef` linking to the off-chain legal order. Optionally time-locked so the holder can contest. |
| Tests | A role matrix row per new function; "seizure never changes `totalSupply`" as an invariant; "a frozen account can still receive a forced return"; event fields asserted for audit reconstruction; and freeze + pause + delist interaction tests. |

**Who would hold the roles:**
- **`FREEZER_ROLE`:** the compliance operations team. Day-to-day sanctions hits;
  fast and reversible.
- **`ENFORCER_ROLE`:** legal or the issuer's board multisig. Rare, slow, and
  irreversible. It should never be the same keys as `FREEZER_ROLE` or
  `MINTER_ROLE`.
- **`DEFAULT_ADMIN_ROLE`:** governance, which appoints both and can't exercise
  either.

**Revisit when:** a milestone needs court-ordered recovery, or the token targets a
regime that mandates it, such as tokenised deposits under a specific regulator.

**Tests today:** `SettlementToken.Allowlist.t.sol` (delisting freezes and doesn't
seize).

---

## INT-D1: Integration tests isolate with evm_snapshot / evm_revert, not redeploys

**Status:** Accepted (PR 6)

**Decision:** One Anvil node and one deployment per pytest session. An autouse
fixture takes `evm_snapshot` before each test and `evm_revert`s after it. Balances,
deals, roles and account nonces all reset (INT-11 asserts deals and nonces). Tests
compute times from the current block, never from a remembered value, so they don't
depend on whether the clock rewinds.

**Why:** a redeploy per test would make the suite several times slower, and it
would hide state-leak bugs behind fresh contracts. Snapshots give every test the
same starting state at a fraction of the cost. Anvil snapshot ids are single-use,
so each test takes its own.

---

## INT-D2: Exact-second time travel uses mined or pinned timestamps, never estimation

**Status:** Accepted (PR 6)

**Decision:**
- **Coarse travel** ("a day later") uses `evm_increaseTime` + `evm_mine`.
- **Reverts at second T:** `Chain.call_at(T)`, which mines a block at T and calls
  against it.
- **Successes at second T:** `Chain.send_at(T)`, which pins the next block and sends
  with explicit gas.

Every boundary test asserts the timestamp it ran at.

**Why:** `evm_increaseTime` drifts with wall-clock time (F-12), and `eth_estimateGas`
ignores a pinned timestamp (F-14). Either one makes a boundary test test something
other than its boundary.

---

## INT-D3: web3.py pinned at 7.16.0, the newest 7.x

**Status:** Accepted (PR 6)

**Decision:** `requirements.txt` pins `web3==7.16.0`, plus the libraries the
assertions depend on: `eth-account`, `eth-abi`, `pytest`, `pytest-timeout` and
`allure-pytest`. All are exact pins.

**Why:** the milestone specifies web3.py 7.x. 8.0.0 exists, and moving to it is a
separate change with its own migration (exception classes, middleware API). Exact
pins keep CI and local runs on identical versions, the same reasoning as Foundry
(CLAUDE.md) and Slither (F-10).

---

## IDX-D1: Reorg detection uses the parent hash *and* a tip check

**Status:** Accepted (PR 7)

**Decision:** before storing block N, the indexer requires `parentHash(N)` to equal
the stored hash of N-1. When no new block is due, it requires the stored tip to be
the chain's block at that height. On either mismatch it walks back to the common
ancestor, deletes everything above it in one transaction, and re-indexes. If even
`start_block` is no longer canonical it raises `ReorgTooDeep` and stores nothing.

**Why:** the parent check is the standard mechanism, but it needs a new block to
compare against. A same-height or shortening reorg has none (F-18). Raising on a
reorg below `start_block` is deliberate: the indexer can't know the state before its
first block, and rebuilding from a guess would be worse than stopping.

**Tests:** IDX-01 (parent check), IDX-02 (tip check, at 0 and 1 confirmations),
IDX-03 (the naive failure), IDX-09 (`ReorgTooDeep`).

---

## IDX-D2: Confirmation depth, and the reconciliation pin

**Status:** Accepted (PR 7)

**Decision:** the indexer only processes blocks up to `latest - confirmations`. The
CLI defaults to 2; tests set it explicitly (0 unless the test is about depth). The
reconciliation suite pins its comparison to the indexer's confirmed tip and reads
the chain by that block's **hash** (EIP-1898).

**Why:** a reorg shallower than the depth never reaches the database (IDX-08). On
Anvil `finalized` is block 0 (F-17), so it can't be the pin there. On a real network
the pin should be the `finalized` block and the depth should cover the network's
practical reorg depth. Reading by hash means the chain side and the database side
describe the same block, even while new blocks arrive (REC-04).

**Tests:** IDX-08, REC-01..REC-04.

---

## IDX-D3: Amounts are exact integers: NUMERIC(78,0) on Postgres, TEXT on SQLite

**Status:** Accepted (PR 7)

**Decision:** every amount column (`value`, `amount`, `deal_id`, `deadline`) uses
`indexer.db.Uint256`. It is NUMERIC(78, 0) on Postgres (78 digits hold any uint256)
and TEXT on SQLite, and always a Python `int`. Sums are computed in Python from those
ints, not with SQL `SUM`.

**Why:** SQLite NUMERIC loses precision above 2^63 without an error (F-16). TEXT can't
be summed in SQL, so the sums move to Python. They then behave identically on both
databases, and the reconciliation compares like with like.

**Tests:** IDX-07 (2^53+1, 2^63+1, 10^30+7, 2^256-1, uint128 max, the negative control,
and bind-time rejection of non-integers).

---

## IDX-D4: Balances and deal states are derived from events on read, not stored

**Status:** Accepted (PR 7)

**Decision:** the database stores blocks and raw events only. `indexer.queries`
replays events in `(block_number, log_index)` order to compute balances, supply and
deal states, as of any block.

**Why:** a rollback is then only "delete rows above the ancestor". With materialised
balance tables, every rollback would also have to reverse-apply the abandoned
events, which is a second place to get wrong. Replaying is O(events) per read. That's
fine at this scale; the load milestone (PR 9) measures it and can add a cached
projection that rebuilds from the ancestor.

**Tests:** IDX-06 (replay order), REC-01..REC-03.

---

## IDX-D5: One database transaction per block; logs fetched by block hash

**Status:** Accepted (PR 7)

**Decision:** a block row and all its events are written in one transaction, and the
logs are requested with `eth_getLogs({"blockHash": ...})` (EIP-234), not by number.

**Why:** a crash then leaves a whole block or none of it, so a restart resumes at
`tip + 1` with nothing to repair (IDX-05). Fetching by hash means a block replaced
between `get_block` and `get_logs` makes the node answer "unknown block" (checked on
Anvil), instead of returning the new block's logs under the old block's hash.

**Tests:** IDX-04, IDX-05.

---

## IDX-D6: The naive indexer is a test fixture in `tests/lib/`

**Status:** Accepted (PR 7)

**Decision:** `NaiveIndexer` lives in `tests/lib/naive_indexer.py`, not in
`services/indexer/`. It subclasses the real indexer and flips one flag.

**Why:** deliberately broken code belongs with the tests that use it, not with code
that could be deployed. This is the same rule SEC-D1 applies to the vulnerable vault (issue #10).
Being a one-flag subclass, it can't drift from the real indexer in any other way, so
the reorg tests compare exactly one behaviour.

---

## Open

- None. Issue #4 is recorded above as TOK-D1.
