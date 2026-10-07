#!/usr/bin/env bash
# One-command install: clone Lab Wizard (or update an existing checkout) and run setup.sh.
#
#   curl -fsSL https://raw.githubusercontent.com/sansseriff/lab-wizard/master/install.sh | bash
#
# Run it from the folder that should hold the checkout. Run it inside an existing
# checkout to pull the latest and set up again.
set -euo pipefail

REPO_URL="https://github.com/sansseriff/lab-wizard.git"
DIR_NAME="lab-wizard"

if ! command -v git &>/dev/null; then
    echo "Lab Wizard needs git: https://git-scm.com/downloads" >&2
    exit 1
fi

is_checkout() {
    [[ -f "$1/setup.sh" && -d "$1/lab_wizard" ]] && git -C "$1" rev-parse --is-inside-work-tree &>/dev/null
}

if is_checkout "$PWD"; then
    REPO_ROOT="$PWD"
elif is_checkout "$PWD/$DIR_NAME"; then
    REPO_ROOT="$PWD/$DIR_NAME"
else
    REPO_ROOT=""
fi

if [[ -n "$REPO_ROOT" ]]; then
    echo "Updating the existing checkout at $REPO_ROOT..."
    git -C "$REPO_ROOT" pull --ff-only
else
    REPO_ROOT="$PWD/$DIR_NAME"
    if [[ -e "$REPO_ROOT" ]]; then
        echo "$REPO_ROOT already exists and is not a Lab Wizard checkout. Move it, or run this from another folder." >&2
        exit 1
    fi
    echo "Cloning $REPO_URL into $REPO_ROOT..."
    git clone "$REPO_URL" "$REPO_ROOT"
fi

# Piped into bash, this script's stdin is the script itself; give setup.sh the
# terminal so its prompts read the keyboard.
if [[ -r /dev/tty ]] && { : </dev/tty; } 2>/dev/null; then
    bash "$REPO_ROOT/setup.sh" </dev/tty
else
    bash "$REPO_ROOT/setup.sh" </dev/null
fi

echo ""
echo "To start Lab Wizard:"
echo "  cd \"$REPO_ROOT\""
echo "  uv run wizard"
