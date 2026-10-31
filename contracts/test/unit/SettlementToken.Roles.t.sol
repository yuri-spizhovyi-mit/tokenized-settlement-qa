// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {SettlementTokenBase} from "./SettlementTokenBase.t.sol";
import {SettlementToken} from "../../src/SettlementToken.sol";

/// @notice TOK-02, TOK-03, TOK-04, TOK-06: who may call what, and revocation timing.
contract SettlementTokenRolesTest is SettlementTokenBase {
    enum Fn {
        Mint,
        Burn,
        Pause,
        Unpause,
        SetAllowlisted,
        GrantRole,
        RevokeRole
    }

    uint256 internal constant FN_COUNT = 7;
    uint256 internal constant AMOUNT = 5e6;

    string[FN_COUNT] internal fnNames =
        ["mint", "burn", "pause", "unpause", "setAllowlisted", "grantRole", "revokeRole"];

    // ---- the matrix --------------------------------------------------------

    /// @notice TOK-02/03/04: every privileged function x every role (admin, minter,
    ///         pauser, compliance), plus a role-less allowlisted holder and an
    ///         arbitrary address.
    /// @dev Each cell runs from the same snapshot. A caller holding the required role
    ///      must succeed and produce the function's effect. Every other caller must
    ///      revert with AccessControlUnauthorizedAccount(caller, requiredRole) and
    ///      leave all token state untouched.
    ///
    ///                    admin   minter  pauser  compliance  alice   mallory
    ///      mint            -       OK      -       -           -       -
    ///      burn            -       OK      -       -           -       -
    ///      pause           -       -       OK      -           -       -
    ///      unpause         -       -       OK      -           -       -
    ///      setAllowlisted  -       -       -       OK          -       -
    ///      grantRole       OK      -       -       -           -       -
    ///      revokeRole      OK      -       -       -           -       -
    ///
    ///      The admin cannot touch the allowlist and compliance cannot grant roles:
    ///      governance and compliance are segregated duties.
    function test_RoleMatrix_OnlyRequiredRoleCanCallEachPrivilegedFunction() public {
        address[6] memory callers = [admin, minter, pauser, compliance, alice, mallory];
        uint256 allowedCells;

        for (uint256 f; f < FN_COUNT; ++f) {
            for (uint256 c; c < callers.length; ++c) {
                uint256 snap = vm.snapshotState();
                allowedCells += _runCell(Fn(f), callers[c]) ? 1 : 0;
                vm.revertToState(snap);
            }
        }

        // Exactly one caller per function may succeed. Catches a role that was
        // accidentally granted twice, or a function that lost its modifier.
        assertEq(allowedCells, FN_COUNT, "allowed cell count");
    }

    /// @dev Runs one (function, caller) cell. Returns true if the call was allowed.
    function _runCell(Fn fn, address caller) internal returns (bool allowed) {
        bytes32 role = _requiredRole(fn);
        string memory cell = string.concat(fnNames[uint256(fn)], " by ", vm.getLabel(caller));

        _arrange(fn);
        State memory before = _state();
        uint256 callerBalanceBefore = token.balanceOf(caller);

        vm.prank(caller);
        (bool ok, bytes memory ret) = address(token).call(_calldata(fn));

        allowed = token.hasRole(role, caller);
        if (allowed) {
            assertTrue(ok, string.concat(cell, ": should succeed"));
            _assertEffect(fn, caller, before, cell);
        } else {
            assertFalse(ok, string.concat(cell, ": should revert"));
            assertEq(ret, _unauthorized(caller, role), string.concat(cell, ": wrong error"));
            _assertUnchanged(before);
            assertEq(token.balanceOf(caller), callerBalanceBefore, string.concat(cell, ": balance"));
            _assertNoRoleOrAllowlistChange(cell);
        }
    }

    function _requiredRole(Fn fn) internal view returns (bytes32) {
        if (fn == Fn.Mint || fn == Fn.Burn) return MINTER_ROLE;
        if (fn == Fn.Pause || fn == Fn.Unpause) return PAUSER_ROLE;
        if (fn == Fn.SetAllowlisted) return ALLOWLIST_ROLE;
        return DEFAULT_ADMIN_ROLE;
    }

    /// @dev Puts the token in the state where the function could succeed, so a revert
    ///      can only come from access control.
    function _arrange(Fn fn) internal {
        if (fn == Fn.Burn) {
            // Every caller gets a balance, so "nothing to burn" can't mask a missing check.
            address[5] memory holders = [admin, pauser, compliance, mallory, minter];
            for (uint256 i; i < holders.length; ++i) {
                vm.prank(compliance);
                token.setAllowlisted(holders[i], true);
                vm.prank(minter);
                token.mint(holders[i], AMOUNT);
            }
        } else if (fn == Fn.Unpause) {
            vm.prank(pauser);
            token.pause();
        }
    }

    function _calldata(Fn fn) internal view returns (bytes memory) {
        if (fn == Fn.Mint) return abi.encodeCall(SettlementToken.mint, (bob, AMOUNT));
        if (fn == Fn.Burn) return abi.encodeCall(SettlementToken.burn, (AMOUNT));
        if (fn == Fn.Pause) return abi.encodeCall(SettlementToken.pause, ());
        if (fn == Fn.Unpause) return abi.encodeCall(SettlementToken.unpause, ());
        if (fn == Fn.SetAllowlisted) {
            return abi.encodeCall(SettlementToken.setAllowlisted, (mallory, true));
        }
        if (fn == Fn.GrantRole) {
            return abi.encodeCall(IAccessControl.grantRole, (MINTER_ROLE, mallory));
        }
        return abi.encodeCall(IAccessControl.revokeRole, (MINTER_ROLE, minter));
    }

    function _assertEffect(Fn fn, address caller, State memory before, string memory cell)
        internal
        view
    {
        if (fn == Fn.Mint) {
            assertEq(token.balanceOf(bob), before.bob + AMOUNT, cell);
            assertEq(token.totalSupply(), before.totalSupply + AMOUNT, cell);
        } else if (fn == Fn.Burn) {
            assertEq(token.balanceOf(caller), before.minter - AMOUNT, cell);
            assertEq(token.totalSupply(), before.totalSupply - AMOUNT, cell);
        } else if (fn == Fn.Pause) {
            assertTrue(token.paused(), cell);
        } else if (fn == Fn.Unpause) {
            assertFalse(token.paused(), cell);
        } else if (fn == Fn.SetAllowlisted) {
            assertTrue(token.isAllowlisted(mallory), cell);
        } else if (fn == Fn.GrantRole) {
            assertTrue(token.hasRole(MINTER_ROLE, mallory), cell);
        } else {
            assertFalse(token.hasRole(MINTER_ROLE, minter), cell);
        }
    }

    /// @dev The three non-balance effects the matrix exercises must not have happened.
    function _assertNoRoleOrAllowlistChange(string memory cell) internal view {
        assertTrue(token.hasRole(MINTER_ROLE, minter), string.concat(cell, ": minter revoked"));
        assertFalse(token.hasRole(MINTER_ROLE, mallory), string.concat(cell, ": mallory granted"));
    }

    // ---- revocation and grants take effect immediately (TOK-06) --------------

    /// @notice TOK-06: a revoked minter's very next mint fails, in the same block.
    function test_RevertWhen_MinterMintsAfterRevocation() public {
        vm.prank(admin);
        token.revokeRole(MINTER_ROLE, minter);
        State memory before = _state();

        vm.prank(minter);
        vm.expectRevert(_unauthorized(minter, MINTER_ROLE));
        token.mint(bob, AMOUNT);

        _assertUnchanged(before);
        assertFalse(token.hasRole(MINTER_ROLE, minter));
    }

    /// @notice TOK-06: a revoked minter can no longer burn its own balance either.
    function test_RevertWhen_MinterBurnsAfterRevocation() public {
        vm.prank(minter);
        token.mint(minter, AMOUNT);
        vm.prank(admin);
        token.revokeRole(MINTER_ROLE, minter);
        State memory before = _state();

        vm.prank(minter);
        vm.expectRevert(_unauthorized(minter, MINTER_ROLE));
        token.burn(AMOUNT);

        _assertUnchanged(before);
    }

    /// @notice TOK-06: a revoked pauser cannot pause.
    function test_RevertWhen_PauserPausesAfterRevocation() public {
        vm.prank(admin);
        token.revokeRole(PAUSER_ROLE, pauser);

        vm.prank(pauser);
        vm.expectRevert(_unauthorized(pauser, PAUSER_ROLE));
        token.pause();

        assertFalse(token.paused());
    }

    /// @notice TOK-06: a pauser revoked mid-incident cannot lift the pause it set.
    function test_RevertWhen_PauserUnpausesAfterRevocation() public {
        vm.prank(pauser);
        token.pause();
        vm.prank(admin);
        token.revokeRole(PAUSER_ROLE, pauser);

        vm.prank(pauser);
        vm.expectRevert(_unauthorized(pauser, PAUSER_ROLE));
        token.unpause();

        assertTrue(token.paused(), "token must stay paused");
    }

    /// @notice TOK-06: an admin that renounces can no longer grant or revoke roles.
    /// @dev Since issue #3 (SEC-05), renouncing takes a scheduled transfer to address(0)
    ///      and the full ADMIN_DELAY; a one-call renounce reverts (see the AdminRules
    ///      suite). Existing role holders keep working after governance is given up.
    function test_RevertWhen_AdminActsAfterRenouncing() public {
        vm.prank(admin);
        token.beginDefaultAdminTransfer(address(0));
        (, uint48 schedule) = token.pendingDefaultAdmin();
        vm.warp(uint256(schedule) + 1);
        vm.prank(admin);
        token.renounceRole(DEFAULT_ADMIN_ROLE, admin);
        assertFalse(token.hasRole(DEFAULT_ADMIN_ROLE, admin));

        vm.startPrank(admin);
        vm.expectRevert(_unauthorized(admin, DEFAULT_ADMIN_ROLE));
        token.grantRole(MINTER_ROLE, bob);
        vm.expectRevert(_unauthorized(admin, DEFAULT_ADMIN_ROLE));
        token.revokeRole(MINTER_ROLE, minter);
        vm.stopPrank();

        assertFalse(token.hasRole(MINTER_ROLE, bob));
        assertTrue(token.hasRole(MINTER_ROLE, minter));

        // Compliance is a separate duty and is unaffected by the loss of governance.
        vm.prank(compliance);
        token.setAllowlisted(mallory, true);
        assertTrue(token.isAllowlisted(mallory));
    }

    /// @notice TOK-06: a revoked compliance officer's next allowlist change fails.
    function test_RevertWhen_ComplianceSetsAllowlistAfterRevocation() public {
        vm.prank(admin);
        token.revokeRole(ALLOWLIST_ROLE, compliance);

        vm.prank(compliance);
        vm.expectRevert(_unauthorized(compliance, ALLOWLIST_ROLE));
        token.setAllowlisted(mallory, true);

        assertFalse(token.isAllowlisted(mallory));
        assertFalse(token.hasRole(ALLOWLIST_ROLE, compliance));
    }

    /// @notice TOK-04: the admin cannot change the allowlist on its own authority.
    /// @dev Segregation of duties is enforced on-chain, but governance can still
    ///      appoint itself. The RoleGranted event makes that self-grant auditable,
    ///      and it is a deliberate, two-step act rather than a side power of admin.
    function test_RevertWhen_AdminSetsAllowlistWithoutAllowlistRole() public {
        vm.prank(admin);
        vm.expectRevert(_unauthorized(admin, ALLOWLIST_ROLE));
        token.setAllowlisted(mallory, true);
        assertFalse(token.isAllowlisted(mallory));

        vm.expectEmit(true, true, true, true, address(token));
        emit IAccessControl.RoleGranted(ALLOWLIST_ROLE, admin, admin);
        vm.startPrank(admin);
        token.grantRole(ALLOWLIST_ROLE, admin);
        token.setAllowlisted(mallory, true);
        vm.stopPrank();
        assertTrue(token.isAllowlisted(mallory));
    }

    /// @notice TOK-06: a new grant is usable in the same block, and emits RoleGranted.
    function test_GrantRole_TakesEffectImmediately() public {
        vm.expectEmit(true, true, true, true, address(token));
        emit IAccessControl.RoleGranted(MINTER_ROLE, bob, admin);
        vm.prank(admin);
        token.grantRole(MINTER_ROLE, bob);

        vm.prank(bob);
        token.mint(alice, AMOUNT);

        assertTrue(token.hasRole(MINTER_ROLE, bob));
        assertEq(token.balanceOf(alice), ALICE_START + AMOUNT);
        assertEq(token.totalSupply(), ALICE_START + AMOUNT);
    }

    /// @notice TOK-06: revocation emits RoleRevoked naming the admin who did it.
    function test_RevokeRole_EmitsRoleRevoked() public {
        vm.expectEmit(true, true, true, true, address(token));
        emit IAccessControl.RoleRevoked(MINTER_ROLE, minter, admin);
        vm.prank(admin);
        token.revokeRole(MINTER_ROLE, minter);
    }

    /// @notice TOK-06: any role holder can give up its own role.
    function test_RenounceRole_RemovesCallersRole() public {
        vm.prank(minter);
        token.renounceRole(MINTER_ROLE, minter);
        assertFalse(token.hasRole(MINTER_ROLE, minter));
    }

    /// @notice TOK-04: nobody can renounce a role on someone else's behalf.
    function test_RevertWhen_RenounceConfirmationIsNotCaller() public {
        vm.prank(mallory);
        vm.expectRevert(IAccessControl.AccessControlBadConfirmation.selector);
        token.renounceRole(MINTER_ROLE, minter);

        assertTrue(token.hasRole(MINTER_ROLE, minter));
    }
}
