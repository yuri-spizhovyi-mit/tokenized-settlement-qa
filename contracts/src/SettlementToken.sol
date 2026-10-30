// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

/// @title SettlementToken
/// @notice Permissioned 6-decimal settlement asset. Only allowlisted accounts may
///         hold, send or spend it; an issuer mints and redeems; a pauser can stop all
///         balance movements in an incident.
/// @dev Roles:
///      - DEFAULT_ADMIN_ROLE: governance. Grants and revokes every role; holds no
///        operational power itself.
///      - MINTER_ROLE: mints to allowlisted accounts and burns its own balance
///        (redemption: a holder transfers to the issuer, the issuer burns).
///      - PAUSER_ROLE: pauses and unpauses.
///      - ALLOWLIST_ROLE: compliance. Adds and removes accounts from the allowlist.
///      Governance and compliance are separate duties: the admin cannot change the
///      allowlist unless it explicitly (and visibly, via RoleGranted) grants itself
///      ALLOWLIST_ROLE.
///
///      Every balance change in OpenZeppelin 5.x ERC20 (transfer, transferFrom, mint,
///      burn) goes through `_update`, so the pause and allowlist checks live there and
///      cannot be bypassed by a code path that skips an overridden public function.
contract SettlementToken is ERC20, AccessControl, Pausable {
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
    bytes32 public constant ALLOWLIST_ROLE = keccak256("ALLOWLIST_ROLE");

    mapping(address account => bool) private _allowlisted;

    event AllowlistUpdated(address indexed account, bool allowed);

    /// @notice `account` took part in a balance movement without being allowlisted.
    error AccountNotAllowlisted(address account);
    /// @notice The constructor was given the zero address as admin.
    error InvalidAdmin(address admin);
    /// @notice The zero address cannot be put on or taken off the allowlist.
    error InvalidAccount(address account);

    constructor(address admin) ERC20("Settlement Token", "STK") {
        if (admin == address(0)) revert InvalidAdmin(admin);
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function isAllowlisted(address account) public view returns (bool) {
        return _allowlisted[account];
    }

    // ---- privileged ---------------------------------------------------------

    function mint(address to, uint256 value) external onlyRole(MINTER_ROLE) {
        _mint(to, value);
    }

    function burn(uint256 value) external onlyRole(MINTER_ROLE) {
        _burn(_msgSender(), value);
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function setAllowlisted(address account, bool allowed) external onlyRole(ALLOWLIST_ROLE) {
        if (account == address(0)) revert InvalidAccount(account);
        _allowlisted[account] = allowed;
        emit AllowlistUpdated(account, allowed);
    }

    // ---- enforcement ---------------------------------------------------------

    /// @dev Checks run in this order: pause, sender, spender, recipient.
    ///      - `from == 0` is a mint and `to == 0` a burn. The zero address can never be
    ///        allowlisted, so that side is skipped.
    ///      - If the caller is not `from`, it is spending an allowance (transferFrom)
    ///        and must be allowlisted too. A mint has `from == 0`, so the minter itself
    ///        needs no listing; a burn has `from == caller`.
    function _update(address from, address to, uint256 value) internal override {
        _requireNotPaused();

        if (from != address(0)) {
            _requireAllowlisted(from);
            address spender = _msgSender();
            if (spender != from) _requireAllowlisted(spender);
        }
        if (to != address(0)) _requireAllowlisted(to);

        super._update(from, to, value);
    }

    function _requireAllowlisted(address account) private view {
        if (!_allowlisted[account]) revert AccountNotAllowlisted(account);
    }
}
