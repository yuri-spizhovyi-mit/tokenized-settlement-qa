// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test, console2} from "forge-std/Test.sol";
import {SettlementToken} from "../../src/SettlementToken.sol";
import {Escrow} from "../../src/Escrow.sol";
import {EscrowHandler} from "./handlers/EscrowHandler.sol";

/// @notice Stateful invariants for Escrow + SettlementToken under random sequences of
///         open, release, refund, re-settle attempts, time warps, pause/unpause,
///         delisting and minting.
/// @dev Requirements (IDs are used in test NatSpec and the RTM):
///      PROP-07  The escrow's token balance always equals the sum of amounts in Funded
///               deals: no deal is paid twice, and no Funded deal is unbacked.
///      PROP-08  Ghost accounting matches real balances: totalReleased equals what the
///               payees received, and totalRefunded equals what the payers recovered.
///      PROP-09  The sum of balances of every account that can hold the token equals
///               totalSupply, and totalSupply equals everything minted.
///      PROP-10  A deal that reached Released or Refunded never leaves that state.
///
///      The handler catches every revert, so fail_on_revert is on: a handler revert
///      would mean the handler itself is broken, not the contracts.
/// forge-config: default.invariant.fail-on-revert = true
/// forge-config: ci.invariant.fail-on-revert = true
contract EscrowInvariantTest is Test {
    SettlementToken internal token;
    Escrow internal escrow;
    EscrowHandler internal handler;

    address internal admin = makeAddr("admin");
    address internal minter = makeAddr("minter");
    address internal pauser = makeAddr("pauser");
    address internal compliance = makeAddr("compliance");
    address internal arbiter = makeAddr("arbiter");

    function setUp() public {
        vm.warp(1_700_000_000);
        token = new SettlementToken(admin);
        escrow = new Escrow(token, arbiter, makeAddr("escrowAdmin"));
        handler = new EscrowHandler(token, escrow, arbiter, pauser, compliance, minter);

        vm.startPrank(admin);
        token.grantRole(token.MINTER_ROLE(), minter);
        token.grantRole(token.PAUSER_ROLE(), pauser);
        token.grantRole(token.ALLOWLIST_ROLE(), compliance);
        vm.stopPrank();

        vm.prank(compliance);
        token.setAllowlisted(address(escrow), true);
        handler.seed();

        // Weighted: each extra entry is another draw. Settlement actions dominate so most
        // calls hit live deals; controls are rarer and restoreControls undoes them.
        bytes4[] memory selectors = new bytes4[](14);
        selectors[0] = EscrowHandler.open.selector;
        selectors[1] = EscrowHandler.open.selector;
        selectors[2] = EscrowHandler.open.selector;
        selectors[3] = EscrowHandler.release.selector;
        selectors[4] = EscrowHandler.release.selector;
        selectors[5] = EscrowHandler.refund.selector;
        selectors[6] = EscrowHandler.refund.selector;
        selectors[7] = EscrowHandler.resettle.selector;
        selectors[8] = EscrowHandler.warp.selector;
        selectors[9] = EscrowHandler.togglePause.selector;
        selectors[10] = EscrowHandler.toggleListing.selector;
        selectors[11] = EscrowHandler.restoreControls.selector;
        selectors[12] = EscrowHandler.restoreControls.selector;
        selectors[13] = EscrowHandler.mintToPayer.selector;
        targetContract(address(handler));
        targetSelector(FuzzSelector({addr: address(handler), selectors: selectors}));
    }

    // ---- invariants ----------------------------------------------------------------

    /// @notice PROP-07: every token the escrow holds belongs to exactly one Funded deal.
    function invariant_EscrowBalanceEqualsSumOfFundedDeals() public view {
        uint256 funded;
        uint256 n = escrow.nextId();
        for (uint256 id; id < n; ++id) {
            (,, uint128 amount,, Escrow.State state) = escrow.deals(id);
            if (state == Escrow.State.Funded) funded += amount;
        }
        assertEq(token.balanceOf(address(escrow)), funded, "escrow balance != sum(Funded)");
        assertEq(
            funded,
            handler.ghostTotalOpened() - handler.ghostTotalReleased()
                - handler.ghostTotalRefunded(),
            "sum(Funded) != opened - released - refunded"
        );
    }

    /// @notice PROP-08: the payees received exactly what was released, and the payers
    ///         recovered exactly what was refunded.
    function invariant_GhostTotalsMatchCounterpartyBalances() public view {
        address[] memory payees = handler.payees();
        uint256 payeesReceived;
        for (uint256 i; i < payees.length; ++i) {
            payeesReceived += token.balanceOf(payees[i]); // payees start at 0, never send
        }
        assertEq(payeesReceived, handler.ghostTotalReleased(), "totalReleased != payees received");

        address[] memory payers = handler.payers();
        uint256 payersHold;
        for (uint256 i; i < payers.length; ++i) {
            payersHold += token.balanceOf(payers[i]);
        }
        // Payers hold: everything minted to them, minus what they put in, plus refunds.
        uint256 recovered = payersHold + handler.ghostTotalOpened() - handler.ghostTotalMinted();
        assertEq(recovered, handler.ghostTotalRefunded(), "totalRefunded != payers recovered");
    }

    /// @notice PROP-09: no balance appears or disappears outside mint.
    function invariant_TrackedBalancesSumToTotalSupply() public view {
        address[] memory payers = handler.payers();
        address[] memory payees = handler.payees();
        uint256 sum = token.balanceOf(address(escrow));
        for (uint256 i; i < payers.length; ++i) {
            sum += token.balanceOf(payers[i]);
        }
        for (uint256 i; i < payees.length; ++i) {
            sum += token.balanceOf(payees[i]);
        }
        assertEq(sum, token.totalSupply(), "sum(holders) != totalSupply");
        assertEq(token.totalSupply(), handler.ghostTotalMinted(), "totalSupply != minted");
    }

    /// @notice PROP-10: Released and Refunded are terminal.
    function invariant_SettledDealsNeverChangeState() public view {
        uint256 n = escrow.nextId();
        for (uint256 id; id < n; ++id) {
            uint8 settledAs = handler.ghostSettledAs(id);
            if (settledAs == 0) continue;
            (,,,, Escrow.State state) = escrow.deals(id);
            assertEq(uint8(state) + 1, settledAs, "settled deal changed state");
        }
        assertEq(handler.count("resettle.SUCCEEDED"), 0, "a settled deal was settled again");
    }

    /// @dev Printed at -vv after each run, so a reviewer can see the invariants weren't
    ///      checked against an empty system.
    function afterInvariant() external view {
        console2.log(
            "open ok/rejected       ", handler.count("open.ok"), handler.count("open.rejected")
        );
        console2.log(
            "release ok/rejected    ",
            handler.count("release.ok"),
            handler.count("release.rejected")
        );
        console2.log(
            "refund ok/rejected     ", handler.count("refund.ok"), handler.count("refund.rejected")
        );
        console2.log("resettle rejected      ", handler.count("resettle.rejected"));
        console2.log(
            "togglePause / listing  ", handler.count("togglePause"), handler.count("toggleListing")
        );
        console2.log("deals opened           ", escrow.nextId());
    }
}
