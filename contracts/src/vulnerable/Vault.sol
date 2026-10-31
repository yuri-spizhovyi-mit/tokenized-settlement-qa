// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  DELIBERATELY VULNERABLE. TEST FIXTURE ONLY. NEVER DEPLOY.                   ║
// ║                                                                              ║
// ║  `VulnerableVault.withdraw` sends ETH before it zeroes the balance, so a     ║
// ║  contract receiver can re-enter and withdraw again (book Ch 9.1, SWC-107).   ║
// ║  It exists so test/security can prove the exploit and the two fixes below.   ║
// ║  The script/ folder has no deploy script for it, and CI's Slither gate       ║
// ║  excludes this file by path; see docs/slither-triage.md.                     ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

/// @notice Minimal ETH vault shared by the vulnerable and fixed versions.
// forge-lint: disable-next-line(locked-ether) false positive: subclasses' withdraw sends ETH out
abstract contract VaultBase {
    mapping(address account => uint256) public balanceOf;

    event Deposited(address indexed account, uint256 amount);
    event Withdrawn(address indexed account, uint256 amount);

    error NothingToWithdraw();
    error TransferFailed();

    function deposit() external payable {
        balanceOf[msg.sender] += msg.value;
        emit Deposited(msg.sender, msg.value);
    }

    function withdraw() external virtual;
}

/// @notice DELIBERATELY VULNERABLE. Interaction before effect: re-entrancy drains it.
contract VulnerableVault is VaultBase {
    function withdraw() external override {
        uint256 amount = balanceOf[msg.sender];
        if (amount == 0) revert NothingToWithdraw();

        // BUG: the external call happens while balanceOf[msg.sender] is still `amount`.
        // forge-lint: disable-next-line(reentrancy-eth) INTENDED: this is the exploit under test
        (bool ok,) = msg.sender.call{value: amount}("");
        if (!ok) revert TransferFailed();

        balanceOf[msg.sender] = 0;
        // forge-lint: disable-next-line(reentrancy-events) INTENDED: part of the exploit fixture
        emit Withdrawn(msg.sender, amount);
    }
}
