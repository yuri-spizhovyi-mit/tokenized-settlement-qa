"""TAPI-09: a transaction stuck after a fee spike is replaced and confirmed once."""

from __future__ import annotations

import allure
import pytest
from web3.exceptions import TransactionNotFound

from lib.builders import USDC
from lib.transfers import get, post, statuses

pytestmark = allure.feature("Transfer API: stuck transactions")

GWEI = 10**9


@pytest.fixture
def manual_mining(system):
    """Automine off for this test: transactions wait in the pool until evm_mine."""
    chain = system.chain
    chain._rpc("evm_setAutomine", False)
    yield chain
    chain._rpc("evm_setAutomine", True)


def _mine(chain, base_fee: int | None = None) -> None:
    if base_fee is not None:
        chain._rpc("anvil_setNextBlockBaseFeePerGas", hex(base_fee))
    chain._rpc("evm_mine")


@pytest.mark.req("TAPI-09")
def test_transaction_dropped_by_a_fee_spike_is_replaced_and_confirmed_once(
    system, api, make_service, hot_wallet, manual_mining
):
    """TAPI-09: the worker sends at 2 x base fee + tip. Before it is mined, the base fee
    jumps to 100 gwei. Anvil *drops* the now-underpriced transaction: no error, no
    receipt, nonce unused (F-20). After `stuck_after_blocks` the worker re-signs the
    same nonce at a higher fee; that one is mined. The payee is paid exactly once."""
    a, chain, token = system.accounts, manual_mining, system.token
    service = make_service(stuck_after_blocks=2)
    nonce_before = chain.nonce(hot_wallet.address)
    transfer = post(api, "spike", a.payee.address, str(30 * USDC)).json()

    service.tick()
    first = get(api, transfer["id"])
    assert first["status"] == "submitted" and len(first["attempts"]) == 1
    original = first["attempts"][0]
    assert chain.w3.eth.get_transaction(original)["blockNumber"] is None  # in the pool

    _mine(chain, base_fee=100 * GWEI)  # the spike: our max fee is now below the base fee
    _mine(chain)
    with pytest.raises(TransactionNotFound):
        chain.w3.eth.get_transaction(original)  # evicted, silently
    assert chain.nonce(hot_wallet.address) == nonce_before

    service.tick()  # 2 blocks without a receipt: stuck -> replaced, same nonce
    replaced = get(api, transfer["id"])
    assert statuses(replaced)[-2:] == ["stuck", "submitted"]
    assert len(replaced["attempts"]) == 2 and replaced["nonce"] == nonce_before

    _mine(chain)
    service.tick()
    _mine(chain)
    service.tick()

    final = get(api, transfer["id"])
    assert statuses(final) == ["accepted", "signed", "submitted", "stuck", "submitted", "mined", "confirmed"]
    assert final["tx_hash"] == final["attempts"][1] != original
    assert token.balance_of(a.payee.address) == 30 * USDC
    assert chain.nonce(hot_wallet.address) == nonce_before + 1
