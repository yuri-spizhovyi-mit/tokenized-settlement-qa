// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {EscrowBase} from "../unit/EscrowBase.t.sol";
import {Escrow} from "../../src/Escrow.sol";

/// @notice EIP-712 signed release: the arbiter signs off-chain, anyone submits on-chain,
///         and every replay from book Ch 9.6 is rejected.
/// @dev Requirements (IDs are used in test NatSpec and the RTM):
///      SEC-10  A valid arbiter signature releases the deal for any submitter; funds go
///              only to the payee and the arbiter's nonce advances by one.
///      SEC-11  Replay: the same signature submitted twice fails the second time.
///      SEC-12  Replay: a signature is bound to the chain id.
///      SEC-13  Replay: a signature is bound to this escrow's address.
///      SEC-14  A signature is valid up to and including its deadline, never after.
///      SEC-15  Replay: a signature is bound to one deal id.
///      SEC-16  Only the current arbiter's signature counts; rotation invalidates every
///              signature the previous arbiter issued, even if it is later rotated back.
///
///      The digest is computed here from the EIP-712 spec, not with a contract helper,
///      so a hashing bug in Escrow can't hide behind a shared function.
contract EscrowSignedReleaseTest is EscrowBase {
    bytes32 internal constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    bytes32 internal constant RELEASE_TYPEHASH =
        keccak256("Release(uint256 id,uint256 nonce,uint256 deadline)");

    address internal relayer = makeAddr("relayer"); // anyone; holds no role
    uint256 internal sigDeadline;
    uint256 internal id;

    function setUp() public override {
        super.setUp();
        assertEq(vm.addr(arbiterKey), arbiter, "fixture: arbiter key mismatch");
        id = _open();
        sigDeadline = vm.getBlockTimestamp() + 1 hours;
    }

    // ---- signing helpers -------------------------------------------------------------

    function _domain(address verifyingContract, uint256 chainId) internal pure returns (bytes32) {
        return keccak256(
            abi.encode(
                DOMAIN_TYPEHASH, keccak256("Escrow"), keccak256("1"), chainId, verifyingContract
            )
        );
    }

    function _sign(
        uint256 key,
        address verifyingContract,
        uint256 chainId,
        uint256 dealId,
        uint256 nonce,
        uint256 deadline_
    ) internal pure returns (bytes memory) {
        bytes32 structHash = keccak256(abi.encode(RELEASE_TYPEHASH, dealId, nonce, deadline_));
        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", _domain(verifyingContract, chainId), structHash)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        return abi.encodePacked(r, s, v);
    }

    /// @dev The common case: the arbiter signs deal `dealId` for this escrow and chain.
    function _arbiterSig(uint256 dealId) internal view returns (bytes memory) {
        return _sign(
            arbiterKey, address(escrow), block.chainid, dealId, escrow.nonces(arbiter), sigDeadline
        );
    }

    // ---- SEC-10: the happy path -----------------------------------------------------

    /// @notice SEC-10: a relayer with no role submits the arbiter's signature; the payee
    ///         is paid, the relayer gains nothing, and the nonce advances.
    function test_ReleaseWithSig_AnyoneCanSubmit() public {
        bytes memory sig = _arbiterSig(id);
        Balances memory before = _balances();

        vm.expectEmit(true, true, false, true, address(escrow));
        emit Escrow.Released(id, payee, AMOUNT);
        vm.prank(relayer);
        escrow.releaseWithSig(id, sigDeadline, sig);

        assertEq(token.balanceOf(payee), before.payee + AMOUNT, "payee");
        assertEq(token.balanceOf(relayer), 0, "relayer gained something");
        assertEq(escrow.nonces(arbiter), 1, "nonce not consumed");
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Released);
    }

    // ---- the five replay tests (book Ch 9.6) ------------------------------------------

    /// @notice SEC-11 (replay 1/5): the same signature twice. The second submission is
    ///         rejected by the nonce, before the deal's state is even looked at. That
    ///         matters for any future signed action that isn't one-shot.
    function test_RevertWhen_SameSignatureSubmittedTwice() public {
        bytes memory sig = _arbiterSig(id);
        vm.prank(relayer);
        escrow.releaseWithSig(id, sigDeadline, sig);

        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        escrow.releaseWithSig(id, sigDeadline, sig);

        assertEq(escrow.nonces(arbiter), 1, "failed replay consumed a nonce");
    }

    /// @notice SEC-12 (replay 2/5): a signature made for another chain id (a fork,
    ///         an L2 deployment) is rejected here.
    function test_RevertWhen_SignatureFromOtherChain() public {
        bytes memory foreign = _sign(
            arbiterKey, address(escrow), block.chainid + 1, id, escrow.nonces(arbiter), sigDeadline
        );
        Balances memory before = _balances();

        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        escrow.releaseWithSig(id, sigDeadline, foreign);

        _assertBalancesUnchanged(before);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    /// @notice SEC-12 (replay 2/5, other direction): after a chain split the same
    ///         contract sees a new chain id; signatures for the old id stop working.
    function test_RevertWhen_ChainIdChangesAfterSigning() public {
        bytes memory sig = _arbiterSig(id);
        vm.chainId(block.chainid + 1);

        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        escrow.releaseWithSig(id, sigDeadline, sig);
    }

    /// @notice SEC-13 (replay 3/5): a second escrow with the same arbiter and the same
    ///         deal id 0 rejects a signature made for the first escrow.
    function test_RevertWhen_SignatureForOtherEscrow() public {
        Escrow other = new Escrow(token, arbiter, escrowAdmin);
        vm.prank(compliance);
        token.setAllowlisted(address(other), true);
        vm.prank(payer);
        token.approve(address(other), type(uint256).max);
        vm.prank(payer);
        uint256 otherId = other.open(payee, AMOUNT, deadline);
        assertEq(otherId, id, "fixture: same deal id in both escrows");

        bytes memory sigForFirst = _arbiterSig(id);

        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        other.releaseWithSig(otherId, sigDeadline, sigForFirst);

        (,,,, Escrow.State st) = other.deals(otherId);
        assertEq(uint8(st), uint8(Escrow.State.Funded));
    }

    /// @notice SEC-14 (replay 4/5): one second past its deadline, a signature is dead,
    ///         and the error names the deadline. At the deadline itself it still works.
    function test_RevertWhen_SignatureExpired() public {
        bytes memory sig = _arbiterSig(id);

        vm.warp(sigDeadline + 1);
        vm.prank(relayer);
        vm.expectRevert(abi.encodeWithSelector(Escrow.SignatureExpired.selector, sigDeadline));
        escrow.releaseWithSig(id, sigDeadline, sig);
        assertEq(escrow.nonces(arbiter), 0);

        vm.warp(sigDeadline);
        vm.prank(relayer);
        escrow.releaseWithSig(id, sigDeadline, sig);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Released);
    }

    /// @notice SEC-14: the submitter can't extend a signature by passing a later
    ///         deadline; the deadline is part of what was signed.
    function test_RevertWhen_SubmitterAltersDeadline() public {
        bytes memory sig = _arbiterSig(id);
        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        escrow.releaseWithSig(id, sigDeadline + 1 days, sig);
    }

    /// @notice SEC-15 (replay 5/5): a signature for deal 0 can't release deal 1, even
    ///         with the same nonce and deadline.
    function test_RevertWhen_SignatureReusedForDifferentDeal() public {
        vm.prank(payer);
        uint256 otherDeal = escrow.open(stranger, AMOUNT, deadline);
        bytes memory sigForFirst = _arbiterSig(id);

        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        escrow.releaseWithSig(otherDeal, sigDeadline, sigForFirst);

        _assertDeal(otherDeal, payer, stranger, AMOUNT, deadline, Escrow.State.Funded);
        assertEq(escrow.nonces(arbiter), 0, "failed attempt consumed the nonce");

        // The signature is still good for the deal it was made for.
        vm.prank(relayer);
        escrow.releaseWithSig(id, sigDeadline, sigForFirst);
        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Released);
    }

    // ---- signer checks -----------------------------------------------------------------

    /// @notice SEC-16: a well-formed signature from anyone but the arbiter is rejected.
    function test_RevertWhen_SignedByNonArbiter() public {
        (, uint256 strangerKey) = makeAddrAndKey("stranger");
        bytes memory sig = _sign(strangerKey, address(escrow), block.chainid, id, 0, sigDeadline);

        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        escrow.releaseWithSig(id, sigDeadline, sig);
    }

    /// @notice SEC-16: a malleated (high-s) copy of a valid signature is rejected, so a
    ///         relayer can't mint a second "valid" signature from the first.
    function test_RevertWhen_SignatureIsMalleated() public {
        bytes memory sig = _arbiterSig(id);
        (bytes32 r, bytes32 s, uint8 v) = _split(sig);
        uint256 n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
        bytes memory malleated = abi.encodePacked(r, bytes32(n - uint256(s)), v == 27 ? 28 : 27);

        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        escrow.releaseWithSig(id, sigDeadline, malleated);
    }

    /// @notice SEC-16 / SEC-08: rotating the arbiter kills every signature the old one
    ///         issued, and rotating it back does not revive them.
    function test_RevertWhen_SignatureFromRotatedOutArbiter() public {
        bytes memory oldSig = _arbiterSig(id);
        address newArbiter = makeAddr("newArbiter");

        vm.prank(escrowAdmin);
        escrow.setArbiter(newArbiter);
        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        escrow.releaseWithSig(id, sigDeadline, oldSig);

        vm.prank(escrowAdmin);
        escrow.setArbiter(arbiter); // rotate back
        vm.prank(relayer);
        vm.expectRevert(Escrow.InvalidSignature.selector);
        escrow.releaseWithSig(id, sigDeadline, oldSig);

        _assertDeal(id, payer, payee, AMOUNT, deadline, Escrow.State.Funded);
    }

    function _split(bytes memory sig) internal pure returns (bytes32 r, bytes32 s, uint8 v) {
        assembly {
            r := mload(add(sig, 32))
            s := mload(add(sig, 64))
            v := byte(0, mload(add(sig, 96)))
        }
    }
}
