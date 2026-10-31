// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {EscrowBase} from "../unit/EscrowBase.t.sol";
import {Escrow} from "../../src/Escrow.sol";

/// @notice Property-based tests for Escrow. Inputs are shaped with `bound()`; `vm.assume`
///         is used only to exclude specific addresses.
/// @dev Requirements (IDs are used in test NatSpec and the RTM):
///      PROP-01  For any amount and deadline, open records exactly that amount and moves
///               exactly that amount from payer to escrow.
///      PROP-02  For any amount and any allowlisted payee, open then release moves exactly
///               that amount from payer to payee, and the escrow ends where it started.
///      PROP-03  No address other than the arbiter can release, whatever it is.
///      PROP-04  For any time strictly before the deadline, refund reverts with
///               TooEarly(deadline) and nothing moves.
contract EscrowFuzzTest is EscrowBase {
    /// @dev Gives the payer exactly `amount` on top of the fixture balance.
    function _fund(uint128 amount) internal {
        vm.prank(minter);
        token.mint(payer, amount);
    }

    /// @dev Every address the fixture uses for a fixed purpose, plus address(0).
    function _assumeFreshAccount(address a) internal view {
        vm.assume(a != address(0) && a != address(escrow) && a != address(token));
        vm.assume(a != payer && a != payee && a != arbiter && a != stranger);
        vm.assume(a != admin && a != minter && a != pauser && a != compliance);
    }

    /// @notice PROP-01: open records exactly the fuzzed amount and deadline.
    function testFuzz_Open_RecordsExactAmount(uint128 amount, uint64 duration) public {
        amount = uint128(bound(amount, 1, type(uint128).max - PAYER_START));
        uint64 nowTs = uint64(vm.getBlockTimestamp());
        uint64 deadline_ = uint64(bound(duration, 1, type(uint64).max - nowTs)) + nowTs;
        _fund(amount);
        Balances memory before = _balances();

        vm.prank(payer);
        uint256 id = escrow.open(payee, amount, deadline_);

        _assertDeal(id, payer, payee, amount, deadline_, Escrow.State.Funded);
        assertEq(token.balanceOf(payer), before.payer - amount, "payer");
        assertEq(token.balanceOf(address(escrow)), before.escrow + amount, "escrow");
        assertEq(token.balanceOf(payee), before.payee, "payee");
        assertEq(token.totalSupply(), before.totalSupply, "supply");
    }

    /// @notice PROP-02: open then release moves exactly the amount, payer to payee, for
    ///         any allowlisted payee address.
    function testFuzz_OpenThenRelease_MovesExactAmount(uint128 amount, address payee_) public {
        _assumeFreshAccount(payee_);
        amount = uint128(bound(amount, 1, type(uint128).max - PAYER_START));
        _fund(amount);
        vm.prank(compliance);
        token.setAllowlisted(payee_, true);
        uint256 payerBefore = token.balanceOf(payer);
        uint256 payeeBefore = token.balanceOf(payee_);
        uint256 escrowBefore = token.balanceOf(address(escrow));
        uint256 supplyBefore = token.totalSupply();

        vm.prank(payer);
        uint256 id = escrow.open(payee_, amount, deadline);
        vm.prank(arbiter);
        escrow.release(id);

        assertEq(token.balanceOf(payer), payerBefore - amount, "payer");
        assertEq(token.balanceOf(payee_), payeeBefore + amount, "payee");
        assertEq(token.balanceOf(address(escrow)), escrowBefore, "escrow kept a remainder");
        assertEq(token.totalSupply(), supplyBefore, "supply");
        _assertDeal(id, payer, payee_, amount, deadline, Escrow.State.Released);
    }

    /// @notice PROP-03: whatever address calls release, unless it is the arbiter, the call
    ///         reverts with NotArbiter and the deal stays Funded.
    function testFuzz_RevertWhen_ReleaseCallerIsNotArbiter(address caller, uint256 warpBy) public {
        vm.assume(caller != arbiter);
        uint256 id = _open();
        // The rule holds at any time, including after the deadline.
        vm.warp(vm.getBlockTimestamp() + bound(warpBy, 0, 3 * DURATION));
        Balances memory before = _balances();

        vm.prank(caller);
        vm.expectRevert(Escrow.NotArbiter.selector);
        escrow.release(id);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    /// @notice PROP-04: for every second from opening up to deadline - 1, any caller's
    ///         refund reverts with TooEarly(deadline).
    function testFuzz_RevertWhen_RefundBeforeDeadline(
        uint64 duration,
        uint64 offset,
        address caller
    ) public {
        uint64 nowTs = uint64(vm.getBlockTimestamp());
        uint64 deadline_ = nowTs + uint64(bound(duration, 1, 365 days));
        vm.prank(payer);
        uint256 id = escrow.open(payee, AMOUNT, deadline_);

        // Any instant in [open time, deadline - 1].
        vm.warp(bound(offset, nowTs, deadline_ - 1));
        Balances memory before = _balances();

        vm.prank(caller);
        vm.expectRevert(abi.encodeWithSelector(Escrow.TooEarly.selector, deadline_));
        escrow.refund(id);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline_, Escrow.State.Funded);
    }
}
