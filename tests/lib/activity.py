"""A realistic burst of token and escrow activity for indexer and reconciliation tests.

Touches every event the indexer stores: mints, transfers, a burn, three deals
(one released, one refunded at its deadline, one left Funded), across a dozen blocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .builders import DAY, USDC, DealBuilder

if TYPE_CHECKING:
    from conftest import System


@dataclass(frozen=True)
class Activity:
    holders: tuple[str, ...]  # every address that held tokens at some point
    funded_deal: int  # the deal left Funded
    funded_amount: int


def generate_activity(system: System) -> Activity:
    a, token, escrow, chain = system.accounts, system.token, system.escrow, system.chain
    for account in (a.payer, a.payee, a.relayer, a.stranger, a.minter):
        token.set_allowlisted(a.compliance, account.address, True)

    token.mint(a.minter, a.payer.address, 5_000 * USDC)
    token.mint(a.minter, a.relayer.address, 1_234_567)
    token.transfer(a.payer, a.stranger.address, 250 * USDC)
    token.transfer(a.relayer, a.payee.address, 34_567)
    token.mint(a.minter, a.minter.address, 10 * USDC)
    token.burn(a.minter, 4 * USDC)

    released = DealBuilder(system).amount(300 * USDC).open()
    refunded = DealBuilder(system).amount(125 * USDC).deadline_in(DAY).open()
    funded = DealBuilder(system).amount(777 * USDC).deadline_in(30 * DAY).open()
    escrow.release(a.arbiter, released.deal_id)
    escrow.refund(a.relayer, refunded.deal_id, at=refunded.event["deadline"])
    chain.mine()

    holders = (a.payer.address, a.payee.address, a.relayer.address, a.stranger.address,
               a.minter.address, escrow.address)
    return Activity(holders=holders, funded_deal=funded.deal_id, funded_amount=777 * USDC)
