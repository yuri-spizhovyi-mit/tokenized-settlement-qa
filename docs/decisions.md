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

## SEC-D1: Deliberately vulnerable code lives in `src/vulnerable/` and is never deployable

**Status:** Accepted (PR 5)

**Decision:** `VulnerableVault` and its fixed versions (`CeiVault`, `GuardedVault`)
live in `contracts/src/vulnerable/`. A banner marks them as test fixtures. No deploy
script references them, and the Slither CI gate excludes the folder by path. Their
findings are still triaged in `docs/slither-triage.md`.

**Why:** they have to compile as real contracts, so the exploit runs against real
bytecode (test-only `.t.sol` files would hide them from Slither and forge-lint
entirely). They also have to be impossible to mistake for product code.

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

## Open

- Freeze and forced transfer for `SettlementToken` are out of scope. The entry is
  tracked in issue #4.
