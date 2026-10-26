# Monthly import for Task Scheduler. Imports whatever is waiting in Inbox, rebuilds the master table
# and the dashboard, and writes Data\last_run.md. Needs no input.
# Python comes from FINANCE_TOOLS_VENV, else ~\.finance-tools\venv (created by Tools\setup.py).
$venv = if ($env:FINANCE_TOOLS_VENV) { $env:FINANCE_TOOLS_VENV } else { Join-Path $HOME ".finance-tools\venv" }
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Error "No Python at $python. Run Tools\setup.py first or set FINANCE_TOOLS_VENV."; exit 2 }
$root = Split-Path $PSScriptRoot -Parent
& $python "$PSScriptRoot\process_inbox.py" --root $root
exit $LASTEXITCODE
