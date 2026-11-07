"""A scriptable EIP-1193 wallet for Playwright tests (book Ch 12).

The page sees `window.ethereum` (INIT_SCRIPT). Every `request()` goes to
`MockWallet.handle` in Python through `page.expose_function`, so a test decides what
the "user" does and can assert on exactly what the page asked for:

- `approve` (default): sign `eth_sendTransaction` locally with the account's key and
  broadcast it to Anvil, as MetaMask would;
- `reject`: answer EIP-1193 error 4001 "User rejected the request", as a user who
  clicks Reject would. Nothing is signed or sent;
- `before_send`: a hook run after the user approves, before broadcasting, so a test
  can change the chain in between (revert after simulation);
- `chain_id`: the chain the wallet claims to be on (wrong-network tests).

Reads (`eth_call`, receipts, ...) are forwarded to the node unchanged, including JSON-RPC
error objects with their revert `data`.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from eth_account.signers.local import LocalAccount
from web3 import Web3

USER_REJECTED = 4001

INIT_SCRIPT = """
(() => {
  const listeners = {};
  window.ethereum = {
    isMockWallet: true,
    async request({ method, params }) {
      const reply = JSON.parse(await window.__wallet(method, JSON.stringify(params ?? [])));
      if (reply.error) {
        const err = new Error(reply.error.message);
        err.code = reply.error.code;
        err.data = reply.error.data;
        throw err;
      }
      return reply.result;
    },
    on(event, cb) { (listeners[event] ||= []).push(cb); },
    removeListener(event, cb) { listeners[event] = (listeners[event] || []).filter((f) => f !== cb); },
    __emit(event, value) { (listeners[event] || []).forEach((cb) => cb(value)); },
  };
})();
"""


class MockWallet:
    def __init__(self, w3: Web3, account: LocalAccount, chain_id: int | None = None):
        self.w3 = w3
        self.account = account
        self.chain_id = chain_id if chain_id is not None else w3.eth.chain_id
        self.mode = "approve"
        self.connected = False
        self.before_send: Callable[[dict], None] | None = None
        self.requests: list[tuple[str, list]] = []
        self.sent: list[str] = []  # hashes this wallet broadcast

    def methods(self) -> list[str]:
        return [m for m, _ in self.requests]

    # page.expose_function calls this with (method, json-encoded params) -> json reply
    def handle(self, method: str, params_json: str) -> str:
        params = json.loads(params_json)
        self.requests.append((method, params))
        try:
            return json.dumps({"result": self._dispatch(method, params)})
        except WalletError as err:
            return json.dumps({"error": {"code": err.code, "message": str(err), "data": err.data}})

    def _dispatch(self, method: str, params: list) -> Any:
        if method == "eth_requestAccounts":
            if self.mode == "reject":
                raise WalletError(USER_REJECTED, "User rejected the request.")
            self.connected = True
            return [self.account.address]
        if method == "eth_accounts":
            return [self.account.address] if self.connected else []
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_sendTransaction":
            return self._send(params[0])
        response = self.w3.provider.make_request(method, params)
        if "error" in response:
            error = response["error"]
            raise WalletError(error.get("code", -32000), error.get("message", ""), error.get("data"))
        return response["result"]

    def _send(self, tx: dict) -> str:
        if self.mode == "reject":
            raise WalletError(USER_REJECTED, "User rejected the request.")
        if self.before_send:
            self.before_send(tx)
        base = self.w3.eth.get_block("latest")["baseFeePerGas"]
        signed = self.account.sign_transaction({
            "to": Web3.to_checksum_address(tx["to"]),
            "data": tx["data"],
            "value": int(tx.get("value", "0x0"), 16),
            "gas": 100_000,  # a real wallet estimates; fixed here so a revert still gets mined
            "maxFeePerGas": 2 * base + 10**9,
            "maxPriorityFeePerGas": 10**9,
            "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
            "chainId": self.w3.eth.chain_id,
        })
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction).to_0x_hex()
        self.sent.append(tx_hash)
        return tx_hash


class WalletError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.data = code, data
