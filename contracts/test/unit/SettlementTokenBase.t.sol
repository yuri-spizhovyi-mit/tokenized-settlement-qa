// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {SettlementToken} from "../../src/SettlementToken.sol";

/// @notice Shared fixture for the SettlementToken unit suites.
/// @dev Requirements covered by the suites (IDs are used in test NatSpec and the RTM):
///      TOK-01  Deployment: admin holds DEFAULT_ADMIN_ROLE only; no supply; not paused.
///      TOK-02  Only MINTER_ROLE can mint and burn.
///      TOK-03  Only PAUSER_ROLE can pause and unpause.
///      TOK-04  Only DEFAULT_ADMIN_ROLE can change the allowlist and grant/revoke roles.
///      TOK-05  Every balance movement requires the sender, the recipient and (for
///              transferFrom) the spender to be allowlisted. transferFrom is no bypass.
///      TOK-06  Revoking a role or delisting an account takes effect immediately.
///      TOK-07  Pause stops every balance movement; admin functions keep working.
abstract contract SettlementTokenBase is Test {
    SettlementToken internal token;

    address internal admin = makeAddr("admin");
    address internal minter = makeAddr("minter");
    address internal pauser = makeAddr("pauser");
    address internal alice = makeAddr("alice"); // allowlisted holder
    address internal bob = makeAddr("bob"); // allowlisted holder
    address internal mallory = makeAddr("mallory"); // never allowlisted, holds no role

    bytes32 internal MINTER_ROLE;
    bytes32 internal PAUSER_ROLE;
    bytes32 internal constant DEFAULT_ADMIN_ROLE = 0x00;

    uint256 internal constant ALICE_START = 1_000e6;

    function setUp() public virtual {
        token = new SettlementToken(admin);
        MINTER_ROLE = token.MINTER_ROLE();
        PAUSER_ROLE = token.PAUSER_ROLE();

        vm.startPrank(admin);
        token.grantRole(MINTER_ROLE, minter);
        token.grantRole(PAUSER_ROLE, pauser);
        token.setAllowlisted(alice, true);
        token.setAllowlisted(bob, true);
        token.setAllowlisted(minter, true);
        vm.stopPrank();

        vm.prank(minter);
        token.mint(alice, ALICE_START);
    }

    // ---- helpers -----------------------------------------------------------

    function _unauthorized(address account, bytes32 role) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(
            IAccessControl.AccessControlUnauthorizedAccount.selector, account, role
        );
    }

    function _notAllowlisted(address account) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(SettlementToken.AccountNotAllowlisted.selector, account);
    }

    /// @dev Snapshot of every piece of token state a test may need to prove unchanged.
    struct State {
        uint256 totalSupply;
        uint256 alice;
        uint256 bob;
        uint256 mallory;
        uint256 minter;
        bool paused;
    }

    function _state() internal view returns (State memory s) {
        s.totalSupply = token.totalSupply();
        s.alice = token.balanceOf(alice);
        s.bob = token.balanceOf(bob);
        s.mallory = token.balanceOf(mallory);
        s.minter = token.balanceOf(minter);
        s.paused = token.paused();
    }

    function _assertUnchanged(State memory before) internal view {
        State memory nowS = _state();
        assertEq(nowS.totalSupply, before.totalSupply, "totalSupply changed");
        assertEq(nowS.alice, before.alice, "alice balance changed");
        assertEq(nowS.bob, before.bob, "bob balance changed");
        assertEq(nowS.mallory, before.mallory, "mallory balance changed");
        assertEq(nowS.minter, before.minter, "minter balance changed");
        assertEq(nowS.paused, before.paused, "paused flag changed");
    }
}
