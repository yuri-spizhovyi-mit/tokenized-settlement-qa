// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {
    AccessControlDefaultAdminRules
} from "@openzeppelin/contracts/access/extensions/AccessControlDefaultAdminRules.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {Nonces} from "@openzeppelin/contracts/utils/Nonces.sol";

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
///      Roles (PR 5):
///      - arbiter: releases deals, directly or by an EIP-712 signature anyone can submit.
///      - DEFAULT_ADMIN_ROLE: rotates the arbiter (issue #7). One holder, two-step
///        handover with ADMIN_DELAY (AccessControlDefaultAdminRules), and no release
///        power of its own.
///
///      Decisions (refund caller, deadline boundaries, input validation, signed release)
///      are recorded in docs/decisions.md.
contract Escrow is AccessControlDefaultAdminRules, EIP712, Nonces {
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

    /// @notice EIP-712 struct for a signed release. `nonce` is the arbiter's current
    ///         nonce; `deadline` is when the signature stops working (inclusive).
    bytes32 public constant RELEASE_TYPEHASH =
        keccak256("Release(uint256 id,uint256 nonce,uint256 deadline)");

    /// @notice Minimum wait for an escrow-admin handover (SEC-D2).
    uint48 public constant ADMIN_DELAY = 2 days;

    IERC20 public immutable token;
    address public arbiter;

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
    event ArbiterChanged(address indexed previousArbiter, address indexed newArbiter);

    /// @notice Only the arbiter may release.
    error NotArbiter();
    /// @notice The deal is not Funded; `state` is what was found.
    error WrongState(State state);
    /// @notice Refund before the deadline; retry at or after `deadline`.
    error TooEarly(uint64 deadline);
    /// @notice The signature is malformed, malleated, or not the current arbiter's for
    ///         this deal, nonce, deadline, chain and escrow.
    error InvalidSignature();
    /// @notice The signature's deadline has passed.
    error SignatureExpired(uint256 deadline);
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

    /// @dev A zero admin reverts with AccessControlInvalidDefaultAdmin(address(0)) from
    ///      the OpenZeppelin base constructor, before the checks below.
    constructor(IERC20 token_, address arbiter_, address admin_)
        AccessControlDefaultAdminRules(ADMIN_DELAY, admin_)
        EIP712("Escrow", "1")
    {
        if (address(token_) == address(0) || arbiter_ == address(0)) revert ZeroAddress();
        token = token_;
        arbiter = arbiter_;
        emit ArbiterChanged(address(0), arbiter_);
    }

    /// @notice Replaces the arbiter. Takes effect in this block: the previous arbiter
    ///         can no longer release, and every signature it issued is void, because
    ///         its nonce is consumed here (so rotating back doesn't revive them).
    function setArbiter(address newArbiter) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newArbiter == address(0)) revert ZeroAddress();
        address previous = arbiter;
        _useNonce(previous);
        arbiter = newArbiter;
        emit ArbiterChanged(previous, newArbiter);
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
        _release(id);
    }

    /// @notice Releases with the arbiter's EIP-712 signature; anyone may submit it.
    /// @dev Checks run in this order: deadline, signature (bound to deal id, the
    ///      arbiter's current nonce, deadline, chain id and this contract), then the
    ///      deal. The nonce is consumed before the release; if the release reverts, the
    ///      whole call reverts and the nonce is restored.
    function releaseWithSig(uint256 id, uint256 deadline, bytes calldata signature) external {
        // forge-lint: disable-next-line(block-timestamp) hour-scale deadlines; see docs/decisions.md
        if (block.timestamp > deadline) revert SignatureExpired(deadline);

        address signer = arbiter;
        bytes32 digest =
            _hashTypedDataV4(keccak256(abi.encode(RELEASE_TYPEHASH, id, nonces(signer), deadline)));
        (address recovered, ECDSA.RecoverError err,) = ECDSA.tryRecover(digest, signature);
        if (err != ECDSA.RecoverError.NoError || recovered != signer) revert InvalidSignature();
        _useNonce(signer);

        _release(id);
    }

    /// @notice The EIP-712 domain separator, for off-chain signers.
    function DOMAIN_SEPARATOR() external view returns (bytes32) {
        return _domainSeparatorV4();
    }

    function _release(uint256 id) private {
        Deal storage deal = _funded(id);

        deal.state = State.Released;
        // Only call before this on the signed path is ecrecover: a precompile staticcall.
        // forge-lint: disable-next-line(reentrancy-events) false positive, see line above
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
