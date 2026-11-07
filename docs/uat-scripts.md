# User acceptance test scripts

Scripts a business user (operations, compliance, finance) runs before a release, on a
local deployment, with the transfer page and a wallet. Each script names the automated
test that covers the same path. UAT confirms that the flow makes sense to a person.
It doesn't replace the automated test, and the automated test doesn't replace it.

**Setup** (the tester's environment, about 10 minutes): Anvil, the contracts deployed
from `contracts/out/`, the tester's account allowlisted and funded with 1,000,000 USDC,
and `ui/` served locally:
`python -m http.server -d ui` and open
`index.html?token=<address>&chainId=31337`.

Record for each script: tester, date, build (commit), **Pass / Fail**, and for a
failure the step, what was seen, and a screenshot. A failure becomes a finding.

---

### UAT-01: Send a normal transfer

*Automated twin: UI-02.*

| Step | Action | Expected result |
|---|---|---|
| 1 | Click **Connect wallet**; approve in the wallet | Your account and a balance of **1,000,000** are shown |
| 2 | Enter a colleague's allowlisted address and `125.5` | No message yet |
| 3 | Click **Send**; approve in the wallet | "Pending: 0x…" then **"Confirmed in block N: sent 125.5 to 0x…"** |
| 4 | Look at the balance | **999,874.5**; the amount field is empty |
| 5 | Ask the colleague to check their balance | It increased by exactly **125.5** |

### UAT-02: Change your mind in the wallet

*Automated twin: UI-03.*

| Step | Action | Expected result |
|---|---|---|
| 1 | Enter a colleague's address and `40`; click **Send** | The wallet asks you to confirm |
| 2 | Click **Reject** in the wallet | "**You rejected the transaction in your wallet. Nothing was sent.**" |
| 3 | Check the form | The address and `40` are still filled in; **Send** is clickable |
| 4 | Check your balance and the wallet's activity | Unchanged; no transaction listed |
| 5 | Click **Send** again and approve | Confirmed; the colleague receives exactly 40 |

### UAT-03: Send a large amount

*Automated twin: UI-04.*

| Step | Action | Expected result |
|---|---|---|
| 1 | Enter a colleague's address and `25000` | |
| 2 | Click **Send** | A **"Large transfer"** panel shows **25,000** and the full recipient address; Send is greyed out |
| 3 | Compare every digit of the amount and address with your instruction | They match exactly |
| 4 | Click **Cancel** | "Cancelled. Nothing was sent."; the balance is unchanged |
| 5 | Click **Send**, then **Yes, send it**, and approve in the wallet | Confirmed; the colleague receives exactly 25,000 |
| 6 | Repeat with `9999.999999` | No large-transfer panel (below the 10,000 threshold) |

### UAT-04: A recipient compliance hasn't approved

*Automated twin: UI-06.*

| Step | Action | Expected result |
|---|---|---|
| 1 | Enter an address that is **not** on the allowlist, amount `1`; click **Send** | "**Account 0x… is not on the allowlist.**" |
| 2 | Check the wallet | It was **not** asked to sign anything |

### UAT-05: Mistyped amounts

*Automated twin: UI-05.*

| Step | Action | Expected result |
|---|---|---|
| 1 | Try `1,000`, `1e6`, `-5`, `abc` | "Enter an amount like 125 or 125.50" each time |
| 2 | Try `0` | "Amount must be greater than zero" |
| 3 | Try `1.0000001` | "At most 6 decimal places" |
| 4 | Try more than your balance | "Insufficient balance: you have …" |
| 5 | Check the wallet after each | Never asked to sign |

### UAT-06: Wrong network

*Automated twin: UI-07.*

| Step | Action | Expected result |
|---|---|---|
| 1 | Switch the wallet to Ethereum mainnet; reload; connect | "**Wrong network:** your wallet is on chain 1, this page needs chain 31337." |
| 2 | Look for the transfer form | Not shown: nothing can be sent |

### UAT-07: Escrow deal released by the arbiter (operations)

*Automated twins: INT-02, INT-03.* Run with the integration harness or a script
console, as the payer and then the arbiter.

| Step | Action | Expected result |
|---|---|---|
| 1 | The payer opens a deal for 250 USDC to a payee, deadline in 7 days | The deal shows **Funded**; the escrow holds 250; the payer's balance is down 250 |
| 2 | The arbiter releases the deal | The deal shows **Released**; the payee receives exactly 250; the escrow holds 0 |
| 3 | The arbiter tries to release it again | Refused: the deal is already settled |

### UAT-08: Escrow refund after the deadline (operations)

*Automated twins: INT-04, INT-08.*

| Step | Action | Expected result |
|---|---|---|
| 1 | Open a deal for 75 USDC, deadline in 1 day | Funded |
| 2 | One second before the deadline, anyone tries to refund | Refused: too early |
| 3 | At the deadline, a third party refunds | The **payer** (not the third party) receives exactly 75; the deal shows **Refunded** |

### UAT-09: Month-end reconciliation (finance)

*Automated twins: REC-01..REC-03.*

| Step | Action | Expected result |
|---|---|---|
| 1 | Run the indexer with 2 confirmations until it is caught up | Its tip is 2 blocks behind the chain head |
| 2 | Run the reconciliation at the indexer's tip | **No mismatches**: every holder, the total supply, and the escrow against open deals |
| 3 | Note the block number and hash on the month-end report | The report is reproducible at that exact block |

---

## Sign-off

| Release | Scripts run | Pass | Fail | Findings raised | Signed off by | Date |
|---|---|---|---|---|---|---|
| | | | | | | |
