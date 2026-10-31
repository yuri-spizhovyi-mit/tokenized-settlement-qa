// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {EscrowBase} from "./EscrowBase.t.sol";
import {Escrow} from "../../src/Escrow.sol";

/// @notice ESC-05, ESC-06, ESC-07: refunding a deal to the payer after its deadline.
contract EscrowRefundTest is EscrowBase {
    uint256 internal id;

    function setUp() public override {
        super.setUp();
        id = _open();
    }

    // ---- the deadline boundary --------------------------------------------------

    /// @notice ESC-05: one second before the deadline the payer must still wait. The
    ///         error carries the deadline so a client can tell the user when to retry.
    function test_RevertWhen_RefundOneSecondBeforeDeadline() public {
        vm.warp(deadline - 1);
        Balances memory before = _balances();

        vm.prank(payer);
        vm.expectRevert(abi.encodeWithSelector(Escrow.TooEarly.selector, deadline));
        escrow.refund(id);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    /// @notice ESC-05: the deadline itself is the first refundable second
    ///         (`block.timestamp >= deadline`). Decision recorded in docs/decisions.md.
    function test_Refund_SucceedsExactlyAtDeadline() public {
        vm.warp(deadline);
        Balances memory before = _balances();

        vm.prank(payer);
        escrow.refund(id);

        assertEq(token.balanceOf(payer), before.payer + AMOUNT, "payer");
        assertEq(token.balanceOf(address(escrow)), before.escrow - AMOUNT, "escrow");
        assertEq(token.balanceOf(payee), before.payee, "payee");
        assertEq(token.totalSupply(), before.totalSupply, "supply");
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Refunded);
    }

    /// @notice ESC-05: right after opening, refund is refused with the same error.
    function test_RevertWhen_RefundImmediatelyAfterOpen() public {
        vm.prank(payer);
        vm.expectRevert(abi.encodeWithSelector(Escrow.TooEarly.selector, deadline));
        escrow.refund(id);
    }

    /// @notice ESC-05: Refunded names the deal, the payer and the amount, from the escrow.
    function test_Refund_EmitsRefunded() public {
        vm.warp(deadline);

        vm.expectEmit(true, true, false, true, address(escrow));
        emit Escrow.Refunded(id, payer, AMOUNT);
        vm.expectEmit(true, true, false, true, address(token));
        emit IERC20.Transfer(address(escrow), payer, AMOUNT);

        vm.prank(payer);
        escrow.refund(id);
    }

    // ---- no caller restriction ----------------------------------------------------

    /// @notice ESC-05: refund has no caller restriction, but the money only ever goes to
    ///         the payer. A keeper, the payee or a stranger can trigger it. Decision
    ///         recorded in docs/decisions.md.
    function test_Refund_AnyCallerRefundsToPayer() public {
        vm.warp(deadline);
        Balances memory before = _balances();
        uint256 strangerBefore = token.balanceOf(stranger);

        vm.prank(stranger);
        escrow.refund(id);

        assertEq(token.balanceOf(payer), before.payer + AMOUNT, "payer not refunded");
        assertEq(token.balanceOf(stranger), strangerBefore, "caller must gain nothing");
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Refunded);
    }

    /// @notice ESC-05: the arbiter can refund too; the role doesn't matter, the clock does.
    function test_Refund_ArbiterCanRefundAfterDeadline() public {
        vm.warp(deadline);
        vm.prank(arbiter);
        escrow.refund(id);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Refunded);
    }

    /// @notice ESC-05: an open caller list doesn't open the clock; a stranger still waits.
    function test_RevertWhen_StrangerRefundsBeforeDeadline() public {
        vm.warp(deadline - 1);
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(Escrow.TooEarly.selector, deadline));
        escrow.refund(id);
    }

    // ---- terminal state (ESC-06) ---------------------------------------------------

    /// @notice ESC-06: a deal refunds once.
    function test_RevertWhen_RefundTwice() public {
        vm.warp(deadline);
        vm.prank(payer);
        escrow.refund(id);
        Balances memory before = _balances();

        vm.prank(payer);
        vm.expectRevert(_wrongState(Escrow.State.Refunded));
        escrow.refund(id);

        _assertBalancesUnchanged(before);
    }

    /// @notice ESC-06: once refunded, the arbiter can no longer release.
    function test_RevertWhen_ReleaseAfterRefund() public {
        vm.warp(deadline);
        vm.prank(payer);
        escrow.refund(id);
        Balances memory before = _balances();

        vm.prank(arbiter);
        vm.expectRevert(_wrongState(Escrow.State.Refunded));
        escrow.release(id);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Refunded);
    }

    /// @notice ESC-06: the state check comes before the clock check, so a settled deal
    ///         reports WrongState even before its deadline, not a misleading TooEarly.
    function test_RevertWhen_RefundReleasedDealBeforeDeadline() public {
        vm.prank(arbiter);
        escrow.release(id);

        vm.prank(payer);
        vm.expectRevert(_wrongState(Escrow.State.Released));
        escrow.refund(id);
    }

    /// @notice ESC-07: an id that was never opened has deadline 0, so the clock check
    ///         alone would pass. Refund must reject it as unknown.
    function test_RevertWhen_RefundUnknownDeal() public {
        uint256 unknown = escrow.nextId();
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnknownDeal.selector, unknown));
        escrow.refund(unknown);
    }
}
