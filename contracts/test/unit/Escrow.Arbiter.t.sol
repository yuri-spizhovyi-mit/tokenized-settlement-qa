// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {EscrowBase} from "./EscrowBase.t.sol";
import {Escrow} from "../../src/Escrow.sol";

/// @notice SEC-07, SEC-08, SEC-09: the arbiter can be rotated by the escrow admin
///         (issue #7), the old arbiter loses power at once, and deals in flight settle.
/// @dev Requirements (IDs are used in test NatSpec and the RTM):
///      SEC-07  Only the escrow's DEFAULT_ADMIN_ROLE can rotate the arbiter; rotation
///              emits ArbiterChanged(previous, new) and rejects the zero address.
///      SEC-08  The previous arbiter loses release power in the same block, both
///              directly and through signatures it issued before rotation.
///      SEC-09  Deals opened before a rotation settle normally under the new arbiter:
///              rotation never strands funds or rewrites a deal.
contract EscrowArbiterTest is EscrowBase {
    address internal newArbiter = makeAddr("newArbiter");

    function _rotate(address to) internal {
        vm.prank(escrowAdmin);
        escrow.setArbiter(to);
    }

    // ---- SEC-07: who may rotate ------------------------------------------------------

    /// @notice SEC-07: rotation updates the arbiter and emits both addresses.
    function test_SetArbiter_RotatesAndEmits() public {
        vm.expectEmit(true, true, false, true, address(escrow));
        emit Escrow.ArbiterChanged(arbiter, newArbiter);
        _rotate(newArbiter);

        assertEq(escrow.arbiter(), newArbiter);
    }

    /// @notice SEC-07: nobody but the escrow admin can rotate: not the arbiter itself,
    ///         not the token's admin, not a counterparty.
    function test_RevertWhen_NonAdminSetsArbiter() public {
        address[5] memory callers = [arbiter, admin, payer, payee, stranger];
        for (uint256 i; i < callers.length; ++i) {
            vm.prank(callers[i]);
            vm.expectRevert(
                abi.encodeWithSelector(
                    IAccessControl.AccessControlUnauthorizedAccount.selector, callers[i], bytes32(0)
                )
            );
            escrow.setArbiter(callers[i]);
        }
        assertEq(escrow.arbiter(), arbiter, "arbiter changed");
    }

    /// @notice SEC-07: rotating to the zero address would leave no one able to release.
    function test_RevertWhen_SetArbiterToZero() public {
        vm.prank(escrowAdmin);
        vm.expectRevert(Escrow.ZeroAddress.selector);
        escrow.setArbiter(address(0));
        assertEq(escrow.arbiter(), arbiter);
    }

    // ---- SEC-08: old arbiter loses power immediately ------------------------------------

    /// @notice SEC-08: in the same block as the rotation, the old arbiter's release
    ///         reverts with NotArbiter and the deal is untouched.
    function test_RevertWhen_OldArbiterReleasesAfterRotation() public {
        uint256 id = _open();
        _rotate(newArbiter);
        Balances memory before = _balances();

        vm.prank(arbiter);
        vm.expectRevert(Escrow.NotArbiter.selector);
        escrow.release(id);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    // ---- SEC-09: deals in flight ---------------------------------------------------------

    /// @notice SEC-09: a deal opened under the old arbiter is released by the new one,
    ///         and the payee is paid in full.
    function test_Release_InFlightDealSettlesUnderNewArbiter() public {
        uint256 id = _open();
        _rotate(newArbiter);
        Balances memory before = _balances();

        vm.prank(newArbiter);
        escrow.release(id);

        assertEq(token.balanceOf(payee), before.payee + AMOUNT);
        assertEq(token.balanceOf(address(escrow)), before.escrow - AMOUNT);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Released);
    }

    /// @notice SEC-09: rotation doesn't touch the refund path; an in-flight deal
    ///         refunds at its deadline as before.
    function test_Refund_InFlightDealUnaffectedByRotation() public {
        uint256 id = _open();
        _rotate(newArbiter);

        vm.warp(deadline);
        vm.prank(payer);
        escrow.refund(id);

        assertEq(token.balanceOf(payer), PAYER_START);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Refunded);
    }

    /// @notice SEC-09: the escrow admin is a separate duty from the arbiter. Rotating
    ///         the arbiter gives the admin no release power of its own.
    function test_RevertWhen_EscrowAdminReleases() public {
        uint256 id = _open();
        vm.prank(escrowAdmin);
        vm.expectRevert(Escrow.NotArbiter.selector);
        escrow.release(id);
    }
}
