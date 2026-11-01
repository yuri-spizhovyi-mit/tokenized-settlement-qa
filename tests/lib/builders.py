"""DealBuilder: describe a deal in a line, get a funded, opened deal.

    deal = DealBuilder(system).amount(250 * USDC).payee(accounts.payee).deadline_in(DAY).open()

The builder does the setup every escrow test needs and none of them is about:
- allowlists the payer, payee and escrow through compliance;
- mints the payer enough tokens;
- approves the escrow for exactly the amount.

Anything a test *is* about (a missing allowlisting, a short approval) is opted out of
explicitly with `.without_allowlisting(...)` or `.approve(...)`, so the test reads
as the condition it checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eth_account.signers.local import LocalAccount

from .clients import Opened

if TYPE_CHECKING:
    from conftest import System

USDC = 10**6  # SettlementToken has 6 decimals
HOUR = 3600
DAY = 24 * HOUR


class DealBuilder:
    def __init__(self, system: System):
        self._s = system
        self._payer: LocalAccount = system.accounts.payer
        self._payee: str = system.accounts.payee.address
        self._amount = 250 * USDC
        self._deadline_in = 7 * DAY
        self._approve: int | None = None
        self._skip_allowlist: set[str] = set()

    def payer(self, account: LocalAccount) -> DealBuilder:
        self._payer = account
        return self

    def payee(self, account: LocalAccount | str) -> DealBuilder:
        self._payee = account if isinstance(account, str) else account.address
        return self

    def amount(self, amount: int) -> DealBuilder:
        self._amount = amount
        return self

    def deadline_in(self, seconds: int) -> DealBuilder:
        self._deadline_in = seconds
        return self

    def approve(self, amount: int) -> DealBuilder:
        """Approve a specific amount instead of exactly the deal amount."""
        self._approve = amount
        return self

    def without_allowlisting(self, *addresses: str) -> DealBuilder:
        """Leave these addresses off the allowlist (payer, payee or the escrow)."""
        self._skip_allowlist.update(a.lower() for a in addresses)
        return self

    def deadline(self) -> int:
        """The absolute deadline open() will use, computed from the current block."""
        return self._s.chain.now() + self._deadline_in

    def prepare(self) -> int:
        """Do all the setup and return the deadline, without opening. For tests whose
        subject is the open() call itself (e.g. expecting it to revert)."""
        token, escrow, acc = self._s.token, self._s.escrow, self._s.accounts
        parties = (self._payer.address, self._payee, escrow.address)
        # List everyone first: minting to an unlisted payer would itself revert.
        for address in parties:
            if not token.is_allowlisted(address):
                token.set_allowlisted(acc.compliance, address, True)
        shortfall = self._amount - token.balance_of(self._payer.address)
        if shortfall > 0:
            token.mint(acc.minter, self._payer.address, shortfall)
        token.approve(self._payer, escrow.address, self._approve if self._approve is not None else self._amount)
        # Then remove whatever the test opted out of.
        for address in parties:
            if address.lower() in self._skip_allowlist:
                token.set_allowlisted(acc.compliance, address, False)
        return self.deadline()

    def open(self) -> Opened:
        deadline = self.prepare()
        return self._s.escrow.open(self._payer, self._payee, self._amount, deadline)
