#!/usr/bin/env sh
# Uruchamia PyLinksWeb ze środowiska .venv (patrz install.sh).
#   ./run.sh            # normalnie
#   ./run.sh --debug    # DevTools + log startu na stderr
set -eu
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Brak środowiska .venv — uruchom najpierw ./install.sh" >&2
    exit 1
fi

exec .venv/bin/python main.py "$@"
