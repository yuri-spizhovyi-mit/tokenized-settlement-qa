// Minimal SettlementToken transfer page (milestone 10, book Ch 12).
//
// It talks only to an EIP-1193 wallet (window.ethereum): no RPC URL, no private
// keys. Configuration comes from the query string:
//   ?token=0x…&chainId=31337&largeValue=10000
//
// Rules the Playwright suite (tests/ui/) pins:
// - Amounts are parsed and formatted with BigInt, never Number: 2^53 + 1 base units
//   must survive a round trip (UI-D2).
// - Every transfer is simulated with eth_call first. A transfer that would revert is
//   explained in words and the wallet is never asked to sign it (UI-D3).
// - Amounts at or above `largeValue` whole tokens need an explicit second
//   confirmation that shows the exact amount and recipient (UI-D4).
// - A rejection in the wallet (EIP-1193 code 4001) is reported as "nothing was
//   sent", and the form keeps its values (UI-D5).
// - One click, one transaction: Send is disabled while a transfer is in flight.

// Function and error selectors. tests/ui/test_ui_contract.py (UI-09) checks every
// one of them against contracts/out/, so they can't drift from the contract.
export const SELECTORS = {
  transfer: "0xa9059cbb",   // transfer(address,uint256)
  balanceOf: "0x70a08231",  // balanceOf(address)
  decimals: "0x313ce567",   // decimals()
  symbol: "0x95d89b41",     // symbol()
};

export const ERRORS = {
  "0xc2e4d6ef": { name: "AccountNotAllowlisted", args: ["address"] },
  "0xe450d38c": { name: "ERC20InsufficientBalance", args: ["address", "uint256", "uint256"] },
  "0xec442f05": { name: "ERC20InvalidReceiver", args: ["address"] },
  "0xd93c0665": { name: "EnforcedPause", args: [] },
};

const USER_REJECTED = 4001; // EIP-1193

// ---- pure helpers (exported for the unit-level tests) ----------------------------

const AMOUNT = /^(\d+)(?:\.(\d+))?$/;
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;

/** "1234.5" -> 1234500000n with 6 decimals. Throws a user-facing message. */
export function parseUnits(text, decimals) {
  const trimmed = String(text).trim();
  const match = AMOUNT.exec(trimmed);
  if (!match) throw new Error("Enter an amount like 125 or 125.50");
  const [, whole, fraction = ""] = match;
  if (fraction.length > decimals) throw new Error(`At most ${decimals} decimal places`);
  const value = BigInt(whole) * 10n ** BigInt(decimals) + BigInt(fraction.padEnd(decimals, "0") || "0");
  if (value === 0n) throw new Error("Amount must be greater than zero");
  if (value >= 2n ** 256n) throw new Error("Amount is too large");
  return value;
}

