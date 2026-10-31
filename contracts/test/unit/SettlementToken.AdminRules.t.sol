// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {
    IAccessControlDefaultAdminRules
} from "@openzeppelin/contracts/access/extensions/IAccessControlDefaultAdminRules.sol";
import {SettlementTokenBase} from "./SettlementTokenBase.t.sol";

/// @notice SEC-05, SEC-06: the token's admin can't be lost by accident, and a handover
///         is two-step and waits out the delay (issue #3, AccessControlDefaultAdminRules).
/// @dev Requirements (IDs are used in test NatSpec and the RTM):
///      SEC-05  DEFAULT_ADMIN_ROLE cannot be granted, revoked or renounced in one step:
///              every shortcut that could lose or duplicate the admin reverts.
///      SEC-06  An admin handover is begin -> wait > delay -> accept by the new admin.
///              Until it completes the old admin keeps full power; after it, none.
contract SettlementTokenAdminRulesTest is SettlementTokenBase {
    address internal newAdmin = makeAddr("newAdmin");
    uint48 internal delay;

    function setUp() public override {
        super.setUp();
        delay = token.defaultAdminDelay();
    }

    function _enforced() internal pure returns (bytes memory) {
        return abi.encodeWithSelector(
            IAccessControlDefaultAdminRules.AccessControlEnforcedDefaultAdminRules.selector
        );
    }

    function _tooEarly(uint48 schedule) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(
            IAccessControlDefaultAdminRules.AccessControlEnforcedDefaultAdminDelay.selector,
            schedule
        );
    }

    function _assertAdminIs(address expected) internal view {
        assertEq(token.defaultAdmin(), expected, "defaultAdmin");
        assertTrue(token.hasRole(DEFAULT_ADMIN_ROLE, expected), "hasRole(admin)");
    }

    // ---- SEC-05: no accidental loss -----------------------------------------------

    /// @notice SEC-05: the deployed delay is the documented two days (SEC-D2).
    function test_Deploy_AdminDelayIsTwoDays() public view {
        assertEq(delay, 2 days);
        assertEq(token.defaultAdmin(), admin);
    }

    /// @notice SEC-05 (issue #3): the one-call renounce that used to brick governance
    ///         now reverts. Nothing is scheduled, so the schedule in the error is 0.
    function test_RevertWhen_AdminRenouncesWithoutScheduledTransfer() public {
        vm.prank(admin);
        vm.expectRevert(_tooEarly(0));
        token.renounceRole(DEFAULT_ADMIN_ROLE, admin);

        _assertAdminIs(admin);
    }

    /// @notice SEC-05: a second admin can't be added on the side.
    function test_RevertWhen_GrantingDefaultAdminRoleDirectly() public {
        vm.prank(admin);
        vm.expectRevert(_enforced());
        token.grantRole(DEFAULT_ADMIN_ROLE, newAdmin);

        assertFalse(token.hasRole(DEFAULT_ADMIN_ROLE, newAdmin));
        _assertAdminIs(admin);
    }

    /// @notice SEC-05: the admin can't revoke itself (or be revoked) in one call.
    function test_RevertWhen_RevokingDefaultAdminRoleDirectly() public {
        vm.prank(admin);
        vm.expectRevert(_enforced());
        token.revokeRole(DEFAULT_ADMIN_ROLE, admin);

        _assertAdminIs(admin);
    }

    /// @notice SEC-05: a handover to the wrong address is recoverable: nothing happens
    ///         until that address accepts, and the admin can cancel at any time.
    function test_CancelDefaultAdminTransfer_KeepsCurrentAdmin() public {
        vm.startPrank(admin);
        token.beginDefaultAdminTransfer(newAdmin);
        token.cancelDefaultAdminTransfer();
        vm.stopPrank();

        (address pending, uint48 schedule) = token.pendingDefaultAdmin();
        assertEq(pending, address(0));
        assertEq(schedule, 0);

        vm.warp(vm.getBlockTimestamp() + delay + 1);
        vm.prank(newAdmin);
        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControlDefaultAdminRules.AccessControlInvalidDefaultAdmin.selector, newAdmin
            )
        );
        token.acceptDefaultAdminTransfer();
        _assertAdminIs(admin);
    }

    /// @notice SEC-05: giving up admin for good is still possible, but only on purpose:
    ///         schedule a transfer to address(0), wait out the delay, then renounce.
    function test_Renounce_SucceedsOnlyAfterScheduledDelay() public {
        vm.prank(admin);
        token.beginDefaultAdminTransfer(address(0));
        (, uint48 schedule) = token.pendingDefaultAdmin();

        vm.warp(schedule);
        vm.prank(admin);
        vm.expectRevert(_tooEarly(schedule));
        token.renounceRole(DEFAULT_ADMIN_ROLE, admin);

        vm.warp(uint256(schedule) + 1);
        vm.prank(admin);
        token.renounceRole(DEFAULT_ADMIN_ROLE, admin);

        assertEq(token.defaultAdmin(), address(0));
        assertFalse(token.hasRole(DEFAULT_ADMIN_ROLE, admin));
    }

    // ---- SEC-06: handover respects the delay -------------------------------------------

    /// @notice SEC-06: begin records the pending admin and an exact accept schedule.
    function test_BeginDefaultAdminTransfer_SchedulesAfterDelay() public {
        uint48 expected = uint48(vm.getBlockTimestamp()) + delay;

        vm.expectEmit(true, false, false, true, address(token));
        emit IAccessControlDefaultAdminRules.DefaultAdminTransferScheduled(newAdmin, expected);
        vm.prank(admin);
        token.beginDefaultAdminTransfer(newAdmin);

        (address pending, uint48 schedule) = token.pendingDefaultAdmin();
        assertEq(pending, newAdmin);
        assertEq(schedule, expected);
        _assertAdminIs(admin); // nothing changes until accept
    }

    /// @notice SEC-06: accepting at the schedule itself is still too early: OZ requires
    ///         block.timestamp > schedule. One second later it succeeds.
    function test_RevertWhen_AcceptAtExactSchedule() public {
        vm.prank(admin);
        token.beginDefaultAdminTransfer(newAdmin);
        (, uint48 schedule) = token.pendingDefaultAdmin();

        vm.warp(schedule);
        vm.prank(newAdmin);
        vm.expectRevert(_tooEarly(schedule));
        token.acceptDefaultAdminTransfer();
        _assertAdminIs(admin);

        vm.warp(uint256(schedule) + 1);
        vm.prank(newAdmin);
        token.acceptDefaultAdminTransfer();
        _assertAdminIs(newAdmin);
    }

    /// @notice SEC-06: only the pending admin can accept, even after the delay.
    function test_RevertWhen_AcceptedByWrongAccount() public {
        vm.prank(admin);
        token.beginDefaultAdminTransfer(newAdmin);
        vm.warp(vm.getBlockTimestamp() + delay + 1);

        vm.prank(mallory);
        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControlDefaultAdminRules.AccessControlInvalidDefaultAdmin.selector, mallory
            )
        );
        token.acceptDefaultAdminTransfer();
        _assertAdminIs(admin);
    }

    /// @notice SEC-06: until the handover completes, the old admin governs fully;
    ///         afterwards the old admin has no power and the new admin has all of it.
    function test_Handover_MovesPowerAtAcceptNotBefore() public {
        vm.prank(admin);
        token.beginDefaultAdminTransfer(newAdmin);

        // Mid-delay: the old admin still grants; the pending admin can't.
        vm.warp(vm.getBlockTimestamp() + delay / 2);
        vm.prank(admin);
        token.grantRole(MINTER_ROLE, bob);
        vm.prank(newAdmin);
        vm.expectRevert(_unauthorized(newAdmin, DEFAULT_ADMIN_ROLE));
        token.revokeRole(MINTER_ROLE, bob);

        vm.warp(vm.getBlockTimestamp() + delay);
        vm.prank(newAdmin);
        token.acceptDefaultAdminTransfer();

        _assertAdminIs(newAdmin);
        assertFalse(token.hasRole(DEFAULT_ADMIN_ROLE, admin), "old admin kept the role");

        vm.prank(admin);
        vm.expectRevert(_unauthorized(admin, DEFAULT_ADMIN_ROLE));
        token.revokeRole(MINTER_ROLE, bob);

        vm.prank(newAdmin);
        token.revokeRole(MINTER_ROLE, bob);
        assertFalse(token.hasRole(MINTER_ROLE, bob));
        // Operational roles granted by the old admin survive the handover.
        assertTrue(token.hasRole(MINTER_ROLE, minter));
        assertTrue(token.hasRole(ALLOWLIST_ROLE, compliance));
    }
}
