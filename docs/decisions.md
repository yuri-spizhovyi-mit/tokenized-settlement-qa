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
`timestamp` detector and forge-lint's `block-timestamp` both flag this comparison.
It is intended: the lint is suppressed at that line with a reference to this
entry, and the Slither result goes in the PR 5 triage log.

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
- A deadline in the past: the deal is refundable immediately, which only affects the
  payer's own funds.
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

## Open

- Freeze and forced transfer for `SettlementToken` are out of scope. The entry is
  tracked in issue #4.
