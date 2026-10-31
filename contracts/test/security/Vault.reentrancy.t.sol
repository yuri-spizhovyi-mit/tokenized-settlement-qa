// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {VaultBase, VulnerableVault} from "../../src/vulnerable/Vault.sol";
import {CeiVault, GuardedVault} from "../../src/vulnerable/FixedVaults.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {ReentrancyAttacker} from "./ReentrancyAttacker.sol";

/// @notice Re-entrancy exploit and regressions (book Ch 9.1, SWC-107).
/// @dev Requirements (IDs are used in test NatSpec and the RTM):
///      SEC-01  The exploit is real: against VulnerableVault, an attacker with a 1 ETH
///              stake drains every honest depositor's ETH.
///      SEC-02  Fix 1 (checks-effects-interactions) stops the same attack: the
///              re-entrant withdraw reverts, and so does the whole transaction.
///      SEC-03  Fix 2 (nonReentrant), with the vulnerable call order kept, stops the
///              same attack with ReentrancyGuardReentrantCall.
///      SEC-04  Both fixes still serve honest withdrawals in full.
contract VaultReentrancyTest is Test {
    uint256 internal constant HONEST_DEPOSITS = 10 ether; // 5 depositors x 2 ETH
    uint256 internal constant STAKE = 1 ether;

    address internal attackerEoa = makeAddr("attackerEoa");

    function _seed(VaultBase vault) internal {
        for (uint256 i; i < 5; ++i) {
            address depositor = makeAddr(string.concat("depositor", vm.toString(i)));
            vm.deal(depositor, 2 ether);
            vm.prank(depositor);
            vault.deposit{value: 2 ether}();
        }
        assertEq(address(vault).balance, HONEST_DEPOSITS);
    }

    function _attack(VaultBase vault) internal returns (ReentrancyAttacker attacker) {
        vm.deal(attackerEoa, STAKE);
        vm.prank(attackerEoa);
        attacker = new ReentrancyAttacker(vault);
        vm.prank(attackerEoa);
        attacker.attack{value: STAKE}(type(uint256).max);
    }

    // ---- SEC-01: the exploit ---------------------------------------------------------

    /// @notice SEC-01: a 1 ETH stake drains all 10 ETH of honest deposits.
    function test_Exploit_ReentrancyDrainsVulnerableVault() public {
        VulnerableVault vault = new VulnerableVault();
        _seed(vault);

        ReentrancyAttacker attacker = _attack(vault);

        assertEq(address(vault).balance, 0, "vault not drained");
        assertEq(address(attacker).balance, HONEST_DEPOSITS + STAKE, "attacker haul");
        assertEq(attacker.reentries(), 10, "one re-entry per honest ether");
        // The ledger still says every honest depositor is owed 2 ETH: the vault is
        // insolvent, and an honest withdraw now fails.
        address victim = makeAddr("depositor0");
        assertEq(vault.balanceOf(victim), 2 ether);
        vm.prank(victim);
        vm.expectRevert(VaultBase.TransferFailed.selector);
        vault.withdraw();
    }

    // ---- SEC-02 / SEC-03: regressions against the fixes -------------------------------

    /// @notice SEC-02: with the balance zeroed before the call, the re-entrant withdraw
    ///         hits NothingToWithdraw; the receive hook reverts, so the outer call fails
    ///         with TransferFailed and nothing moves.
    function test_RevertWhen_ReentrancyAgainstCeiVault() public {
        CeiVault vault = new CeiVault();
        _seed(vault);
        vm.deal(attackerEoa, STAKE);
        vm.prank(attackerEoa);
        ReentrancyAttacker attacker = new ReentrancyAttacker(vault);

        vm.prank(attackerEoa);
        vm.expectRevert(VaultBase.TransferFailed.selector);
        attacker.attack{value: STAKE}(type(uint256).max);

        assertEq(address(vault).balance, HONEST_DEPOSITS, "honest funds moved");
        assertEq(address(attacker).balance, 0);
        assertEq(attackerEoa.balance, STAKE, "attacker stake should be returned by revert");
    }

    /// @notice SEC-03: the guard alone, with the vulnerable call-before-effect order,
    ///         stops the attack. Asserted at the re-entrant call itself, so a pass can't
    ///         come from some other revert.
    function test_RevertWhen_ReentrancyAgainstGuardedVault() public {
        GuardedVault vault = new GuardedVault();
        _seed(vault);
        vm.deal(attackerEoa, STAKE);
        vm.prank(attackerEoa);
        ReentrancyAttacker attacker = new ReentrancyAttacker(vault);

        // The inner withdraw reverts with the guard's error; the attacker's receive
        // bubbles it, and the vault reports the failed send as TransferFailed.
        vm.expectRevert(VaultBase.TransferFailed.selector);
        vm.prank(attackerEoa);
        attacker.attack{value: STAKE}(type(uint256).max);

        assertEq(address(vault).balance, HONEST_DEPOSITS, "honest funds moved");
        assertEq(address(attacker).balance, 0);
    }

    /// @notice SEC-03: the re-entrant call into GuardedVault fails with exactly the
    ///         guard's error, not some unrelated revert. A probe catches the inner revert
    ///         instead of bubbling it, so its data can be asserted exactly.
    function test_RevertWhen_ReentrantCallHitsGuard() public {
        GuardedVault vault = new GuardedVault();
        GuardProbe probe = new GuardProbe(vault);
        vm.deal(address(probe), STAKE);

        probe.depositAndWithdraw();

        assertEq(
            probe.innerRevert(),
            abi.encodeWithSelector(ReentrancyGuard.ReentrancyGuardReentrantCall.selector),
            "inner withdraw must revert with the guard's error"
        );
        assertEq(address(probe).balance, STAKE, "outer withdraw still pays exactly once");
        assertEq(vault.balanceOf(address(probe)), 0);
    }

    // ---- SEC-04: fixes still work for honest users -----------------------------------

    /// @notice SEC-04: both fixed vaults pay every honest depositor in full.
    function test_FixedVaults_ServeHonestWithdrawals() public {
        VaultBase[2] memory vaults = [VaultBase(new CeiVault()), VaultBase(new GuardedVault())];
        for (uint256 v; v < vaults.length; ++v) {
            _seed(vaults[v]);
            for (uint256 i; i < 5; ++i) {
                address depositor = makeAddr(string.concat("depositor", vm.toString(i)));
                uint256 before = depositor.balance;
                vm.prank(depositor);
                vaults[v].withdraw();
                assertEq(depositor.balance, before + 2 ether);
                assertEq(vaults[v].balanceOf(depositor), 0);
            }
            assertEq(address(vaults[v]).balance, 0);
        }
    }
}

/// @dev Records the revert data of a re-entrant withdraw instead of bubbling it.
contract GuardProbe {
    GuardedVault internal immutable vault;
    bytes public innerRevert;
    bool internal entered;

    constructor(GuardedVault vault_) {
        vault = vault_;
    }

    function depositAndWithdraw() external {
        vault.deposit{value: address(this).balance}();
        vault.withdraw();
    }

    receive() external payable {
        if (entered) return;
        entered = true;
        try vault.withdraw() {}
        catch (bytes memory reason) {
            innerRevert = reason;
        }
    }
}