/** 1234500000n -> "1,234.5" with 6 decimals. Exact; trailing zeros trimmed. */
export function formatUnits(value, decimals) {
  const base = 10n ** BigInt(decimals);
  const whole = (value / base).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = (value % base).toString().padStart(decimals, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : whole;
}

const word = (hex) => hex.replace(/^0x/, "").padStart(64, "0");
export const encodeTransfer = (to, value) =>
  SELECTORS.transfer + word(to.toLowerCase()) + word(value.toString(16));

/** Revert data -> a sentence for a person. */
export function explainRevert(data, decimals) {
  const hex = typeof data === "string" ? data : data?.data;
  if (!hex || hex.length < 10) return "The transfer would fail (no reason given).";
  const known = ERRORS[hex.slice(0, 10)];
  if (!known) return `The transfer would fail (error ${hex.slice(0, 10)}).`;
  const words = hex.slice(10).match(/.{64}/g) || [];
  const arg = (i) => known.args[i] === "address" ? "0x" + words[i].slice(24) : BigInt("0x" + words[i]);
  switch (known.name) {
    case "AccountNotAllowlisted": return `Account ${arg(0)} is not on the allowlist.`;
    case "ERC20InsufficientBalance":
      return `Insufficient balance: you have ${formatUnits(arg(1), decimals)}, you tried to send ${formatUnits(arg(2), decimals)}.`;
    case "ERC20InvalidReceiver": return "That recipient can't receive tokens.";
    case "EnforcedPause": return "Transfers are paused right now.";
  }
}

// ---- page ------------------------------------------------------------------------

const params = new URLSearchParams(location.search);
const config = {
  token: params.get("token"),
  chainId: Number(params.get("chainId") || 31337),
  largeValue: BigInt(params.get("largeValue") || "10000"),
};
const $ = (id) => document.getElementById(id);
const state = { account: null, decimals: 6, balance: 0n, busy: false, pending: null };

function show(id, text, tone) {
  const el = $(id);
  el.textContent = text;
  if (tone) el.dataset.tone = tone;
  el.hidden = !text;
}
const clearMessages = () => { show("error", ""); };

async function call(data, block = "latest") {
  return window.ethereum.request({ method: "eth_call", params: [{ from: state.account, to: config.token, data }, block] });
}

async function refresh() {
  const accounts = await window.ethereum.request({ method: "eth_accounts" });
  state.account = accounts[0] || null;
  $("account").textContent = state.account || "";
  $("account-line").hidden = !state.account;
  const chainId = Number(await window.ethereum.request({ method: "eth_chainId" }));
  const wrong = chainId !== config.chainId;
  show("network", wrong ? `Wrong network: your wallet is on chain ${chainId}, this page needs chain ${config.chainId}.` : "");
  $("send").disabled = wrong || state.busy;
  if (!state.account || wrong) return;
  state.decimals = Number(BigInt(await call(SELECTORS.decimals)));
  const symbolHex = await call(SELECTORS.symbol);
  const len = Number(BigInt("0x" + symbolHex.slice(66, 130)));
  const symbol = new TextDecoder().decode(Uint8Array.from(symbolHex.slice(130, 130 + 2 * len).match(/.{2}/g) || [], (b) => parseInt(b, 16)));
  document.querySelectorAll(".symbol").forEach((el) => { el.textContent = symbol; });
  state.balance = BigInt(await call(SELECTORS.balanceOf + word(state.account.toLowerCase())));
  $("balance").textContent = formatUnits(state.balance, state.decimals);
  $("balance-line").hidden = false;
  $("transfer").hidden = false;
}

async function connect() {
  clearMessages();
  try {
    await window.ethereum.request({ method: "eth_requestAccounts" });
  } catch (err) {
    show("error", err.code === USER_REJECTED ? "Connection request rejected in your wallet." : `Wallet error: ${err.message}`);
    return;
  }
  $("connect").hidden = true;
  await refresh();
}

function readForm() {
  const recipient = $("recipient").value.trim();
  if (!ADDRESS.test(recipient)) throw new Error("Enter a recipient address: 0x followed by 40 hex characters");
  const value = parseUnits($("amount").value, state.decimals);
  if (value > state.balance) throw new Error(`Insufficient balance: you have ${formatUnits(state.balance, state.decimals)}`);
  return { recipient, value };
}

async function submit(event) {
  event?.preventDefault();
  if (state.busy) return;              // one click, one transaction
  clearMessages();
  let order;
  try { order = readForm(); } catch (err) { show("error", err.message); return; }
  setBusy(true);
  try {
    const data = encodeTransfer(order.recipient, order.value);
    try {
      await call(data);                // would it revert? Don't ask the wallet if so.
    } catch (err) {
      show("error", explainRevert(err.data, state.decimals));
      return;
    }
    if (order.value >= config.largeValue * 10n ** BigInt(state.decimals)) {
      state.pending = { ...order, data };
      $("confirm-amount").textContent = formatUnits(order.value, state.decimals);
      $("confirm-recipient").textContent = order.recipient;
      $("confirm").hidden = false;
      return;                          // waits for "Yes, send it"
    }
    await send(order, data);
  } finally {
    if (!state.pending) setBusy(false);
  }
}

async function send(order, data) {
  let hash;
  try {
    hash = await window.ethereum.request({ method: "eth_sendTransaction", params: [{ from: state.account, to: config.token, data }] });
  } catch (err) {
    show("error", err.code === USER_REJECTED
      ? "You rejected the transaction in your wallet. Nothing was sent."
      : `Wallet error: ${err.message}`);
    return;
  }
  show("status", `Pending: ${hash}`, "warn");
  $("status").dataset.hash = hash;
  const receipt = await waitForReceipt(hash);
  if (!receipt) { show("status", `Not mined yet: ${hash}. It may still confirm.`, "warn"); return; }
  if (receipt.status === "0x1") {
    show("status", `Confirmed in block ${Number(receipt.blockNumber)}: sent ${formatUnits(order.value, state.decimals)} to ${order.recipient}`, "ok");
    $("amount").value = "";
  } else {
    let reason = "reverted";
    try { await call(data, receipt.blockNumber); } catch (err) { reason = explainRevert(err.data, state.decimals); }
    show("status", `Failed on chain in block ${Number(receipt.blockNumber)}: ${reason}`, "error");
  }
  await refresh();
}

async function waitForReceipt(hash, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const receipt = await window.ethereum.request({ method: "eth_getTransactionReceipt", params: [hash] });
    if (receipt) return receipt;
    await new Promise((r) => setTimeout(r, 250));
  }
  return null;
}

function setBusy(busy) {
  state.busy = busy;
  $("send").disabled = busy || !$("network").hidden;
}

async function confirmYes() {
  const order = state.pending;
  $("confirm").hidden = true;
  state.pending = null;
  try { await send(order, order.data); } finally { setBusy(false); }
}

function confirmNo() {
  $("confirm").hidden = true;
  state.pending = null;
  setBusy(false);
  show("status", "Cancelled. Nothing was sent.", "warn");
}

if (typeof document !== "undefined" && document.getElementById("connect")) {
  if (!window.ethereum) {
    show("error", "No wallet found. Install an EIP-1193 wallet to use this page.");
    $("connect").disabled = true;
  } else {
    $("connect").addEventListener("click", connect);
    $("transfer").addEventListener("submit", submit);
    $("confirm-yes").addEventListener("click", confirmYes);
    $("confirm-no").addEventListener("click", confirmNo);
    window.ethereum.on?.("accountsChanged", () => refresh());
    window.ethereum.on?.("chainChanged", () => refresh());
  }
}
