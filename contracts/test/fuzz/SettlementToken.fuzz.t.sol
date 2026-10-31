// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {SettlementTokenBase} from "../unit/SettlementTokenBase.t.sol";

/// @notice Property-based tests for SettlementToken. Inputs are shaped with `bound()`;
///         `vm.assume` is used only to exclude specific addresses.
/// @dev Requirements (IDs are used in test NatSpec and the RTM):
///      PROP-05  For any amount and any allowlisted recipient, a transfer conserves
///               totalSupply and the sum of the two balances involved.
///      PROP-06  An address without the required role can never mint, pause, or change
///               the allowlist, whatever the arguments.
contract SettlementTokenFuzzTest is SettlementTokenBase {
    /// @notice PROP-05: a transfer moves value, it never creates or destroys it. Covers
    ///         self-transfers and zero-value transfers too.
    function testFuzz_Transfer_ConservesTotalSupply(uint256 amount, address to) public {
        vm.assume(to != address(0));
        vm.prank(compliance);
        token.setAllowlisted(to, true);
        amount = bound(amount, 0, token.balanceOf(alice));

        uint256 supplyBefore = token.totalSupply();
        uint256 aliceBefore = token.balanceOf(alice);
        uint256 toBefore = token.balanceOf(to);

        vm.prank(alice);
        token.transfer(to, amount);

        assertEq(token.totalSupply(), supplyBefore, "totalSupply changed");
        if (to == alice) {
            assertEq(token.balanceOf(alice), aliceBefore, "self-transfer changed balance");
        } else {
            assertEq(token.balanceOf(alice), aliceBefore - amount, "sender");
            assertEq(token.balanceOf(to), toBefore + amount, "recipient");
        }
    }

    /// @notice PROP-06: no address without MINTER_ROLE can mint, to anyone, any amount.
    function testFuzz_RevertWhen_CallerWithoutMinterRoleMints(
        address caller,
        address to,
        uint256 amount
    ) public {
        vm.assume(caller != minter);
        uint256 supplyBefore = token.totalSupply();

        vm.prank(caller);
        vm.expectRevert(_unauthorized(caller, MINTER_ROLE));
        token.mint(to, amount);

        assertEq(token.totalSupply(), supplyBefore);
    }

    /// @notice PROP-06: no address without PAUSER_ROLE can pause.
    function testFuzz_RevertWhen_CallerWithoutPauserRolePauses(address caller) public {
        vm.assume(caller != pauser);

        vm.prank(caller);
        vm.expectRevert(_unauthorized(caller, PAUSER_ROLE));
        token.pause();

        assertFalse(token.paused());
    }

    /// @notice PROP-06: no address without ALLOWLIST_ROLE can list or delist anyone. That
    ///         includes the admin, which holds DEFAULT_ADMIN_ROLE but not ALLOWLIST_ROLE.
    function testFuzz_RevertWhen_CallerWithoutAllowlistRoleSetsAllowlist(
        address caller,
        address account,
        bool allowed
    ) public {
        vm.assume(caller != compliance);
        bool before = token.isAllowlisted(account);

        vm.prank(caller);
        vm.expectRevert(_unauthorized(caller, ALLOWLIST_ROLE));
        token.setAllowlisted(account, allowed);

        assertEq(token.isAllowlisted(account), before);
    }
}
