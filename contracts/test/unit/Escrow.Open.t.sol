// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {EscrowBase} from "./EscrowBase.t.sol";
import {Escrow} from "../../src/Escrow.sol";

/// @notice ESC-01, ESC-02, ESC-03: deployment and opening a deal.
contract EscrowOpenTest is EscrowBase {
    // ---- deployment ------------------------------------------------------------

    /// @notice ESC-01: the token and arbiter are wired in and readable.
    function test_Deploy_SetsTokenAndArbiter() public view {
        assertEq(address(escrow.token()), address(token));
        assertEq(escrow.arbiter(), arbiter);
        assertEq(escrow.nextId(), 0);
    }

    /// @notice ESC-01: an escrow with no token can never hold funds.
    function test_RevertWhen_DeployedWithZeroToken() public {
        vm.expectRevert(Escrow.ZeroAddress.selector);
        new Escrow(IERC20(address(0)), arbiter, escrowAdmin);
    }

    /// @notice ESC-01: an escrow with no arbiter could never release; every deal
    ///         would end in a refund.
    function test_RevertWhen_DeployedWithZeroArbiter() public {
        vm.expectRevert(Escrow.ZeroAddress.selector);
        new Escrow(token, address(0), escrowAdmin);
    }

    /// @notice ESC-01 / SEC-07: an escrow with no admin could never rotate a compromised
    ///         arbiter. Rejected by the AccessControlDefaultAdminRules constructor.
    function test_RevertWhen_DeployedWithZeroAdmin() public {
        vm.expectRevert(
            abi.encodeWithSignature("AccessControlInvalidDefaultAdmin(address)", address(0))
        );
        new Escrow(token, arbiter, address(0));
    }

    // ---- open: happy path ------------------------------------------------------

    /// @notice ESC-02: open stores every field of the deal, starting in Funded.
    function test_Open_RecordsDeal() public {
        uint256 id = _open();

        assertEq(id, 0, "first id");
        assertEq(escrow.nextId(), 1, "nextId not advanced");
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    /// @notice ESC-02: open pulls exactly AMOUNT from the payer and nothing else moves.
    function test_Open_PullsTokensFromPayerIntoEscrow() public {
        Balances memory before = _balances();

        _open();

        assertEq(token.balanceOf(payer), before.payer - AMOUNT, "payer");
        assertEq(token.balanceOf(address(escrow)), before.escrow + AMOUNT, "escrow");
        assertEq(token.balanceOf(payee), before.payee, "payee paid on open");
        assertEq(token.totalSupply(), before.totalSupply, "supply changed");
        assertEq(
            token.allowance(payer, address(escrow)),
            type(uint256).max,
            "infinite allowance should not be decremented"
        );
    }

    /// @notice ESC-02: Opened carries every field, from the escrow contract itself,
    ///         and the token emits the matching Transfer.
    function test_Open_EmitsOpenedWithAllArgs() public {
        vm.expectEmit(true, true, true, true, address(escrow));
        emit Escrow.Opened(0, payer, payee, AMOUNT, deadline);
        vm.expectEmit(true, true, false, true, address(token));
        emit IERC20.Transfer(payer, address(escrow), AMOUNT);

        _open();
    }

    /// @notice ESC-02: ids are sequential and deals don't share state.
    function test_Open_AssignsSequentialIdsToIndependentDeals() public {
        uint256 first = _open();
        vm.prank(payer);
        uint256 second = escrow.open(stranger, 1e6, deadline + 1);

        assertEq(first, 0);
        assertEq(second, 1);
        _assertDeal(first, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
        _assertDeal(second, payer, stranger, 1e6, deadline + 1, Escrow.State.Funded);
        assertEq(token.balanceOf(address(escrow)), AMOUNT + 1e6);
    }

    // ---- open: input validation (ESC-03) --------------------------------------

    /// @notice ESC-03: a zero-value deal settles nothing but would still emit Opened and
    ///         Released, a false settlement record for indexers and reconciliation.
    function test_RevertWhen_OpenWithZeroAmount() public {
        Balances memory before = _balances();

        vm.prank(payer);
        vm.expectRevert(Escrow.ZeroAmount.selector);
        escrow.open(payee, 0, deadline);

        _assertBalancesUnchanged(before);
        assertEq(escrow.nextId(), 0, "id consumed by a failed open");
    }

    /// @notice ESC-03: a zero payee can never receive; release would always revert.
    function test_RevertWhen_OpenWithZeroPayee() public {
        vm.prank(payer);
        vm.expectRevert(abi.encodeWithSelector(Escrow.InvalidPayee.selector, address(0)));
        escrow.open(address(0), AMOUNT, deadline);

        assertEq(escrow.nextId(), 0);
    }

    /// @notice ESC-03: if the escrow were its own payee, release would "pay" the escrow,
    ///         mark the deal Released, and the funds would be stranded forever.
    function test_RevertWhen_OpenWithEscrowAsPayee() public {
        vm.prank(payer);
        vm.expectRevert(abi.encodeWithSelector(Escrow.InvalidPayee.selector, address(escrow)));
        escrow.open(address(escrow), AMOUNT, deadline);

        assertEq(escrow.nextId(), 0);
    }

    /// @notice ESC-03: the deadline must be in the future. `deadline == now` would make the
    ///         deal refundable in the same block it opens, giving the arbiter no window;
    ///         it is rejected like any past deadline. Decision ESC-D6.
    function test_RevertWhen_OpenWithDeadlineEqualToNow() public {
        uint64 nowTs = uint64(vm.getBlockTimestamp());
        Balances memory before = _balances();

        vm.prank(payer);
        vm.expectRevert(abi.encodeWithSelector(Escrow.DeadlineInPast.selector, nowTs));
        escrow.open(payee, AMOUNT, nowTs);

        _assertBalancesUnchanged(before);
        assertEq(escrow.nextId(), 0, "id consumed by a failed open");
    }

    /// @notice ESC-03: one second in the past is rejected, with the offending deadline.
    function test_RevertWhen_OpenWithDeadlineOneSecondAgo() public {
        uint64 past = uint64(vm.getBlockTimestamp()) - 1;

        vm.prank(payer);
        vm.expectRevert(abi.encodeWithSelector(Escrow.DeadlineInPast.selector, past));
        escrow.open(payee, AMOUNT, past);

        assertEq(escrow.nextId(), 0);
    }

    /// @notice ESC-03: one second in the future is the earliest valid deadline. The deal
    ///         opens Funded, and is refundable one second later, not before.
    function test_Open_AcceptsDeadlineOneSecondAhead() public {
        uint64 soon = uint64(vm.getBlockTimestamp()) + 1;

        vm.prank(payer);
        uint256 id = escrow.open(payee, AMOUNT, soon);
        _assertDeal(id, payer, payee, AMOUNT, soon, Escrow.State.Funded);

        vm.prank(payer);
        vm.expectRevert(abi.encodeWithSelector(Escrow.TooEarly.selector, soon));
        escrow.refund(id);

        vm.warp(soon);
        vm.prank(payer);
        escrow.refund(id);
        _assertDeal(id, payer, payee, AMOUNT, soon, Escrow.State.Refunded);
    }

    /// @notice ESC-02: open is all-or-nothing; without enough allowance no deal exists.
    function test_RevertWhen_OpenWithoutAllowance() public {
        vm.prank(payer);
        token.approve(address(escrow), AMOUNT - 1);

        vm.prank(payer);
        vm.expectRevert(
            abi.encodeWithSignature(
                "ERC20InsufficientAllowance(address,uint256,uint256)",
                address(escrow),
                AMOUNT - 1,
                AMOUNT
            )
        );
        escrow.open(payee, AMOUNT, deadline);

        assertEq(escrow.nextId(), 0, "deal recorded although funding failed");
    }

    /// @notice ESC-02: a payer cannot open a deal larger than their balance.
    function test_RevertWhen_OpenExceedsPayerBalance() public {
        vm.prank(payer);
        vm.expectRevert(
            abi.encodeWithSignature(
                "ERC20InsufficientBalance(address,uint256,uint256)",
                payer,
                PAYER_START,
                PAYER_START + 1
            )
        );
        escrow.open(payee, uint128(PAYER_START + 1), deadline);
    }
}
