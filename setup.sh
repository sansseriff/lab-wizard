#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$REPO_ROOT/lab_wizard/wizard/frontend"

# ask "Question? [Y/n] " -> succeeds unless the answer is no. With no terminal
# to answer from (e.g. CI), takes the default yes.
ask() {
    local answer=""
    read -rp "$1" answer || answer=""
    [[ ! "$answer" =~ ^[Nn] ]]
}

# --- 1. uv (Python package manager) ---

echo ""
echo "This project uses 'uv' as its Python package manager."
echo "  https://docs.astral.sh/uv/"
echo ""
if command -v uv &>/dev/null; then
    echo "uv is already installed ($(uv --version)). Skipping."
else
    if ! ask "Install uv? [Y/n] "; then
        echo "uv is required to set up this project. Exiting."
        exit 1
    fi
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer puts uv in ~/.local/bin (or $XDG_BIN_HOME); use it from this shell
    export PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$HOME/.local/bin:$PATH"
    echo "uv installed. Open a new terminal to use 'uv' outside this script."
fi

# --- 1b. Create .venv with uv sync ---

echo ""
echo "Creating virtual environment and installing Python dependencies..."
cd "$REPO_ROOT"
uv sync

# --- 2. Bun + frontend build ---

echo ""
echo "Installing bun (JavaScript runtime)..."

if command -v bun &>/dev/null; then
    CURRENT_BUN="$(bun --version)"
    echo "bun is already installed (v${CURRENT_BUN})."

    # Check if an upgrade is available
    LATEST_BUN="$(curl -fsSL https://github.com/oven-sh/bun/releases/latest -o /dev/null -w '%{url_effective}' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
    if [[ -n "$LATEST_BUN" && "$CURRENT_BUN" != "$LATEST_BUN" ]]; then
        echo "A newer version of bun is available (v${LATEST_BUN})."
        if ask "Upgrade bun? [Y/n] "; then
            bun upgrade
        fi
    else
        echo "bun is up to date."
    fi
else
    curl -fsSL https://bun.sh/install | bash
    # Source bun into the current shell so we can use it immediately
    export BUN_INSTALL="$HOME/.bun"
    export PATH="$BUN_INSTALL/bin:$PATH"
fi

echo ""
echo "Installing frontend dependencies..."
cd "$FRONTEND_DIR"
bun install

echo ""
echo "Building frontend (output -> lab_wizard/wizard/backend/static/)..."
bun run ./build.ts

# --- 3. macOS: app bundle for the Dock icon ---

# macOS shows the icon of the app bundle a window runs from; without one the
# wizard's window gets the icon of the terminal that launched it.
if [[ "$(uname)" == "Darwin" ]]; then
    echo ""
    echo "Building Lab Wizard.app (gives the wizard window its Dock icon)..."
    cd "$REPO_ROOT"
    uv run python -m lab_wizard.wizard.backend.macos_app \
        || echo "Could not build Lab Wizard.app; the wizard still works, with a generic Dock icon."
fi

# --- 4. Workspace (config/, projects/, data/, measurements/) ---

echo ""
echo "Initializing the Lab Wizard workspace in the repository root..."
cd "$REPO_ROOT"
uv run wizard init .

echo ""
echo "Setup complete. Launch the UI with 'uv run wizard'."
