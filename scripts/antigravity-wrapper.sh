#!/usr/bin/env bash
# ==============================================================================
# Antigravity Non-Interactive Shield (Epic 6.0)
# Prevents pipeline deadlocks from CLI tools prompting the user for input.
# ==============================================================================

set -o pipefail

export TERM=dumb
export DEBIAN_FRONTEND=noninteractive

COMMAND=$1
shift

if [[ "$COMMAND" == "prisma" ]]; then
    if [[ "$1" == "db" && "$2" == "push" ]]; then
        echo "[Antigravity Shield] Auto-appending --accept-data-loss to prisma db push"
        npx prisma db push --accept-data-loss "$@"
    else
        npx prisma "$@"
    fi
elif [[ "$COMMAND" == "git" ]]; then
    # Prevent git from waiting on terminal for SSH or GPG prompts
    export GIT_TERMINAL_PROMPT=0
    git "$@"
elif [[ "$COMMAND" == "apt" || "$COMMAND" == "apt-get" ]]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get -yq "$@"
else
    # Execute generic command
    "$COMMAND" "$@"
fi
