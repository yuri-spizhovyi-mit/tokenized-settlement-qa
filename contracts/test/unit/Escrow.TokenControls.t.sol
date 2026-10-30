// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {EscrowBase} from "./EscrowBase.t.sol";
import {Escrow} from "../../src/Escrow.sol";
import {SettlementToken} from "../../src/SettlementToken.sol";

/// @notice ESC-08, ESC-09: the escrow inherits the token's allowlist and pause controls.
///         A payout the token refuses must leave the deal Funded and the money in escrow.
contract EscrowTokenControlsTest is EscrowBase {
    function _notAllowlisted(address account) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(SettlementToken.AccountNotAllowlisted.selector, account);
    }

    // ---- allowlist (ESC-08) --------------------------------------------------------

    /// @notice ESC-08 (issue #5): an escrow that compliance has not listed cannot take
    ///         custody. open reverts with the escrow's own address, and no deal exists.
    function test_RevertWhen_OpenWithEscrowNotAllowlisted() public {
        vm.prank(compliance);
        token.setAllowlisted(address(escrow), false);
        Balances memory before = _balances();

        vm.prank(payer);
        vm.expectRevert(_notAllowlisted(address(escrow)));
        escrow.open(payee, AMOUNT, deadline);

        _assertBalancesUnchanged(before);
        assertEq(escrow.nextId(), 0, "deal recorded although funding failed");
    }

    /// @notice ESC-08: a delisted payer cannot fund a deal.
    function test_RevertWhen_OpenWithPayerNotAllowlisted() public {
        vm.prank(compliance);
        token.setAllowlisted(payer, false);

        vm.prank(payer);
        vm.expectRevert(_notAllowlisted(payer));
        escrow.open(payee, AMOUNT, deadline);
    }

    /// @notice ESC-08: the escrow does not pre-check the payee (it only knows IERC20),
    ///         so a deal can be opened to an unlisted payee; the token stops the payout.
    ///         release reverts, the deal stays Funded, and the money stays in escrow.
    function test_RevertWhen_ReleaseToPayeeNotAllowlisted() public {
        uint256 id = _open();
        vm.prank(compliance);
        token.setAllowlisted(payee, false);
        Balances memory before = _balances();

        vm.prank(arbiter);
        vm.expectRevert(_notAllowlisted(payee));
        escrow.release(id);

        _assertBalancesUnchanged(before);
        assertEq(token.balanceOf(address(escrow)), AMOUNT, "funds left escrow");
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    /// @notice ESC-08: funds held for a delisted payee are not stuck. After the deadline
    ///         the payer gets them back, and the deal ends Refunded.
    function test_Refund_RecoversFundsHeldForDelistedPayee() public {
        uint256 id = _open();
        vm.prank(compliance);
        token.setAllowlisted(payee, false);
        vm.prank(arbiter);
        vm.expectRevert(_notAllowlisted(payee));
        escrow.release(id);

        vm.warp(deadline);
        vm.prank(payer);
        escrow.refund(id);

        assertEq(token.balanceOf(payer), PAYER_START, "payer not made whole");
        assertEq(token.balanceOf(address(escrow)), 0);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Refunded);
    }

    /// @notice ESC-08: if compliance delists the escrow mid-deal, every payout stops,
    ///         including refunds. Funds are frozen in escrow until it is re-listed. This
    ///         is intended: delisting the custodian is a compliance freeze.
    function test_RevertWhen_EscrowDelistedBeforeRelease() public {
        uint256 id = _open();
        vm.prank(compliance);
        token.setAllowlisted(address(escrow), false);
        Balances memory before = _balances();

        vm.prank(arbiter);
        vm.expectRevert(_notAllowlisted(address(escrow)));
        escrow.release(id);

        vm.warp(deadline);
        vm.prank(payer);
        vm.expectRevert(_notAllowlisted(address(escrow)));
        escrow.refund(id);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);

        // Re-listing lifts the freeze; the deal settles normally.
        vm.prank(compliance);
        token.setAllowlisted(address(escrow), true);
        vm.prank(payer);
        escrow.refund(id);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Refunded);
    }

    // ---- pause (ESC-09) -------------------------------------------------------------

    /// @notice ESC-09: while the token is paused the arbiter cannot release.
    function test_RevertWhen_ReleaseWhileTokenPaused() public {
        uint256 id = _open();
        vm.prank(pauser);
        token.pause();
        Balances memory before = _balances();

        vm.prank(arbiter);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.release(id);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    /// @notice ESC-09: while the token is paused nobody can refund, even after the deadline.
    function test_RevertWhen_RefundWhileTokenPaused() public {
        uint256 id = _open();
        vm.warp(deadline);
        vm.prank(pauser);
        token.pause();
        Balances memory before = _balances();

        vm.prank(payer);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.refund(id);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    /// @notice ESC-09: no new deals while paused.
    function test_RevertWhen_OpenWhileTokenPaused() public {
        vm.prank(pauser);
        token.pause();

        vm.prank(payer);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.open(payee, AMOUNT, deadline);

        assertEq(escrow.nextId(), 0);
    }

    /// @notice ESC-09: a pause only delays settlement; after unpause the deal releases.
    function test_Release_SucceedsAfterTokenUnpaused() public {
        uint256 id = _open();
        vm.prank(pauser);
        token.pause();
        vm.prank(arbiter);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.release(id);

        vm.prank(pauser);
        token.unpause();
        vm.prank(arbiter);
        escrow.release(id);

        assertEq(token.balanceOf(payee), AMOUNT);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Released);
    }
}
