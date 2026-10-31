#!/usr/bin/env bash
# Toolchain bootstrap for Claude cloud sessions.
#
# The cloud workspace blocks foundryup and binaries.soliditylang.org, so this
# script pulls Foundry and solc straight from their GitHub release assets and
# installs Slither from PyPI. CI does NOT use this script; it uses
# foundry-rs/foundry-toolchain as normal.
#
# Idempotent: every step checks what is already installed and skips it.
# Run from anywhere:  bash scripts/cloud-setup.sh
set -euo pipefail

FOUNDRY_VERSION="v1.8.7"
FOUNDRY_SHA256="ba470eb718e779e4b0e3f1520b5284231b1bf98e9a39de07fb8f77ed70525bcf"
SOLC_VERSION="0.8.28"
SOLC_SHA256="9a0fb7e0db2c0641dbae1c5cc645dc686820c83af516226abb1c0a2f76636f25"
SLITHER_VERSION="0.11.4" # match CI: crytic/slither-action can't run 0.11.5+ (F-10)

FOUNDRY_BIN="$HOME/.foundry/bin"
# forge looks for solc X.Y.Z at ~/.svm/X.Y.Z/solc-X.Y.Z before it tries to
# download it, so installing it here lets `solc_version = "0.8.28"` in
# contracts/foundry.toml resolve offline with no machine-specific path.
SOLC_DIR="$HOME/.svm/$SOLC_VERSION"
SOLC_BIN="$SOLC_DIR/solc-$SOLC_VERSION"
GLOBAL_FOUNDRY_TOML="$HOME/.foundry/foundry.toml"
VENV="$HOME/.local/share/tsqa-venv"
LOCAL_BIN="$HOME/.local/bin"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '==> %s\n' "$*"; }

# Prints a command's output, or nothing if it fails. Avoids `cmd | grep -q`,
# which can SIGPIPE `cmd` and trip pipefail.
output_of() { "$@" 2>/dev/null || true; }

if [[ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]]; then
  echo "cloud-setup.sh supports Linux x86_64 only (got $(uname -s)-$(uname -m))." >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

verify_sha256() { # file expected
  local actual
  actual="$(sha256sum "$1" | cut -d' ' -f1)"
  if [[ "$actual" != "$2" ]]; then
    echo "Checksum mismatch for $1: expected $2, got $actual" >&2
    exit 1
  fi
}

# --- Foundry ---------------------------------------------------------------
if [[ "$(output_of "$FOUNDRY_BIN/forge" --version)" == *"Version: ${FOUNDRY_VERSION#v}"* ]]; then
  log "Foundry $FOUNDRY_VERSION already installed"
else
  log "Installing Foundry $FOUNDRY_VERSION"
  curl -fsSL -o "$TMP/foundry.tar.gz" \
    "https://github.com/foundry-rs/foundry/releases/download/${FOUNDRY_VERSION}/foundry_${FOUNDRY_VERSION}_linux_amd64.tar.gz"
  verify_sha256 "$TMP/foundry.tar.gz" "$FOUNDRY_SHA256"
  mkdir -p "$FOUNDRY_BIN"
  tar -xzf "$TMP/foundry.tar.gz" -C "$FOUNDRY_BIN"
fi

# --- solc ------------------------------------------------------------------
if [[ "$(output_of "$SOLC_BIN" --version)" == *"Version: ${SOLC_VERSION}+"* ]]; then
  log "solc $SOLC_VERSION already installed"
else
  log "Installing solc $SOLC_VERSION"
  # ethereum/solidity redirects to argotorg/solidity; -L follows it.
  curl -fsSL -o "$TMP/solc" \
    "https://github.com/ethereum/solidity/releases/download/v${SOLC_VERSION}/solc-static-linux"
  verify_sha256 "$TMP/solc" "$SOLC_SHA256"
  mkdir -p "$SOLC_DIR"
  install -m 0755 "$TMP/solc" "$SOLC_BIN"
fi
mkdir -p "$LOCAL_BIN"
ln -sf "$SOLC_BIN" "$LOCAL_BIN/solc"

# --- Global Foundry config: offline + local solc ---------------------------
# Merged under contracts/foundry.toml, so the committed file stays CI-clean.
log "Writing $GLOBAL_FOUNDRY_TOML (offline = true)"
cat > "$GLOBAL_FOUNDRY_TOML" <<EOF
# Managed by tokenized-settlement-qa/scripts/cloud-setup.sh. Re-running it overwrites this file.
# solc $SOLC_VERSION is preinstalled at $SOLC_BIN, where forge's
# solc_version lookup finds it; offline stops forge from trying to download it.
[profile.default]
offline = true
EOF

# --- Slither ---------------------------------------------------------------
# A venv avoids the system Python's externally-managed-environment guard.
if [[ "$(output_of "$VENV/bin/slither" --version)" == "$SLITHER_VERSION" ]]; then
  log "slither-analyzer $SLITHER_VERSION already installed"
else
  log "Installing slither-analyzer $SLITHER_VERSION"
  [[ -d "$VENV" ]] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet "slither-analyzer==${SLITHER_VERSION}"
fi
ln -sf "$VENV/bin/slither" "$LOCAL_BIN/slither"

# --- Dependencies (forge-std, OpenZeppelin) --------------------------------
log "Initialising git submodules"
git -C "$REPO_ROOT" submodule update --init

# --- PATH ------------------------------------------------------------------
PATH_LINE='export PATH="$HOME/.foundry/bin:$HOME/.local/bin:$PATH"'
if ! grep -qxF "$PATH_LINE" "$HOME/.bashrc" 2>/dev/null; then
  echo "$PATH_LINE" >> "$HOME/.bashrc"
fi
export PATH="$FOUNDRY_BIN:$LOCAL_BIN:$PATH"

# --- Smoke check -----------------------------------------------------------
log "Versions"
forge --version | head -1
solc --version | tail -1
slither --version

log "Done. Open a new shell (or: source ~/.bashrc), then: cd contracts && forge test -vvv"
