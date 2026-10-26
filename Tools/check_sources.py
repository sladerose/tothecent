r"""Show which statements are held, due or late, using Config\statement_sources.csv.

The last period held comes from Data\raw\<stem>_statements.csv (by last four digits of the
account when one is given). Sources with no raw stem use manual_last_held. A statement is late
when the last period end plus cadence_days plus grace_days is before today.
Usage: python check_sources.py [--root PATH] [--today YYYY-MM-DD] [--write]
"""
import argparse
import csv
import datetime as dt
from pathlib import Path


def last_end(raw_dir, stem, last4):
    path = raw_dir / f"{stem}_statements.csv"
    if not path.exists():
        return None
    ends = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if last4 and not r["account_number"].endswith(last4):
                continue
            ends.append(r["period_end"])
    return max(ends) if ends else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--write", action="store_true", help="also write Data\\sources_status.md")
    a = ap.parse_args()
    root = Path(a.root)
    today = dt.date.fromisoformat(a.today)
    rows = []
    with open(root / "Config" / "statement_sources.csv", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            last = last_end(root / "Data" / "raw", r["raw_stem"], r["raw_last4"]) if r["raw_stem"] else (r["manual_last_held"] or None)
            if r["status"] == "dormant":
                state, due = "dormant", ""
            elif not last:
                state, due = "unknown", ""
            else:
                d = dt.date.fromisoformat(last) + dt.timedelta(days=int(r["cadence_days"]) + int(r["grace_days"]))
                due = d.isoformat()
                state = "late" if d < today else ("due soon" if (d - today).days <= 7 else "ok")
            rows.append((r["source_id"], r["account"], r["owner"], last or "", due, state, r["fetched_by"]))
    out = [f"Statement sources as at {today.isoformat()}", "", "| Source | Account | Owner | Last period held | Expect by | State | Fetched by |", "|---|---|---|---|---|---|---|"]
    out += ["| " + " | ".join(x) + " |" for x in rows]
    late = [x for x in rows if x[5] == "late"]
    out += ["", f"Late: {len(late)}. Unknown: {sum(1 for x in rows if x[5] == 'unknown')}."]
    text = "\n".join(out)
    print(text)
    if a.write:
        (root / "Data" / "sources_status.md").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()