// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @title Escrow
/// @notice Holds a settlement token for a deal until the arbiter releases it to the
///         payee, or, once the deadline has passed, anyone refunds it to the payer.
/// @dev Every state-changing function follows checks-effects-interactions: the deal's
///      state is written and the event emitted before the token is called, so a
///      re-entrant call finds the deal already settled and reverts with WrongState.
///
///      The escrow inherits the token's controls. With SettlementToken, the escrow
///      contract itself must be allowlisted, and a paused token stops open, release and
///      refund. A payout the token refuses reverts the whole call, so the deal stays
///      Funded and the funds stay here.
///
///      Decisions (refund caller, deadline boundaries, input validation) are recorded in
///      docs/decisions.md.
contract Escrow {
    using SafeERC20 for IERC20;

    enum State {
        Funded,
        Released,
        Refunded
    }

    struct Deal {
        address payer;
        address payee;
        uint128 amount;
        uint64 deadline;
        State state;
    }

    IERC20 public immutable token;
    address public immutable arbiter;

    /// @notice The id the next `open` will assign. Every id below it exists.
    uint256 public nextId;
    mapping(uint256 id => Deal) public deals;

    event Opened(
        uint256 indexed id,
        address indexed payer,
        address indexed payee,
        uint128 amount,
        uint64 deadline
    );
    event Released(uint256 indexed id, address indexed payee, uint128 amount);
    event Refunded(uint256 indexed id, address indexed payer, uint128 amount);

    /// @notice Only the arbiter may release.
    error NotArbiter();
    /// @notice The deal is not Funded; `state` is what was found.
    error WrongState(State state);
    /// @notice Refund before the deadline; retry at or after `deadline`.
    error TooEarly(uint64 deadline);
    /// @notice The id was never opened. Without this check it would read as an empty
    ///         deal in state Funded (the enum's zero value).
    error UnknownDeal(uint256 id);
    /// @notice A deal must move a non-zero amount.
    error ZeroAmount();
    /// @notice `payee` could never be paid: the zero address or this escrow.
    error InvalidPayee(address payee);
    /// @notice `deadline` is not in the future, so the deal would be refundable at once.
    error DeadlineInPast(uint64 deadline);
    /// @notice The constructor was given the zero address for the token or arbiter.
    error ZeroAddress();

    constructor(IERC20 token_, address arbiter_) {
        if (address(token_) == address(0) || arbiter_ == address(0)) revert ZeroAddress();
        token = token_;
        arbiter = arbiter_;
    }

    /// @notice Opens a deal and pulls `amount` from the caller (the payer) into escrow.
    ///         The caller must have approved this contract for at least `amount`, and
    ///         `deadline` must be strictly in the future.
    function open(address payee, uint128 amount, uint64 deadline) external returns (uint256 id) {
        if (amount == 0) revert ZeroAmount();
        if (payee == address(0) || payee == address(this)) revert InvalidPayee(payee);
        // forge-lint: disable-next-line(block-timestamp) day-scale deadlines; see docs/decisions.md
        if (deadline <= block.timestamp) revert DeadlineInPast(deadline);

        id = nextId++;
        deals[id] = Deal(msg.sender, payee, amount, deadline, State.Funded);
        emit Opened(id, msg.sender, payee, amount, deadline);

        token.safeTransferFrom(msg.sender, address(this), amount);
    }

    /// @notice Pays the deal's amount to the payee. Arbiter only; any time while Funded.
    function release(uint256 id) external {
        if (msg.sender != arbiter) revert NotArbiter();
        Deal storage deal = _funded(id);

        deal.state = State.Released;
        emit Released(id, deal.payee, deal.amount);

        token.safeTransfer(deal.payee, deal.amount);
    }

    /// @notice Returns the deal's amount to the payer. Anyone may call it once
    ///         `block.timestamp >= deadline`; the funds only ever go to the payer.
    function refund(uint256 id) external {
        Deal storage deal = _funded(id);
        // forge-lint: disable-next-line(block-timestamp) day-scale deadlines; see docs/decisions.md
        if (block.timestamp < deal.deadline) revert TooEarly(deal.deadline);

        deal.state = State.Refunded;
        emit Refunded(id, deal.payer, deal.amount);

        token.safeTransfer(deal.payer, deal.amount);
    }

    /// @dev Existence first, then state: a settled deal reports WrongState, not TooEarly.
    function _funded(uint256 id) private view returns (Deal storage deal) {
        if (id >= nextId) revert UnknownDeal(id);
        deal = deals[id];
        if (deal.state != State.Funded) revert WrongState(deal.state);
    }
}
