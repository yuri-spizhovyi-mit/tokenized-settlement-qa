// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {VaultBase} from "./Vault.sol";

// Fixed versions of VulnerableVault, kept next to it so the regression tests run the
// same exploit against all three. Test fixtures: not part of the product either.

/// @notice Fix 1: checks-effects-interactions. The balance is zeroed before the call, so
///         a re-entrant withdraw finds nothing to withdraw.
contract CeiVault is VaultBase {
    function withdraw() external override {
        uint256 amount = balanceOf[msg.sender];
        if (amount == 0) revert NothingToWithdraw();

        balanceOf[msg.sender] = 0;
        emit Withdrawn(msg.sender, amount);

        (bool ok,) = msg.sender.call{value: amount}("");
        if (!ok) revert TransferFailed();
    }
}

/// @notice Fix 2: nonReentrant. The original, call-before-effect order is kept on
///         purpose, so this proves the guard alone stops the attack.
contract GuardedVault is VaultBase, ReentrancyGuard {
    function withdraw() external override nonReentrant {
        uint256 amount = balanceOf[msg.sender];
        if (amount == 0) revert NothingToWithdraw();

        // forge-lint: disable-next-line(reentrancy-eth) false positive: nonReentrant guards it
        (bool ok,) = msg.sender.call{value: amount}("");
        if (!ok) revert TransferFailed();

        balanceOf[msg.sender] = 0;
        // forge-lint: disable-next-line(reentrancy-events) false positive: nonReentrant guards it
        emit Withdrawn(msg.sender, amount);
    }
}
