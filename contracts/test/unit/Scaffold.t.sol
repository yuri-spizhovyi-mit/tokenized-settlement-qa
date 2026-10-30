// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";

/// @dev Throwaway token used only to prove the toolchain and remappings work.
///      Deleted in PR 2 when SettlementToken lands.
contract ScaffoldToken is ERC20 {
    constructor() ERC20("Scaffold", "SCF") {
        _mint(msg.sender, 1_000e18);
    }
}

/// @notice Toolchain smoke test: forge-std and OpenZeppelin 5.x compile and link.
contract ScaffoldTest is Test {
    ScaffoldToken internal token;
    address internal alice = makeAddr("alice");

    function setUp() public {
        token = new ScaffoldToken();
    }

    function test_Setup_MintsInitialSupplyToDeployer() public view {
        assertEq(token.totalSupply(), 1_000e18);
        assertEq(token.balanceOf(address(this)), 1_000e18);
    }

    function test_RevertWhen_TransferExceedsBalance() public {
        vm.prank(alice);
        vm.expectRevert(
            abi.encodeWithSelector(IERC20Errors.ERC20InsufficientBalance.selector, alice, 0, 1)
        );
        token.transfer(address(this), 1);
    }
}
