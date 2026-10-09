#!/usr/bin/env bash
# Thin launcher: ./ledger.sh sync | comment <pr> | report | setup
export LEDGER_CWD="$PWD"
cd "$(dirname "${BASH_SOURCE[0]}")" && exec python3 -m ledger "$@"
