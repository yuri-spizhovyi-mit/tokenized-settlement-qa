// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {EscrowBase} from "./EscrowBase.t.sol";
import {Escrow} from "../../src/Escrow.sol";

/// @notice ESC-04, ESC-06, ESC-07: releasing a deal to the payee.
contract EscrowReleaseTest is EscrowBase {
    uint256 internal id;

    function setUp() public override {
        super.setUp();
        id = _open();
    }

    /// @notice ESC-04: release pays the payee the full amount and empties the escrow.
    function test_Release_PaysPayeeAndMarksReleased() public {
        Balances memory before = _balances();

        vm.prank(arbiter);
        escrow.release(id);

        assertEq(token.balanceOf(payee), before.payee + AMOUNT, "payee");
        assertEq(token.balanceOf(address(escrow)), before.escrow - AMOUNT, "escrow");
        assertEq(token.balanceOf(payer), before.payer, "payer");
        assertEq(token.totalSupply(), before.totalSupply, "supply");
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Released);
    }

    /// @notice ESC-04: Released names the deal, the payee and the amount, from the escrow.
    function test_Release_EmitsReleased() public {
        vm.expectEmit(true, true, false, true, address(escrow));
        emit Escrow.Released(id, payee, AMOUNT);
        vm.expectEmit(true, true, false, true, address(token));
        emit IERC20.Transfer(address(escrow), payee, AMOUNT);

        vm.prank(arbiter);
        escrow.release(id);
    }

    /// @notice ESC-04: the arbiter may release after the deadline too, as long as nobody
    ///         has refunded yet. First valid call wins.
    function test_Release_SucceedsAfterDeadlineIfStillFunded() public {
        vm.warp(deadline + 1 days);

        vm.prank(arbiter);
        escrow.release(id);

        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Released);
    }

    /// @notice ESC-04: only the arbiter can release, whoever else asks.
    function test_RevertWhen_ReleaseCallerIsNotArbiter() public {
        address[4] memory others = [payer, payee, stranger, admin];
        Balances memory before = _balances();

        for (uint256 i; i < others.length; ++i) {
            vm.prank(others[i]);
            vm.expectRevert(Escrow.NotArbiter.selector);
            escrow.release(id);
        }

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    /// @notice ESC-06: a deal pays out once. The second release names the state it found.
    function test_RevertWhen_ReleaseTwice() public {
        vm.startPrank(arbiter);
        escrow.release(id);
        Balances memory before = _balances();

        vm.expectRevert(_wrongState(Escrow.State.Released));
        escrow.release(id);
        vm.stopPrank();

        _assertBalancesUnchanged(before);
    }

    /// @notice ESC-06: once released, the payer cannot claw the funds back by refunding.
    function test_RevertWhen_RefundAfterRelease() public {
        vm.prank(arbiter);
        escrow.release(id);
        vm.warp(deadline);
        Balances memory before = _balances();

        vm.prank(payer);
        vm.expectRevert(_wrongState(Escrow.State.Released));
        escrow.refund(id);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Released);
    }

    /// @notice ESC-07: an id that was never opened reads as an empty struct whose state
    ///         field is 0, which is State.Funded. Release must not treat it as live.
    function test_RevertWhen_ReleaseUnknownDeal() public {
        uint256 unknown = escrow.nextId();

        vm.prank(arbiter);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnknownDeal.selector, unknown));
        escrow.release(unknown);
    }
}
