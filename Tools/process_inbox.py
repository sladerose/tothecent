"""Import new statements from Inbox, rebuild the master table and the dashboard.

usage: process_inbox.py [--root PATH] [--dry-run] [--rebuild-only]

--dry-run       parse and check the statements in Inbox, change nothing
--rebuild-only  skip the Inbox and rebuild the master table and dashboard from the raw CSVs and the rules
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from finance_tools.importer import run  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--rebuild-only", action="store_true")
args = ap.parse_args()

res = run(args.root, dry_run=args.dry_run, rebuild_only=args.rebuild_only)
mode = "DRY RUN (nothing changed)" if args.dry_run else "IMPORT"
print(f"{mode}: {res['imported']} statements {'ready' if args.dry_run else 'imported'}, "
      f"{res['duplicates']} duplicates, {res['failed']} failed")
for f in res["files"]:
    print(f"  [{f['status']}] {f['bank']:9s} {f['file']}  {f['period']}  rows={f['rows']}  {f['message']}")
if not args.dry_run:
    print(f"Master: {res.get('master_rows')} transactions, {res.get('master_categorised')} categorised; "
          f"new transactions: {len(res['new_tx'])}; dashboard {res.get('dashboard')}")
for w in res["warnings"]:
    print("  WARNING:", w)
sys.exit(1 if res["failed"] else 0)
