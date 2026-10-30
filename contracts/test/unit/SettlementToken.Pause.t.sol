// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {SettlementTokenBase} from "./SettlementTokenBase.t.sol";
import {SettlementToken} from "../../src/SettlementToken.sol";

/// @notice TOK-07: pause is an emergency brake on balances, not on administration.
contract SettlementTokenPauseTest is SettlementTokenBase {
    uint256 internal constant AMOUNT = 100e6;

    function setUp() public override {
        super.setUp();
        // Pre-pause setup: an allowance and a minter balance, so the paused calls
        // below can only fail because of the pause.
        vm.prank(alice);
        token.approve(bob, AMOUNT);
        vm.prank(alice);
        token.transfer(minter, AMOUNT);

        vm.expectEmit(false, false, false, true, address(token));
        emit Pausable.Paused(pauser);
        vm.prank(pauser);
        token.pause();
    }

    bytes internal enforcedPause = abi.encodeWithSelector(Pausable.EnforcedPause.selector);

    // ---- balance movements stop ----------------------------------------------

    /// @notice TOK-07: pause() sets the flag and emits Paused(pauser) (asserted in setUp).
    function test_Pause_SetsPausedFlag() public view {
        assertTrue(token.paused());
    }

    /// @notice TOK-07
    function test_RevertWhen_TransferWhilePaused() public {
        State memory before = _state();
        vm.prank(alice);
        vm.expectRevert(enforcedPause);
        token.transfer(bob, AMOUNT);
        _assertUnchanged(before);
    }

    /// @notice TOK-07: the allowance path is paused too, and the allowance survives.
    function test_RevertWhen_TransferFromWhilePaused() public {
        State memory before = _state();
        vm.prank(bob);
        vm.expectRevert(enforcedPause);
        token.transferFrom(alice, bob, AMOUNT);
        _assertUnchanged(before);
        assertEq(token.allowance(alice, bob), AMOUNT);
    }

    /// @notice TOK-07: no new supply is issued during an incident.
    function test_RevertWhen_MintWhilePaused() public {
        State memory before = _state();
        vm.prank(minter);
        vm.expectRevert(enforcedPause);
        token.mint(bob, AMOUNT);
        _assertUnchanged(before);
    }

    /// @notice TOK-07: no supply is destroyed during an incident.
    function test_RevertWhen_BurnWhilePaused() public {
        State memory before = _state();
        vm.prank(minter);
        vm.expectRevert(enforcedPause);
        token.burn(AMOUNT);
        _assertUnchanged(before);
    }

    /// @notice TOK-07: pause is checked before the allowlist, so a paused token reports
    ///         EnforcedPause for every movement and doesn't reveal allowlist status.
    function test_RevertWhen_PausedAndRecipientNotAllowlisted_ReportsPause() public {
        vm.prank(alice);
        vm.expectRevert(enforcedPause);
        token.transfer(mallory, AMOUNT);
    }

    /// @notice TOK-07: pausing twice is an error, not a silent no-op.
    function test_RevertWhen_PauseWhileAlreadyPaused() public {
        vm.prank(pauser);
        vm.expectRevert(enforcedPause);
        token.pause();
    }

    // ---- administration keeps working ------------------------------------------

    /// @notice TOK-07: compliance can still list and delist accounts mid-incident.
    function test_SetAllowlisted_WorksWhilePaused() public {
        vm.startPrank(admin);
        token.setAllowlisted(mallory, true);
        token.setAllowlisted(alice, false);
        vm.stopPrank();

        assertTrue(token.isAllowlisted(mallory));
        assertFalse(token.isAllowlisted(alice));
        assertTrue(token.paused());
    }

    /// @notice TOK-07: a compromised minter can be revoked while the token is paused.
    function test_RevokeRole_WorksWhilePaused() public {
        vm.prank(admin);
        token.revokeRole(MINTER_ROLE, minter);

        assertFalse(token.hasRole(MINTER_ROLE, minter));
        assertTrue(token.paused());
    }

    /// @notice TOK-07: a replacement role holder can be appointed while paused.
    function test_GrantRole_WorksWhilePaused() public {
        vm.prank(admin);
        token.grantRole(PAUSER_ROLE, bob);

        assertTrue(token.hasRole(PAUSER_ROLE, bob));
        assertTrue(token.paused());
    }

    /// @notice TOK-07: role holders can still renounce while paused.
    function test_RenounceRole_WorksWhilePaused() public {
        vm.prank(minter);
        token.renounceRole(MINTER_ROLE, minter);
        assertFalse(token.hasRole(MINTER_ROLE, minter));
    }

    /// @notice TOK-07: approvals don't move balances, so they stay available.
    function test_Approve_WorksWhilePaused() public {
        vm.prank(alice);
        token.approve(bob, AMOUNT * 3);
        assertEq(token.allowance(alice, bob), AMOUNT * 3);
    }

    /// @notice TOK-07: the brake can be released, by the pauser only (matrix covers others).
    function test_Unpause_RestoresTransfers() public {
        vm.expectEmit(false, false, false, true, address(token));
        emit Pausable.Unpaused(pauser);
        vm.prank(pauser);
        token.unpause();
        assertFalse(token.paused());

        vm.prank(alice);
        token.transfer(bob, AMOUNT);
        assertEq(token.balanceOf(bob), AMOUNT);
    }

    /// @notice TOK-07: unpausing an unpaused token is an error.
    function test_RevertWhen_UnpauseWhileNotPaused() public {
        vm.startPrank(pauser);
        token.unpause();
        vm.expectRevert(abi.encodeWithSelector(Pausable.ExpectedPause.selector));
        token.unpause();
        vm.stopPrank();
    }

    /// @notice TOK-07: pausing confers no extra power; the admin still can't mint.
    function test_RevertWhen_AdminMintsWhilePaused() public {
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControl.AccessControlUnauthorizedAccount.selector, admin, MINTER_ROLE
            )
        );
        token.mint(bob, AMOUNT);
    }
}
