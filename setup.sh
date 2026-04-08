#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$REPO_ROOT/lab_wizard/wizard/frontend"

# --- 1. uv (Python package manager) ---

echo ""
echo "This project uses 'uv' as its Python package manager."
echo "  https://docs.astral.sh/uv/"
echo ""
read -rp "Install uv? [Y/n] " answer
answer="${answer:-Y}"

if [[ "$answer" =~ ^[Nn]$ ]]; then
    echo "uv is required to set up this project. Exiting."
    exit 1
fi

if command -v uv &>/dev/null; then
    echo "uv is already installed ($(uv --version)). Skipping."
else
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "uv installed. You may need to restart your shell or source your profile."
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
        read -rp "Upgrade bun? [Y/n] " bun_answer
        bun_answer="${bun_answer:-Y}"
        if [[ ! "$bun_answer" =~ ^[Nn]$ ]]; then
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

echo ""
echo "Setup complete. You can now run the wizard ui with 'uv run wizard', or just 'wizard' if the virtual environment is activated."
