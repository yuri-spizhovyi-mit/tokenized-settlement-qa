// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {Escrow} from "../../src/Escrow.sol";

/// @dev The parts of Circle's FiatToken (USDC) these tests drive. Stable since V2.
interface IFiatToken is IERC20Metadata {
    function masterMinter() external view returns (address);
    function configureMinter(address minter, uint256 minterAllowedAmount) external returns (bool);
    function mint(address to, uint256 amount) external returns (bool);
    function blacklister() external view returns (address);
    function blacklist(address account) external;
    function isBlacklisted(address account) external view returns (bool);
}

/// @notice Escrow against real USDC on a mainnet fork, pinned to a block.
/// @dev Requirements (IDs are used in test NatSpec and the RTM):
///      PROP-11  Escrow settles real USDC (6 decimals, proxy token) for the exact amount,
///               including 1 base unit (0.000001 USDC) and amounts above 2^64.
///      PROP-12  USDC's own blocklist behaves like SettlementToken's allowlist from the
///               escrow's point of view: a blacklisted payee blocks release with the deal
///               still Funded and refundable; a blacklisted escrow freezes both payouts.
///
///      Runs only when MAINNET_RPC_URL is set (CI's fork job). Without it every test is
///      skipped, not failed: cloud sessions cannot reach RPC providers.
///
///      USDC reverts with require-strings, not custom errors, so these tests assert the
///      exact string. That's the one place the repo's "exact custom errors" rule can't
///      apply: the error format belongs to Circle's contract.
contract EscrowUsdcForkTest is Test {
    /// @dev Pinned for reproducible state and RPC caching. USDC is FiatTokenV2_2 here.
    uint256 internal constant FORK_BLOCK = 21_000_000;
    IFiatToken internal constant USDC = IFiatToken(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);
    bytes internal constant BLACKLISTED = bytes("Blacklistable: account is blacklisted");

    Escrow internal escrow;
    address internal arbiter = makeAddr("arbiter");
    address internal payer = makeAddr("payer");
    address internal payee = makeAddr("payee");

    uint64 internal deadline;

    function setUp() public {
        string memory rpc = vm.envOr("MAINNET_RPC_URL", string(""));
        if (bytes(rpc).length == 0) {
            vm.skip(true, "MAINNET_RPC_URL not set; fork tests run in CI's fork job");
            return;
        }
        vm.createSelectFork(rpc, FORK_BLOCK);

        escrow = new Escrow(IERC20(address(USDC)), arbiter);
        deadline = uint64(vm.getBlockTimestamp()) + 7 days;

        _mintUsdc(payer, 10_000_000e6);
        vm.prank(payer);
        USDC.approve(address(escrow), type(uint256).max);
    }

    /// @dev Mints through USDC's real minter path: the master minter configures us, we mint.
    function _mintUsdc(address to, uint256 amount) internal {
        vm.prank(USDC.masterMinter());
        USDC.configureMinter(address(this), amount);
        USDC.mint(to, amount);
    }

    function _state(uint256 id) internal view returns (Escrow.State state) {
        (,,,, state) = escrow.deals(id);
    }

    // ---- 6-decimal amounts (PROP-11) ------------------------------------------------

    /// @notice PROP-11: the fork is the token we think it is.
    function test_Fork_UsdcHasSixDecimals() public view {
        assertEq(USDC.decimals(), 6);
        assertEq(USDC.symbol(), "USDC");
    }

    /// @notice PROP-11: the smallest USDC amount, 1 base unit = 0.000001 USDC, round-trips.
    function test_Fork_ReleasesOneBaseUnit() public {
        vm.prank(payer);
        uint256 id = escrow.open(payee, 1, deadline);
        assertEq(USDC.balanceOf(address(escrow)), 1);

        vm.prank(arbiter);
        escrow.release(id);

        assertEq(USDC.balanceOf(payee), 1);
        assertEq(USDC.balanceOf(address(escrow)), 0);
        assertEq(uint8(_state(id)), uint8(Escrow.State.Released));
    }

    /// @notice PROP-11: a realistic settlement, 1,234,567.890123 USDC, moves to the unit.
    function test_Fork_ReleasesSixDecimalAmountExactly() public {
        uint128 amount = 1_234_567_890123; // 1,234,567.890123 USDC
        uint256 payerBefore = USDC.balanceOf(payer);
        uint256 supplyBefore = USDC.totalSupply();

        vm.prank(payer);
        uint256 id = escrow.open(payee, amount, deadline);
        vm.prank(arbiter);
        escrow.release(id);

        assertEq(USDC.balanceOf(payer), payerBefore - amount, "payer");
        assertEq(USDC.balanceOf(payee), amount, "payee");
        assertEq(USDC.balanceOf(address(escrow)), 0, "escrow");
        assertEq(USDC.totalSupply(), supplyBefore, "USDC supply moved");
    }

    /// @notice PROP-11: amounts above 2^64 base units fit Escrow's uint128 and USDC alike.
    function test_Fork_RefundsAmountAboveUint64() public {
        uint128 amount = uint128(type(uint64).max) + 1; // ~18.4 trillion USDC
        _mintUsdc(payer, amount);

        vm.prank(payer);
        uint256 id = escrow.open(payee, amount, deadline);
        vm.warp(deadline);
        escrow.refund(id);

        assertEq(USDC.balanceOf(address(escrow)), 0);
        assertEq(uint8(_state(id)), uint8(Escrow.State.Refunded));
    }

    // ---- USDC blocklist (PROP-12) ----------------------------------------------------

    /// @notice PROP-12: Circle blacklists the payee mid-deal. release reverts with USDC's
    ///         message, the deal stays Funded, and the payer can still refund.
    function test_Fork_RevertWhen_PayeeBlacklistedBeforeRelease() public {
        vm.prank(payer);
        uint256 id = escrow.open(payee, 500e6, deadline);
        vm.prank(USDC.blacklister());
        USDC.blacklist(payee);
        assertTrue(USDC.isBlacklisted(payee));

        vm.prank(arbiter);
        vm.expectRevert(BLACKLISTED);
        escrow.release(id);

        assertEq(USDC.balanceOf(address(escrow)), 500e6, "funds left escrow");
        assertEq(uint8(_state(id)), uint8(Escrow.State.Funded));

        vm.warp(deadline);
        escrow.refund(id);
        assertEq(uint8(_state(id)), uint8(Escrow.State.Refunded));
    }

    /// @notice PROP-12: a blacklisted payer cannot fund a deal.
    function test_Fork_RevertWhen_PayerBlacklistedAtOpen() public {
        vm.prank(USDC.blacklister());
        USDC.blacklist(payer);

        vm.prank(payer);
        vm.expectRevert(BLACKLISTED);
        escrow.open(payee, 500e6, deadline);

        assertEq(escrow.nextId(), 0);
    }

    /// @notice PROP-12: Circle blacklists the escrow contract itself. Both payouts freeze,
    ///         the same outcome as delisting the escrow on SettlementToken (ESC-D5).
    function test_Fork_RevertWhen_EscrowBlacklistedFreezesPayouts() public {
        vm.prank(payer);
        uint256 id = escrow.open(payee, 500e6, deadline);
        vm.prank(USDC.blacklister());
        USDC.blacklist(address(escrow));

        vm.prank(arbiter);
        vm.expectRevert(BLACKLISTED);
        escrow.release(id);

        vm.warp(deadline);
        vm.expectRevert(BLACKLISTED);
        escrow.refund(id);

        assertEq(USDC.balanceOf(address(escrow)), 500e6);
        assertEq(uint8(_state(id)), uint8(Escrow.State.Funded));
    }
}
