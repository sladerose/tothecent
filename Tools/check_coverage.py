"""Sweep the imported statements for gaps.

usage: check_coverage.py RAW_DIR [TODAY]

For every account it reports: gaps between consecutive statements, balance breaks (a closing balance that
differs from the next opening balance), an opening balance that is not zero on the first statement
(earlier history exists but is not held), a closing balance that is not zero where statements stop before
the latest period (a statement is missing or the account was closed without a closing statement), and
accounts whose latest statement is older than expected for their cycle.
"""

import csv
import glob
import os
import sys
from datetime import date, timedelta

raw_dir = sys.argv[1]
today = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
STALE = {"fnb": 40, "nedbank": 40, "discovery": 40, "fnbmm": 40, "fnbpk": 40, "tymecur": 200, "tymesav": 200}

findings, summary = [], []
latest_by_stem = {}
accounts = []
for path in sorted(glob.glob(os.path.join(raw_dir, "*_statements.csv"))):
    stem = os.path.basename(path).replace("_statements.csv", "")
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        continue
    latest_by_stem[stem] = max(r["period_end"] for r in rows)
    by = {}
    for r in rows:
        by.setdefault(r["account_number"], []).append(r)
    for acct, sts in by.items():
        sts.sort(key=lambda r: (r["period_start"], r["period_end"]))
        accounts.append((stem, acct, sts))

for stem, acct, sts in accounts:
    tag = f"{stem} {acct[-4:]}"
    first, last = sts[0], sts[-1]
    if float(first["opening"]) != 0 and stem not in ("tymesav",):
        findings.append((tag, "history", f"first statement starts {first['period_start']} with opening balance {first['opening']}, so earlier history is not held"))
    for a, b in zip(sts, sts[1:]):
        gap = (date.fromisoformat(b["period_start"]) - date.fromisoformat(a["period_end"])).days
        if gap > 1:
            findings.append((tag, "gap", f"no statement from {a['period_end']} to {b['period_start']} ({gap - 1} days)"))
        if float(a["closing"]) != float(b["opening"]):
            findings.append((tag, "balance", f"closing {a['closing']} on {a['period_end']} does not equal next opening {b['opening']}"))
    latest = latest_by_stem[stem]
    if last["period_end"] < latest and float(last["closing"]) != 0:
        findings.append((tag, "stopped", f"last statement ends {last['period_end']} with balance {last['closing']}, but other accounts run to {latest}"))
    age = (today - date.fromisoformat(last["period_end"])).days
    if last["period_end"] >= latest and age > STALE.get(stem, 40):
        findings.append((tag, "stale", f"latest statement ends {last['period_end']}, {age} days ago"))
    summary.append((stem, acct[-4:], len(sts), first["period_start"], last["period_end"], last["closing"]))

print("ACCOUNT COVERAGE")
for s in summary:
    if s[0] == "tymesav" and float(s[5]) == 0:
        continue
    print(f"  {s[0]:8} {s[1]:6} {s[2]:3} statements  {s[3]} to {s[4]}  closing {s[5]}")
closed = sum(1 for s in summary if s[0] == "tymesav" and float(s[5]) == 0)
if closed:
    print(f"  tymesav  ({closed} closed GoalSave pockets with a zero balance not listed)")
print("\nFINDINGS")
if not findings:
    print("  none")
for tag, kind, msg in sorted(findings):
    print(f"  [{kind}] {tag}: {msg}")
