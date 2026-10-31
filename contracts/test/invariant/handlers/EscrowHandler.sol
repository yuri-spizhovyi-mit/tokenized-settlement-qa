// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {CommonBase} from "forge-std/Base.sol";
import {StdCheats} from "forge-std/StdCheats.sol";
import {StdUtils} from "forge-std/StdUtils.sol";
import {SettlementToken} from "../../../src/SettlementToken.sol";
import {Escrow} from "../../../src/Escrow.sol";

/// @notice Drives Escrow and SettlementToken through random but mostly-valid sequences
///         and keeps ghost accounting of what *should* have happened.
/// @dev Steering, so the fuzzer spends its calls on interesting states:
///      - amounts are bounded to what the payer actually holds;
///      - release and refund pick an existing Funded deal when there is one;
///      - refund warps to the deadline half the time, so refunds really happen;
///      - token controls (pause, delisting) are toggled, and restoreControls lifts them
///        all, so a run alternates between frozen and live periods instead of freezing
///        for good after the first pause.
///      Every escrow call goes through try/catch. A call the token or escrow refuses
///      (paused, delisted, too early) is counted as a rejection and changes no ghost,
///      so the invariants check that a rejected call also changed no real state.
contract EscrowHandler is CommonBase, StdCheats, StdUtils {
    SettlementToken public immutable token;
    Escrow public immutable escrow;
    address public immutable arbiter;
    address public immutable pauser;
    address public immutable compliance;
    address public immutable minter;

    address[] internal _payers;
    address[] internal _payees;

    uint256 public constant INITIAL_PAYER_BALANCE = 1_000_000e6;

    // ---- ghost variables ---------------------------------------------------------

    uint256 public ghostTotalOpened;
    uint256 public ghostTotalReleased;
    uint256 public ghostTotalRefunded;
    uint256 public ghostTotalMinted;

    /// @dev 0 = never settled, otherwise uint8(State) + 1 at the moment it settled.
    mapping(uint256 id => uint8) public ghostSettledAs;

    /// @dev Counters for the call summary, by outcome.
    mapping(bytes32 action => uint256) public calls;

    constructor(
        SettlementToken token_,
        Escrow escrow_,
        address arbiter_,
        address pauser_,
        address compliance_,
        address minter_
    ) {
        token = token_;
        escrow = escrow_;
        arbiter = arbiter_;
        pauser = pauser_;
        compliance = compliance_;
        minter = minter_;

        for (uint256 i; i < 3; ++i) {
            _payers.push(makeAddr(string.concat("payer", vm.toString(i))));
            _payees.push(makeAddr(string.concat("payee", vm.toString(i))));
        }
    }

    /// @dev Called once by the test's setUp, after the handler holds MINTER-free setup.
    function seed() external {
        for (uint256 i; i < _payers.length; ++i) {
            vm.prank(compliance);
            token.setAllowlisted(_payers[i], true);
            vm.prank(compliance);
            token.setAllowlisted(_payees[i], true);

            vm.prank(minter);
            token.mint(_payers[i], INITIAL_PAYER_BALANCE);
            ghostTotalMinted += INITIAL_PAYER_BALANCE;

            vm.prank(_payers[i]);
            token.approve(address(escrow), type(uint256).max);
        }
    }

    // ---- views for the invariants ------------------------------------------------

    function payers() external view returns (address[] memory) {
        return _payers;
    }

    function payees() external view returns (address[] memory) {
        return _payees;
    }

    // ---- escrow actions ----------------------------------------------------------

    function open(uint256 payerSeed, uint256 payeeSeed, uint256 amount, uint256 duration) external {
        address payer = _payers[payerSeed % _payers.length];
        address payee = _payees[payeeSeed % _payees.length];
        uint256 balance = token.balanceOf(payer);
        if (balance == 0) return _count("open.skipped");

        uint128 amt = uint128(bound(amount, 1, balance));
        uint64 deadline = uint64(vm.getBlockTimestamp() + bound(duration, 1, 30 days));

        vm.prank(payer);
        try escrow.open(payee, amt, deadline) {
            ghostTotalOpened += amt;
            _count("open.ok");
        } catch {
            _count("open.rejected");
        }
    }

    function release(uint256 dealSeed) external {
        (bool found, uint256 id) = _pickFunded(dealSeed);
        if (!found) return _count("release.skipped");
        (,, uint128 amount,,) = escrow.deals(id);

        vm.prank(arbiter);
        try escrow.release(id) {
            ghostTotalReleased += amount;
            ghostSettledAs[id] = uint8(Escrow.State.Released) + 1;
            _count("release.ok");
        } catch {
            _count("release.rejected");
        }
    }

    function refund(uint256 dealSeed, uint256 callerSeed, bool jumpToDeadline) external {
        (bool found, uint256 id) = _pickFunded(dealSeed);
        if (!found) return _count("refund.skipped");
        (,, uint128 amount, uint64 deadline,) = escrow.deals(id);
        if (jumpToDeadline && vm.getBlockTimestamp() < deadline) vm.warp(deadline);

        // Refund has no caller restriction (ESC-D1): any of the actors may trigger it.
        address caller = callerSeed % 2 == 0
            ? _payers[callerSeed % _payers.length]
            : _payees[callerSeed % _payees.length];
        vm.prank(caller);
        try escrow.refund(id) {
            ghostTotalRefunded += amount;
            ghostSettledAs[id] = uint8(Escrow.State.Refunded) + 1;
            _count("refund.ok");
        } catch {
            _count("refund.rejected");
        }
    }

    /// @dev Tries to settle a deal that is already settled. Must always be refused; if it
    ///      ever succeeds, the terminal-state invariant catches the state change.
    function resettle(uint256 dealSeed, bool asRelease) external {
        uint256 n = escrow.nextId();
        if (n == 0) return _count("resettle.skipped");
        uint256 id = dealSeed % n;
        if (ghostSettledAs[id] == 0) return _count("resettle.skipped");

        if (asRelease) {
            vm.prank(arbiter);
            try escrow.release(id) {
                _count("resettle.SUCCEEDED");
            } catch {
                _count("resettle.rejected");
            }
        } else {
            vm.warp(vm.getBlockTimestamp() + 30 days);
            try escrow.refund(id) {
                _count("resettle.SUCCEEDED");
            } catch {
                _count("resettle.rejected");
            }
        }
    }

    // ---- environment actions -----------------------------------------------------

    function warp(uint256 secondsAhead) external {
        vm.warp(vm.getBlockTimestamp() + bound(secondsAhead, 1, 10 days));
        _count("warp");
    }

    function togglePause() external {
        // Read first: vm.prank applies to the *next* external call, and a view counts.
        bool paused = token.paused();
        vm.prank(pauser);
        if (paused) token.unpause();
        else token.pause();
        _count("togglePause");
    }

    /// @dev Toggles one participant's listing: a payer, a payee, or the escrow itself.
    function toggleListing(uint256 who) external {
        uint256 n = _payers.length + _payees.length + 1;
        uint256 pick = who % n;
        address account = pick < _payers.length
            ? _payers[pick]
            : pick < _payers.length + _payees.length
                ? _payees[pick - _payers.length]
                : address(escrow);

        bool listed = token.isAllowlisted(account);
        vm.prank(compliance);
        token.setAllowlisted(account, !listed);
        _count("toggleListing");
    }

    /// @dev Lifts every control: unpause and re-list all participants. Without it a run
    ///      spends most of its calls frozen, and the invariants are checked against a
    ///      system where nothing moves.
    function restoreControls() external {
        if (token.paused()) {
            vm.prank(pauser);
            token.unpause();
        }
        _relist(address(escrow));
        for (uint256 i; i < _payers.length; ++i) {
            _relist(_payers[i]);
            _relist(_payees[i]);
        }
        _count("restoreControls");
    }

    function _relist(address account) internal {
        if (token.isAllowlisted(account)) return;
        vm.prank(compliance);
        token.setAllowlisted(account, true);
    }

    /// @dev New supply arrives during a run, so the supply invariant isn't a constant.
    function mintToPayer(uint256 payerSeed, uint256 amount) external {
        address payer = _payers[payerSeed % _payers.length];
        uint256 amt = bound(amount, 1, 100_000e6);
        vm.prank(minter);
        try token.mint(payer, amt) {
            ghostTotalMinted += amt;
            _count("mint.ok");
        } catch {
            _count("mint.rejected");
        }
    }

    // ---- internals ---------------------------------------------------------------

    /// @dev Scans from a random start for a Funded deal, so release/refund hit live deals.
    function _pickFunded(uint256 dealSeed) internal view returns (bool, uint256) {
        uint256 n = escrow.nextId();
        if (n == 0) return (false, 0);
        uint256 start = dealSeed % n; // reduce first: dealSeed + i overflows at uint256.max
        for (uint256 i; i < n; ++i) {
            uint256 id = (start + i) % n;
            (,,,, Escrow.State state) = escrow.deals(id);
            if (state == Escrow.State.Funded) return (true, id);
        }
        return (false, 0);
    }

    function _count(string memory action) internal {
        ++calls[keccak256(bytes(action))];
    }

    function count(string calldata action) external view returns (uint256) {
        return calls[keccak256(bytes(action))];
    }
}
