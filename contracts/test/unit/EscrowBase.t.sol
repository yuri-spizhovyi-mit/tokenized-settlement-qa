// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {SettlementToken} from "../../src/SettlementToken.sol";
import {Escrow} from "../../src/Escrow.sol";

/// @notice Shared fixture for the Escrow unit suites.
/// @dev Requirements covered by the suites (IDs are used in test NatSpec and the RTM):
///      ESC-01  Deployment: token and arbiter are fixed at construction; zero rejected.
///      ESC-02  open records the deal, pulls exactly `amount` from the payer into
///              escrow, and emits Opened with every field.
///      ESC-03  open rejects a zero amount and a payee that could never be paid
///              (zero address, or the escrow itself).
///      ESC-04  Only the arbiter can release; release pays the payee in full and
///              marks the deal Released.
///      ESC-05  Anyone can refund once block.timestamp >= deadline; refund pays the
///              payer in full and marks the deal Refunded.
///      ESC-06  Released and Refunded are terminal: no second release or refund, and
///              no release after refund (or refund after release).
///      ESC-07  An id that was never opened cannot be released or refunded.
///      ESC-08  Token allowlist: escrow, payer and payee must be allowlisted; a failed
///              payout leaves the deal Funded and the funds in escrow.
///      ESC-09  Token pause: release and refund revert while paused; the deal stays
///              Funded and can settle after unpause.
abstract contract EscrowBase is Test {
    SettlementToken internal token;
    Escrow internal escrow;

    address internal admin = makeAddr("admin");
    address internal minter = makeAddr("minter");
    address internal pauser = makeAddr("pauser");
    address internal compliance = makeAddr("compliance");
    address internal arbiter = makeAddr("arbiter");
    address internal payer = makeAddr("payer");
    address internal payee = makeAddr("payee");
    address internal stranger = makeAddr("stranger"); // allowlisted, no role in the escrow

    uint128 internal constant AMOUNT = 250e6;
    uint256 internal constant PAYER_START = 1_000e6;
    uint64 internal constant DURATION = 7 days;

    uint64 internal deadline;

    function setUp() public virtual {
        vm.warp(1_700_000_000); // a realistic timestamp, so "deadline - 1" never underflows

        token = new SettlementToken(admin);
        escrow = new Escrow(token, arbiter);
        deadline = uint64(block.timestamp) + DURATION;

        vm.startPrank(admin);
        token.grantRole(token.MINTER_ROLE(), minter);
        token.grantRole(token.PAUSER_ROLE(), pauser);
        token.grantRole(token.ALLOWLIST_ROLE(), compliance);
        vm.stopPrank();

        vm.startPrank(compliance);
        token.setAllowlisted(address(escrow), true);
        token.setAllowlisted(payer, true);
        token.setAllowlisted(payee, true);
        token.setAllowlisted(stranger, true);
        vm.stopPrank();

        vm.prank(minter);
        token.mint(payer, PAYER_START);

        vm.prank(payer);
        token.approve(address(escrow), type(uint256).max);
    }

    // ---- helpers -------------------------------------------------------------

    function _open() internal returns (uint256 id) {
        vm.prank(payer);
        id = escrow.open(payee, AMOUNT, deadline);
    }

    function _wrongState(Escrow.State s) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(Escrow.WrongState.selector, s);
    }

    function _deal(uint256 id) internal view returns (Escrow.Deal memory d) {
        (d.payer, d.payee, d.amount, d.deadline, d.state) = escrow.deals(id);
    }

    function _assertDeal(
        uint256 id,
        address payer_,
        address payee_,
        uint128 amount_,
        uint64 deadline_,
        Escrow.State state_
    ) internal view {
        Escrow.Deal memory d = _deal(id);
        assertEq(d.payer, payer_, "deal.payer");
        assertEq(d.payee, payee_, "deal.payee");
        assertEq(d.amount, amount_, "deal.amount");
        assertEq(d.deadline, deadline_, "deal.deadline");
        assertEq(uint8(d.state), uint8(state_), "deal.state");
    }

    /// @dev Every balance the escrow can move, plus total supply.
    struct Balances {
        uint256 payer;
        uint256 payee;
        uint256 escrow;
        uint256 totalSupply;
    }

    function _balances() internal view returns (Balances memory b) {
        b.payer = token.balanceOf(payer);
        b.payee = token.balanceOf(payee);
        b.escrow = token.balanceOf(address(escrow));
        b.totalSupply = token.totalSupply();
    }

    function _assertBalancesUnchanged(Balances memory before) internal view {
        Balances memory nowB = _balances();
        assertEq(nowB.payer, before.payer, "payer balance changed");
        assertEq(nowB.payee, before.payee, "payee balance changed");
        assertEq(nowB.escrow, before.escrow, "escrow balance changed");
        assertEq(nowB.totalSupply, before.totalSupply, "totalSupply changed");
    }
}
