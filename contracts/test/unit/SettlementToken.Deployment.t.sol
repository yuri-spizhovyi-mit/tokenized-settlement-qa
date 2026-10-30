// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {SettlementTokenBase} from "./SettlementTokenBase.t.sol";
import {SettlementToken} from "../../src/SettlementToken.sol";

/// @notice TOK-01: the token deploys into a known, minimal-privilege state.
contract SettlementTokenDeploymentTest is SettlementTokenBase {
    SettlementToken internal fresh;

    function setUp() public override {
        super.setUp();
        fresh = new SettlementToken(admin);
    }

    /// @notice TOK-01: metadata matches a USDC-style settlement asset.
    function test_Deploy_SetsMetadata() public view {
        assertEq(fresh.name(), "Settlement Token");
        assertEq(fresh.symbol(), "STK");
        assertEq(fresh.decimals(), 6);
    }

    /// @notice TOK-01: nothing exists until a minter mints, and transfers are live.
    function test_Deploy_StartsEmptyAndUnpaused() public view {
        assertEq(fresh.totalSupply(), 0);
        assertEq(fresh.balanceOf(admin), 0);
        assertFalse(fresh.paused());
    }

    /// @notice TOK-01: the admin gets DEFAULT_ADMIN_ROLE and nothing else (least privilege).
    function test_Deploy_GrantsOnlyAdminRole() public view {
        assertTrue(fresh.hasRole(DEFAULT_ADMIN_ROLE, admin));
        assertFalse(fresh.hasRole(MINTER_ROLE, admin));
        assertFalse(fresh.hasRole(PAUSER_ROLE, admin));
        assertFalse(fresh.isAllowlisted(admin));
        assertFalse(fresh.hasRole(DEFAULT_ADMIN_ROLE, address(this)), "deployer kept admin");
    }

    /// @notice TOK-01: every role is administered by DEFAULT_ADMIN_ROLE.
    function test_Deploy_RoleAdminsAreDefaultAdmin() public view {
        assertEq(fresh.getRoleAdmin(MINTER_ROLE), DEFAULT_ADMIN_ROLE);
        assertEq(fresh.getRoleAdmin(PAUSER_ROLE), DEFAULT_ADMIN_ROLE);
        assertEq(fresh.getRoleAdmin(DEFAULT_ADMIN_ROLE), DEFAULT_ADMIN_ROLE);
    }

    /// @notice TOK-01: role identifiers are the conventional keccak256 hashes.
    function test_Deploy_RoleIdsAreKeccakOfNames() public view {
        assertEq(fresh.MINTER_ROLE(), keccak256("MINTER_ROLE"));
        assertEq(fresh.PAUSER_ROLE(), keccak256("PAUSER_ROLE"));
    }

    /// @notice TOK-01: a zero admin would leave the token with no one able to grant roles.
    function test_RevertWhen_AdminIsZeroAddress() public {
        vm.expectRevert(abi.encodeWithSelector(SettlementToken.InvalidAdmin.selector, address(0)));
        new SettlementToken(address(0));
    }
}
