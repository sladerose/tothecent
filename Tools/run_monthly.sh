#!/bin/sh
# Monthly import. Imports whatever is waiting in Inbox, rebuilds the master table and the dashboard,
# and writes Data/last_run.md. Shell equivalent of run_monthly.ps1. Pass --dry-run to change nothing.
# Python comes from the virtual environment named in FINANCE_TOOLS_VENV, else ~/.finance-tools/venv (created by Tools/setup.py).
here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
venv=${FINANCE_TOOLS_VENV:-$HOME/.finance-tools/venv}
py="$venv/bin/python"
[ -x "$py" ] || { echo "No Python at $py. Set FINANCE_TOOLS_VENV to the virtual environment folder." >&2; exit 2; }
exec "$py" "$here/process_inbox.py" --root "$root" "$@"
