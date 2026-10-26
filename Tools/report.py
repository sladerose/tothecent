r"""Read only status report: what needs the user's attention, from the files the importer already wrote.

usage: python report.py [--root PATH] [--today YYYY-MM-DD] [--top N]

Changes nothing. Exit code 0 when nothing needs attention, 1 when something does.
Reuses the importer's own checks (balance chain, gaps) and the dashboard's rules for the two
judgements the dashboard also shows: a bank is stale when its last statement is more than 40 days
old, and uncategorised rows are judged against uncategorised_share_pct in Config\targets.csv.
"""
import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from finance_tools import household
from finance_tools.importer import BANKS, check_chain, find_conflicted_copies, read_csv, stale_warnings
from check_sources import last_end

DASHBOARD_STALE_DAYS = 40
LOCK_STALE_HOURS = 6


def targets(root: str) -> dict:
    return {r["key"]: r["value"] for r in read_csv(os.path.join(root, "Config", "targets.csv"))}


def run_state(root: str) -> list[str]:
    """Problems with the last run: a failed dashboard build, a held lock, sync conflict copies."""
    out = []
    last_run = os.path.join(root, "Data", "last_run.md")
    if not os.path.exists(last_run):
        out.append("Data\\last_run.md is missing: the importer has not run in this folder.")
    else:
        with open(last_run, encoding="utf-8") as f:
            for line in f:
                if "Dashboard: FAILED" in line or "Dashboard: not built" in line:
                    out.append(line.strip())
                elif "Dashboard:" in line and "rebuilt" not in line:
                    out.append(line.strip())
    lock = os.path.join(root, "Data", ".import.lock")
    if os.path.exists(lock):
        age = (date.today() - date.fromtimestamp(os.path.getmtime(lock))).days
        out.append(f"Data\\.import.lock exists (modified {age} day(s) ago). An import is running or stopped half way.")
    out += [f"Sync conflict copy: {p}" for p in find_conflicted_copies(root)]
    return out


def inbox_left(root: str) -> list[str]:
    """PDFs still in the Inbox, with the last message the importer logged for the same file."""
    log = {}
    for r in read_csv(os.path.join(root, "Data", "import_log.csv")):
        log[r["file"]] = f"{r['status']}: {r['message']}"
    out = []
    inbox = os.path.join(root, "Inbox")
    for folder, dirs, names in os.walk(inbox):
        dirs[:] = [d for d in dirs if d != "_duplicates"]
        for n in sorted(names):
            if n.lower().endswith(".pdf"):
                out.append(f"{os.path.relpath(os.path.join(folder, n), inbox)}: " + log.get(n, "no importer message yet"))
    return out


def statements(root: str) -> list[dict]:
    raw = os.path.join(root, "Data", "raw")
    rows = []
    for bank, d in BANKS.items():
        for r in read_csv(os.path.join(raw, f"{d['stem']}_statements.csv")):
            rows.append(dict(r, bank=bank))
    return rows


def unreconciled(sts: list[dict]) -> list[str]:
    return [f"{s['bank']}: {s['file']} ({s['period_start']} to {s['period_end']}) {s['problems']}".strip()
            for s in sts if str(s["reconciled"]).lower() != "true"]


def stale(sts: list[dict], today: date) -> list[str]:
    """The dashboard rule: a bank is stale when its last statement ends more than 40 days ago.
    Loans are not in the dashboard bank list, so they follow the importer's own rule."""
    out = stale_warnings([s for s in sts if BANKS[s["bank"]].get("type") == "loan"], today)
    for bank, d in BANKS.items():
        if d.get("type") == "loan":
            continue
        # The dashboard lists each account of a bank, except the pooled TymeBank GoalSave pockets.
        ends = defaultdict(list)
        for s in sts:
            if s["bank"] == bank:
                ends[None if d.get("multi") else s["account_number"]].append(s["period_end"])
        for acct, ee in sorted(ends.items(), key=lambda kv: str(kv[0])):
            days = (today - date.fromisoformat(max(ee))).days
            if days > DASHBOARD_STALE_DAYS:
                out.append(f"{bank}{'' if acct is None else ' account ending ' + acct[-4:]}: statements end {max(ee)} ({days} days ago)")
    return out


def late_sources(root: str, today: date) -> list[str]:
    path = os.path.join(root, "Config", "statement_sources.csv")
    out = []
    for r in read_csv(path):
        if r["status"] == "dormant":
            continue
        raw = os.path.join(root, "Data", "raw")
        from pathlib import Path
        last = last_end(Path(raw), r["raw_stem"], r["raw_last4"]) if r["raw_stem"] else (r["manual_last_held"] or None)
        if not last:
            out.append(f"{r['account']}: no statement held yet")
            continue
        due = date.fromisoformat(last).toordinal() + int(r["cadence_days"]) + int(r["grace_days"])
        if due < today.toordinal():
            out.append(f"{r['account']}: last period {last}, expected by {date.fromordinal(due).isoformat()}")
    return out


def uncategorised(root: str, today: date, top: int):
    path = os.path.join(root, "Data", "master", "transactions.csv")
    rows = read_csv(path)
    judged = [r for r in rows if not r["category"].startswith("Transfer:")]
    unc = [r for r in rows if not r["category"]]
    share = (len(unc) / len(judged) * 100) if judged else 0.0
    by_key = defaultdict(lambda: [0, 0.0])
    for r in unc:
        k = by_key[r["merchant_key"] or r["description"][:40]]
        k[0] += 1
        k[1] += float(r["amount"])
    ranked = sorted(by_key.items(), key=lambda kv: (-kv[1][0], kv[0]))[:top]
    return len(rows), len(unc), share, ranked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    root, today = a.root, date.fromisoformat(a.today)
    household.apply(os.path.join(root, "Config"), BANKS)

    sts = statements(root)
    sections = [
        ("Run state", run_state(root)),
        ("Statements left in the Inbox", inbox_left(root)),
        ("Statements that did not reconcile", unreconciled(sts)),
        ("Gaps and balance breaks between statements", check_chain(sts)),
        ("Stale banks (more than 40 days since the last statement)", stale(sts, today)),
        ("Statement sources that are late", late_sources(root, today)),
    ]
    attention = 0
    print(f"Finance report as at {today.isoformat()}")
    for title, items in sections:
        print(f"\n{title}: {len(items)}")
        for i in items:
            print(f"  {i}")
        attention += len(items)

    total, unc, share, ranked = uncategorised(root, today, a.top)
    limit = targets(root).get("uncategorised_share_pct", "")
    over = bool(limit) and share > float(limit)
    print(f"\nNot yet categorised: {unc} of {total} rows, {share:.1f} percent of rows that are not transfers"
          + (f" (target {limit} percent, {'over' if over else 'within'})" if limit else " (no target set)"))
    for key, (n, amt) in ranked:
        print(f"  {n:4d} rows  {amt:12,.2f}  {key}")
    attention += 1 if unc else 0
    print(f"\nItems needing attention: {attention}")
    return 1 if attention else 0


if __name__ == "__main__":
    sys.exit(main())
