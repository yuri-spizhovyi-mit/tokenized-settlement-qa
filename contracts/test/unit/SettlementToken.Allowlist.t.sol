// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SettlementTokenBase} from "./SettlementTokenBase.t.sol";
import {SettlementToken} from "../../src/SettlementToken.sol";

/// @notice TOK-05, TOK-06: the allowlist gates every balance movement, on every path.
contract SettlementTokenAllowlistTest is SettlementTokenBase {
    uint256 internal constant AMOUNT = 100e6;

    // ---- transfer ------------------------------------------------------------

    /// @notice TOK-05: the happy path moves exactly `AMOUNT` and emits Transfer.
    function test_Transfer_MovesTokensBetweenAllowlistedAccounts() public {
        vm.expectEmit(true, true, false, true, address(token));
        emit IERC20.Transfer(alice, bob, AMOUNT);
        vm.prank(alice);
        assertTrue(token.transfer(bob, AMOUNT));

        assertEq(token.balanceOf(alice), ALICE_START - AMOUNT);
        assertEq(token.balanceOf(bob), AMOUNT);
        assertEq(token.totalSupply(), ALICE_START, "transfer must not change supply");
    }

    /// @notice TOK-05: tokens cannot leave the allowlisted set by a direct transfer.
    function test_RevertWhen_TransferRecipientNotAllowlisted() public {
        State memory before = _state();

        vm.prank(alice);
        vm.expectRevert(_notAllowlisted(mallory));
        token.transfer(mallory, AMOUNT);

        _assertUnchanged(before);
    }

    /// @notice TOK-05: a zero-value transfer is still a transfer; no "amount == 0" shortcut.
    function test_RevertWhen_ZeroValueTransferRecipientNotAllowlisted() public {
        vm.prank(alice);
        vm.expectRevert(_notAllowlisted(mallory));
        token.transfer(mallory, 0);
    }

    /// @notice TOK-06: delisting freezes the holder's balance in the same block.
    function test_RevertWhen_SenderDelistedBeforeTransfer() public {
        vm.prank(admin);
        token.setAllowlisted(alice, false);
        State memory before = _state();

        vm.prank(alice);
        vm.expectRevert(_notAllowlisted(alice));
        token.transfer(bob, AMOUNT);

        _assertUnchanged(before);
        assertEq(token.balanceOf(alice), ALICE_START, "delisting must not seize funds");
    }

    /// @notice TOK-06: re-listing restores the holder's ability to transfer.
    function test_Transfer_SucceedsAfterSenderRelisted() public {
        vm.startPrank(admin);
        token.setAllowlisted(alice, false);
        token.setAllowlisted(alice, true);
        vm.stopPrank();

        vm.prank(alice);
        token.transfer(bob, AMOUNT);
        assertEq(token.balanceOf(bob), AMOUNT);
    }

    // ---- transferFrom: no bypass ---------------------------------------------

    /// @notice TOK-05: the allowance path works between allowlisted parties and
    ///         consumes exactly the amount moved.
    function test_TransferFrom_SpendsAllowanceBetweenAllowlistedParties() public {
        vm.prank(alice);
        token.approve(bob, AMOUNT * 2);

        vm.expectEmit(true, true, false, true, address(token));
        emit IERC20.Transfer(alice, bob, AMOUNT);
        vm.prank(bob);
        assertTrue(token.transferFrom(alice, bob, AMOUNT));

        assertEq(token.balanceOf(alice), ALICE_START - AMOUNT);
        assertEq(token.balanceOf(bob), AMOUNT);
        assertEq(token.allowance(alice, bob), AMOUNT, "allowance not reduced by exactly AMOUNT");
        assertEq(token.totalSupply(), ALICE_START);
    }

    /// @notice TOK-05 (bypass): an allowlisted spender cannot route tokens to a
    ///         non-allowlisted recipient through transferFrom.
    function test_RevertWhen_TransferFromRecipientNotAllowlisted() public {
        vm.prank(alice);
        token.approve(bob, AMOUNT);
        State memory before = _state();

        vm.prank(bob);
        vm.expectRevert(_notAllowlisted(mallory));
        token.transferFrom(alice, mallory, AMOUNT);

        _assertUnchanged(before);
        assertEq(token.allowance(alice, bob), AMOUNT, "allowance consumed by a failed transfer");
    }

    /// @notice TOK-05/06 (bypass): an allowance granted before delisting cannot be used
    ///         to move the delisted holder's tokens.
    function test_RevertWhen_TransferFromOwnerDelistedAfterApproving() public {
        vm.prank(alice);
        token.approve(bob, AMOUNT);
        vm.prank(admin);
        token.setAllowlisted(alice, false);
        State memory before = _state();

        vm.prank(bob);
        vm.expectRevert(_notAllowlisted(alice));
        token.transferFrom(alice, bob, AMOUNT);

        _assertUnchanged(before);
        assertEq(token.allowance(alice, bob), AMOUNT);
    }

    /// @notice TOK-05 (bypass): a non-allowlisted spender cannot move tokens even
    ///         between two allowlisted holders.
    function test_RevertWhen_TransferFromSpenderNotAllowlisted() public {
        vm.prank(alice);
        token.approve(mallory, AMOUNT);
        State memory before = _state();

        vm.prank(mallory);
        vm.expectRevert(_notAllowlisted(mallory));
        token.transferFrom(alice, bob, AMOUNT);

        _assertUnchanged(before);
        assertEq(token.allowance(alice, mallory), AMOUNT);
    }

    /// @notice TOK-06: delisting the spender cancels its power to use an existing allowance.
    function test_RevertWhen_TransferFromSpenderDelistedAfterApproval() public {
        vm.prank(alice);
        token.approve(bob, AMOUNT);
        vm.prank(admin);
        token.setAllowlisted(bob, false);

        vm.prank(bob);
        vm.expectRevert(_notAllowlisted(bob));
        token.transferFrom(alice, alice, AMOUNT);

        assertEq(token.allowance(alice, bob), AMOUNT);
    }

    /// @notice TOK-05: approving is not a balance movement, so it is not gated. The
    ///         allowance is inert until the spender is allowlisted (tested above).
    function test_Approve_DoesNotRequireAllowlistedSpender() public {
        vm.prank(alice);
        assertTrue(token.approve(mallory, AMOUNT));
        assertEq(token.allowance(alice, mallory), AMOUNT);
    }

    // ---- mint and burn -------------------------------------------------------

    /// @notice TOK-05: new supply can only be issued to an allowlisted account.
    function test_RevertWhen_MintRecipientNotAllowlisted() public {
        State memory before = _state();

        vm.prank(minter);
        vm.expectRevert(_notAllowlisted(mallory));
        token.mint(mallory, AMOUNT);

        _assertUnchanged(before);
    }

    /// @notice TOK-05: mint emits Transfer from the zero address and grows supply.
    function test_Mint_IncreasesBalanceAndSupply() public {
        vm.expectEmit(true, true, false, true, address(token));
        emit IERC20.Transfer(address(0), bob, AMOUNT);
        vm.prank(minter);
        token.mint(bob, AMOUNT);

        assertEq(token.balanceOf(bob), AMOUNT);
        assertEq(token.totalSupply(), ALICE_START + AMOUNT);
    }

    /// @notice TOK-05: burn emits Transfer to the zero address and shrinks supply.
    function test_Burn_DecreasesBalanceAndSupply() public {
        vm.prank(alice);
        token.transfer(minter, AMOUNT); // redemption: holder returns tokens to the issuer

        vm.expectEmit(true, true, false, true, address(token));
        emit IERC20.Transfer(minter, address(0), AMOUNT);
        vm.prank(minter);
        token.burn(AMOUNT);

        assertEq(token.balanceOf(minter), 0);
        assertEq(token.totalSupply(), ALICE_START - AMOUNT);
    }

    /// @notice TOK-05: a minter that has been delisted cannot burn: burning is a balance
    ///         movement out of a non-allowlisted account.
    function test_RevertWhen_BurnerNotAllowlisted() public {
        vm.prank(alice);
        token.transfer(minter, AMOUNT);
        vm.prank(admin);
        token.setAllowlisted(minter, false);
        State memory before = _state();

        vm.prank(minter);
        vm.expectRevert(_notAllowlisted(minter));
        token.burn(AMOUNT);

        _assertUnchanged(before);
    }

    // ---- allowlist management -------------------------------------------------

    /// @notice TOK-04: allowlist changes are observable on-chain for audit and indexing.
    function test_SetAllowlisted_UpdatesFlagAndEmitsEvent() public {
        vm.expectEmit(true, false, false, true, address(token));
        emit SettlementToken.AllowlistUpdated(mallory, true);
        vm.prank(admin);
        token.setAllowlisted(mallory, true);
        assertTrue(token.isAllowlisted(mallory));

        vm.expectEmit(true, false, false, true, address(token));
        emit SettlementToken.AllowlistUpdated(mallory, false);
        vm.prank(admin);
        token.setAllowlisted(mallory, false);
        assertFalse(token.isAllowlisted(mallory));
    }

    /// @notice TOK-04: the zero address is the mint/burn sentinel and cannot be listed.
    function test_RevertWhen_AllowlistingZeroAddress() public {
        vm.prank(admin);
        vm.expectRevert(abi.encodeWithSelector(SettlementToken.InvalidAccount.selector, address(0)));
        token.setAllowlisted(address(0), true);
    }
}
