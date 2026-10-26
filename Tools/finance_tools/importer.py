"""Monthly statement importer.

Reads new statement PDFs from Inbox, parses and reconciles each one, appends the accepted ones to
the raw CSVs, files the PDFs in the read-only archive, rebuilds the master table and the dashboard,
and writes a log. A statement that fails a check is reported and left in the Inbox.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import date, datetime, timedelta

import pdfplumber

from . import discovery, fnb, fnbcc, homeloan, household, nedbank, nedbank_homeloan, tyme
from .master import load_pockets, rebuild_master

BANKS = {
    "FNB": {"mod": fnb, "inbox": "FNB", "archive": "FNB Statements", "stem": "fnb",
            "signature": "firstnationalbank", "exclude": ("moneymaximiser", "savingsaccount:", "homeloantransactionhistory", "fnbpremiercredit")},
    "FNB Credit Card": {"mod": fnbcc, "inbox": "FNB Credit Card", "archive": "FNB Credit Card Statements",
                        "stem": "fnbcc", "signature": "fnbpremiercredit", "chain_start": True},
    "FNB Money Maximiser": {"mod": fnb, "inbox": "FNB Money Maximiser",
                            "archive": "FNB Money Maximiser Statements", "stem": "fnbmm",
                            "signature": "moneymaximiser", "type": "savings"},
    "FNB Savings Pockets": {"mod": fnb, "inbox": "Savings and pockets",
                            "archive": "FNB Savings Pocket Statements", "stem": "fnbpk",
                            "signature": "savingsaccount:", "exclude": ("moneymaximiser",), "type": "savings"},
    "TymeBank Current": {"mod": tyme, "inbox": "TymeBank", "archive": "TymeBank Statements",
                         "stem": "tymecur", "signature": "currentaccountaccountnumber",
                         "exclude": ("goalsaveaccountaccountnumber",), "kind": "current", "multi": True},
    "TymeBank Savings": {"mod": tyme, "inbox": "TymeBank", "archive": "TymeBank Statements",
                         "stem": "tymesav", "signature": "goalsaveaccountaccountnumber", "type": "savings",
                         "kind": "savings", "multi": True},
    "TymeBank Current ({second})": {"mod": tyme, "inbox": "TymeBank", "archive": "TymeBank Statements ({second})",
                                 "stem": "tymeslcur", "signature": "taxinvoicenumber", "exclude": ("accountnumber:",),
                                 "kind": "current", "multi": True, "route": {"savings": "TymeBank Savings ({second})"}, "stale_days": 400},
    "TymeBank Savings ({second})": {"mod": tyme, "inbox": "TymeBank", "archive": "TymeBank Statements ({second})",
                                 "stem": "tymeslsav", "signature": "@reached through the route of the current entry@", "type": "savings",
                                 "kind": "savings", "multi": True, "stale_days": 400},
    "FNB Home Loan": {"mod": homeloan, "inbox": "FNB Home Loan", "archive": "FNB Home Loan Statements",
                      "stem": "fnbhl", "signature": "homeloantransactionhistory", "type": "loan", "autonumber": True},
    "Nedbank Home Loan": {"mod": nedbank_homeloan, "inbox": "Nedbank", "archive": "Nedbank Home Loan Statements",
                          "stem": "nedbankhl", "signature": "homeloanstatement", "type": "loan", "autonumber": True, "stale_days": 110},
    "Nedbank": {"mod": nedbank, "inbox": "Nedbank", "archive": "Nedbank Statements",
                "stem": "nedbank", "signature": "nedbankltdregno", "exclude": ("homeloanstatement",)},
    "Discovery": {"mod": discovery, "inbox": "Discovery", "archive": "Discovery Bank Statements",
                  "stem": "discovery", "signature": "discoverybanklimited"},
}
STALE_DAYS = 40
KEEP_BACKUPS = 6


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: str, header: list[str], rows: list[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="raise")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def header_of(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return next(csv.reader(f))


def open_statement(path: str):
    """Open a PDF. GoTyme statements are locked, so fall back to the TymeBank password list (environment variable)."""
    try:
        pdf = pdfplumber.open(path)
        pdf.pages[0].extract_text()
        return pdf
    except Exception:
        return tyme.open_pdf(path)


def detect_bank(path: str) -> str | None:
    with open_statement(path) as pdf:
        text = "".join((p.extract_text(x_tolerance=1.5) or "") for p in pdf.pages)
    flat = re.sub(r"\s+", "", text).lower()
    hits = [b for b, d in BANKS.items()
            if d["signature"] in flat and not any(x in flat for x in d.get("exclude", ()))]
    return hits[0] if len(hits) == 1 else None


def parse_all(bank: str, path: str) -> list[tuple[dict, list[str]]]:
    """Every statement in a file with its reconciliation problems. A GoTyme file holds one statement per account."""
    d = BANKS[bank]
    mod = d["mod"]
    if d.get("multi"):
        keep = {d["kind"], *d.get("route", {})}
        return [(s, mod.reconcile(s)) for s in mod.parse_file(path) if s["kind"] in keep]
    st = mod.parse_statement(path, 0) if bank == "Nedbank" else mod.parse_statement(path)
    return [(st, mod.reconcile(st))]


RAW_TX_HEADER = ["bank", "owner", "account_number", "statement_no", "source_file", "date", "description", "desc_source",
                 "amount", "balance", "accrued_charge", "page"]
RAW_ST_HEADER = ["file", "statement_no", "account_number", "product", "period_start", "period_end", "opening", "closing",
                 "rows", "reconciled", "problems"]


def ensure_raw(raw_dir: str) -> None:
    """Create empty raw files for a bank that has never been imported, so the master builder can read them."""
    for d in BANKS.values():
        for kind, header in (("transactions", RAW_TX_HEADER), ("statements", RAW_ST_HEADER)):
            path = os.path.join(raw_dir, f"{d['stem']}_{kind}.csv")
            if not os.path.exists(path):
                write_csv(path, header, [])


def check_chain(statements: list[dict]) -> list[str]:
    """Warnings about gaps, overlaps and balance breaks between consecutive statements of each account."""
    warnings = []
    by_acct: dict[tuple, list[dict]] = {}
    for s in statements:
        by_acct.setdefault((s["bank"], s["account_number"]), []).append(s)
    for (bank, acct), sts in by_acct.items():
        sts.sort(key=lambda s: (s["period_start"], s["period_end"]))
        for a, b in zip(sts, sts[1:]):
            gap = (date.fromisoformat(b["period_start"]) - date.fromisoformat(a["period_end"])).days
            if gap > 1:
                warnings.append(f"{bank}: gap of {gap - 1} days between statements ending {a['period_end']} and starting {b['period_start']}")
            elif str(a["closing"]) != str(b["opening"]) and float(a["closing"]) != float(b["opening"]):
                warnings.append(f"{bank}: closing balance {a['closing']} on {a['period_end']} does not equal the next opening balance {b['opening']}")
    return warnings


def stale_warnings(statements: list[dict], today: date) -> list[str]:
    out = []
    for bank in BANKS:
        ends = [s["period_end"] for s in statements if s["bank"] == bank]
        if ends and (today - date.fromisoformat(max(ends))).days > BANKS[bank].get("stale_days", STALE_DAYS):
            out.append(f"{bank}: the latest statement ends {max(ends)}. A newer statement should be available.")
    return out


LOCK_STALE_HOURS = 6
CONFLICT_WORDS = ("conflict",)
CONFLICT_SCAN = ("Data", "Config", "Inbox", "Dashboard")


def find_conflicted_copies(root: str) -> list[str]:
    """Files a sync client renamed after a clash between two computers (the name contains 'conflict')."""
    found = []
    for sub in CONFLICT_SCAN:
        for folder, _dirs, names in os.walk(os.path.join(root, sub)):
            found += [os.path.relpath(os.path.join(folder, n), root) for n in names
                      if any(w in n.lower() for w in CONFLICT_WORDS)]
    return sorted(found)


def acquire_lock(root: str) -> str:
    """Create Data/.import.lock exclusively so two computers never import at once. A lock older than LOCK_STALE_HOURS is stale."""
    path = os.path.join(root, "Data", ".import.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = (datetime.now().timestamp() - os.path.getmtime(path)) / 3600
            if age < LOCK_STALE_HOURS:
                with open(path, encoding="utf-8") as f:
                    holder = f.read().strip()
                raise RuntimeError(f"another import holds {path} ({holder}). Wait for it to finish. "
                                   f"If it died, delete the lock file after {LOCK_STALE_HOURS} hours or once you are sure nothing is running")
            os.remove(path)  # stale
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', 'unknown')} "
                    f"pid {os.getpid()} since {datetime.now():%Y-%m-%d %H:%M}")
        return path
    raise RuntimeError(f"could not take the import lock at {path}")


def run(root: str, dry_run: bool = False, rebuild_only: bool = False) -> dict:
    """Guarded entry point. A dry run only warns about conflicted copies. A real run or rebuild stops on them and holds the import lock."""
    conflicts = find_conflicted_copies(root)
    if dry_run:
        res = _run(root, dry_run=True, rebuild_only=rebuild_only)
        res["warnings"] = [f"conflicted copy from the sync client, resolve before a real import: {c}" for c in conflicts] + res["warnings"]
        return res
    if conflicts:
        raise RuntimeError("conflicted copies found, resolve them before importing: " + "; ".join(conflicts))
    lock = acquire_lock(root)
    try:
        return _run(root, dry_run=False, rebuild_only=rebuild_only)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _run(root: str, dry_run: bool = False, rebuild_only: bool = False) -> dict:
    tools = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw_dir, master_dir = os.path.join(root, "Data", "raw"), os.path.join(root, "Data", "master")
    master_path = os.path.join(master_dir, "transactions.csv")
    rules_path = os.path.join(root, "Config", "category_rules.csv")
    dash_path = os.path.join(root, "Dashboard", "household-dashboard.html")
    now = datetime.now()
    result = {"files": [], "new_tx": [], "warnings": [], "imported": 0, "failed": 0, "duplicates": 0, "dry_run": dry_run}

    household.apply(os.path.join(root, "Config"), BANKS)
    ensure_raw(raw_dir)
    raw_st = {b: read_csv(os.path.join(raw_dir, f"{d['stem']}_statements.csv")) for b, d in BANKS.items()}
    pocket_map = load_pockets(os.path.join(root, "Config", "pocket_map.csv"))
    known_accounts = {b: {s["account_number"] for s in rows} | set(BANKS[b].get("accounts", ())) for b, rows in raw_st.items()}

    # discover files: bank folders first, then loose PDFs in the Inbox root
    candidates = []
    inbox = os.path.join(root, "Inbox")
    shared = {d["inbox"] for d in BANKS.values() if sum(1 for x in BANKS.values() if x["inbox"] == d["inbox"]) > 1}
    done_folders = set()
    for bank, d in BANKS.items():
        folder = os.path.join(inbox, d["inbox"])
        if os.path.isdir(folder) and d["inbox"] not in done_folders:
            done_folders.add(d["inbox"])
            hint = None if d["inbox"] in shared else bank  # a folder shared by two account types is told apart by the PDF itself
            candidates += [(hint, os.path.join(folder, n)) for n in sorted(os.listdir(folder)) if n.lower().endswith(".pdf")]
    if os.path.isdir(inbox):
        candidates += [(None, os.path.join(inbox, n)) for n in sorted(os.listdir(inbox)) if n.lower().endswith(".pdf")]

    accepted: list[dict] = []
    seen = {b: {(s["account_number"], s["period_start"], s["period_end"]) for s in rows} for b, rows in raw_st.items()}
    if not rebuild_only:
        for hint, path in candidates:
            name = os.path.basename(path)
            rec = {"file": name, "bank": hint or "", "status": "", "message": "", "rows": 0, "period": ""}
            result["files"].append(rec)
            try:
                bank = detect_bank(path)
                if bank is None:
                    raise ValueError("could not tell which bank issued this statement")
                if hint and bank != hint:
                    raise ValueError(f"the file is in the {hint} folder but looks like a {bank} statement")
                rec["bank"] = bank
                parsed = parse_all(bank, path)
                if not parsed:
                    raise ValueError("no statements found in the file")
                if BANKS[bank].get("chain_start"):  # the statement prints only its end date: it starts the day after the previous one
                    for st, _ in parsed:
                        prev = [str(s["period_end"]) for s in raw_st[bank] + [a for a in accepted if a["bank"] == bank]
                                if s["account_number"] == st["account_number"] and str(s["period_end"]) < str(st["period_end"])]
                        st["period_start"] = (date.fromisoformat(max(prev)) + timedelta(days=1) if prev else
                                              min([t["date"] for t in st["transactions"]] + [st["period_end"]]))
                rec["period"] = f"{min(str(s['period_start']) for s, _ in parsed)} to {max(str(s['period_end']) for s, _ in parsed)}"
                rec["rows"] = sum(len(s["transactions"]) for s, _ in parsed)
                fresh = []
                for st, problems in parsed:
                    sbank = BANKS[bank].get("route", {}).get(st.get("kind"), bank)  # a monthly file carries several kinds of account
                    ident = (st["account_number"], str(st["period_start"]), str(st["period_end"]))
                    if bank == "FNB Savings Pockets" and st["account_number"][-4:] in pocket_map:
                        known_accounts[bank].add(st["account_number"])  # a pocket listed in Config\pocket_map.csv is a known account
                    if not BANKS[bank].get("multi") and st["account_number"] not in known_accounts[bank]:
                        raise ValueError(f"account {st['account_number']} is not a known {bank} account. For a savings pocket, add it to Config\\pocket_map.csv first. Otherwise the importer needs an entry for it")
                    if ident in seen[sbank]:
                        continue
                    overlap = [s for s in raw_st[sbank] + [a for a in accepted if a["bank"] == sbank]
                               if s["account_number"] == st["account_number"]
                               and str(st["period_start"]) < str(s["period_end"]) and str(st["period_end"]) > str(s["period_start"])]
                    if overlap:
                        raise ValueError(f"the period overlaps an imported statement ({overlap[0]['period_start']} to {overlap[0]['period_end']})")
                    if problems:
                        raise ValueError(f"account {st['account_number']} {st['period_start']} to {st['period_end']} does not reconcile: " + "; ".join(problems))
                    fresh.append((st, ident, sbank))
                if not fresh:
                    rec["status"], rec["message"] = "duplicate", "this statement period is already imported"
                    rec["path"] = path
                    result["duplicates"] += 1
                    continue
                for st, ident, sbank in fresh:
                    seen[sbank].add(ident)
                    accepted.append({"bank": sbank, "file_bank": bank, "path": path, "st": st, "rec": rec, "account_number": st["account_number"],
                                     "period_start": str(st["period_start"]), "period_end": str(st["period_end"]),
                                     "multi": bool(BANKS[sbank].get("multi")), "file_period": rec["period"]})
                rec["status"] = "ready" if dry_run else "imported"
            except Exception as exc:
                rec["status"], rec["message"] = "failed", str(exc)
                result["failed"] += 1

    # number the new statements, name the archive copies
    accepted.sort(key=lambda a: (a["bank"], a["period_start"]))
    next_no = {b: max([int(s["statement_no"]) for s in rows] + [0]) + 1 for b, rows in raw_st.items()}
    acct_next = {}
    for a in accepted:
        if a["bank"] == "Nedbank" or BANKS[a["bank"]].get("autonumber"):  # statements that carry no number of their own
            a["st"]["statement_no"] = next_no[a["bank"]]
            next_no[a["bank"]] += 1
        if a["multi"]:  # numbered per account, in period order
            key = (a["bank"], a["account_number"])
            if key not in acct_next:
                acct_next[key] = max([int(s["statement_no"]) for s in raw_st[a["bank"]] if s["account_number"] == a["account_number"]] + [0]) + 1
            a["st"]["statement_no"] = acct_next[key]
            acct_next[key] += 1
            a["archive_name"] = f"{a['file_bank']}_{a['file_period'].replace(' to ', '_to_')}_{sha(a['path'])[:8]}.pdf"
        else:
            a["archive_name"] = f"{a['file_bank']}_{a['account_number'][-4:]}_{a['period_start']}_to_{a['period_end']}.pdf"
    for rec in {id(a["rec"]): a["rec"] for a in accepted}.values():
        mine = [a for a in accepted if a["rec"] is rec]
        rec["message"] = (f"statement {mine[0]['st']['statement_no']}, archived as {mine[0]['archive_name']}" if not mine[0]["multi"]
                          else f"{len(mine)} new statements, archived as {mine[0]['archive_name']}")

    projected = [dict(s, bank=b) for b, rows in raw_st.items() for s in rows] + \
                [{"bank": a["bank"], "account_number": a["account_number"], "period_start": a["period_start"],
                  "period_end": a["period_end"], "opening": a["st"]["opening"], "closing": a["st"]["closing"]} for a in accepted]
    result["warnings"] += check_chain(projected) + stale_warnings(projected, now.date())
    result["imported"] = len(accepted)

    if dry_run:
        return result

    for rec in result["files"]:  # duplicates are set aside, never deleted
        if rec["status"] == "duplicate" and rec.get("path") and os.path.exists(rec["path"]):
            dup_dir = os.path.join(inbox, "_duplicates")
            os.makedirs(dup_dir, exist_ok=True)
            target = os.path.join(dup_dir, rec["file"])
            if os.path.exists(target):
                target = os.path.join(dup_dir, now.strftime("%Y%m%d_%H%M%S_") + rec["file"])
            shutil.move(rec["path"], target)
            rec["message"] += ". Moved to Inbox\\_duplicates"

    old_ids = {r["tx_id"] for r in read_csv(master_path)}
    if accepted:
        backup = os.path.join(root, "Data", "_backup", now.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(backup, exist_ok=True)
        for n in os.listdir(raw_dir):
            shutil.copy2(os.path.join(raw_dir, n), backup)
        if os.path.exists(master_path):
            shutil.copy2(master_path, backup)
        olds = sorted(os.listdir(os.path.join(root, "Data", "_backup")))
        for old in olds[:-KEEP_BACKUPS]:
            shutil.rmtree(os.path.join(root, "Data", "_backup", old), ignore_errors=True)

        for bank, d in BANKS.items():
            mine = [a for a in accepted if a["bank"] == bank]
            if not mine:
                continue
            tx_path = os.path.join(raw_dir, f"{d['stem']}_transactions.csv")
            st_path = os.path.join(raw_dir, f"{d['stem']}_statements.csv")
            tx_rows, st_rows = read_csv(tx_path), read_csv(st_path)
            for a in mine:
                s = a["st"]
                for t in s["transactions"]:
                    tx_rows.append({"bank": bank, "owner": s.get("owner") or d["owner"], "account_number": s["account_number"],
                                    "statement_no": s["statement_no"], "source_file": a["archive_name"],
                                    **{k: ("" if v is None else v) for k, v in t.items()}})
                st_rows.append({"file": a["archive_name"], "statement_no": s["statement_no"], "account_number": s["account_number"],
                                "product": s["product"], "period_start": s["period_start"], "period_end": s["period_end"],
                                "opening": s["opening"], "closing": s["closing"], "rows": len(s["transactions"]),
                                "reconciled": True, "problems": ""})
            write_csv(tx_path, header_of(tx_path), tx_rows)
            write_csv(st_path, header_of(st_path), st_rows)

        archived = set()
        for a in accepted:  # archive only after the data is safely written
            if a["path"] in archived:
                continue
            archived.add(a["path"])
            dest_dir = os.path.join(root, "_Archive", BANKS[a["file_bank"]]["archive"])
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, a["archive_name"])
            if os.path.exists(dest) and sha(dest) != sha(a["path"]):
                a["rec"]["message"] += ". WARNING: a different file with this name is already archived, so the original was left in the Inbox"
                continue
            if not os.path.exists(dest):
                shutil.copy2(a["path"], dest)
            if sha(dest) != sha(a["path"]):
                a["rec"]["message"] += ". WARNING: archive copy did not verify, so the original was left in the Inbox"
                continue
            os.chmod(dest, stat.S_IREAD)
            os.chmod(a["path"], stat.S_IWRITE | stat.S_IREAD)
            os.remove(a["path"])

    master, pairs = rebuild_master(raw_dir, master_path, rules_path)
    result["new_tx"] = [m for m in master if m["tx_id"] not in old_ids] if old_ids else []
    result["master_rows"] = len(master)
    result["master_categorised"] = sum(1 for m in master if m["category"])
    result["transfer_pairs"] = pairs

    os.makedirs(os.path.dirname(dash_path), exist_ok=True)
    if not master:
        result["dashboard"] = "not built: no transactions yet"
    else:
        proc = subprocess.run([sys.executable, os.path.join(tools, "build_dashboard.py"), master_path, raw_dir,
                               os.path.join(tools, "dashboard_template.html"), dash_path], capture_output=True, text=True)
        result["dashboard"] = "rebuilt" if proc.returncode == 0 else "FAILED: " + proc.stderr.strip()[-300:]

    write_logs(root, result, now)
    return result


def write_logs(root: str, result: dict, now: datetime) -> None:
    log_path = os.path.join(root, "Data", "import_log.csv")
    rows = read_csv(log_path)
    for r in result["files"]:
        rows.append({"run": now.strftime("%Y-%m-%d %H:%M"), "file": r["file"], "bank": r["bank"], "status": r["status"],
                     "period": r["period"], "rows": r["rows"], "message": r["message"]})
    if rows:
        write_csv(log_path, ["run", "file", "bank", "status", "period", "rows", "message"], rows)

    new = result["new_tx"]
    unc = sorted([t for t in new if not t["category"]], key=lambda t: -abs(float(t["amount"])))
    lines = [f"# Last import run: {now.strftime('%Y-%m-%d %H:%M')}", "",
             f"Statements imported: {result['imported']}. Duplicates skipped: {result['duplicates']}. Failed: {result['failed']}.", ""]
    if result["files"]:
        lines += ["| File | Bank | Period | Rows | Result |", "|---|---|---|---|---|"]
        lines += [f"| {r['file']} | {r['bank']} | {r['period']} | {r['rows']} | {r['status']}: {r['message']} |" for r in result["files"]]
        lines.append("")
    else:
        lines += ["No statements were waiting in the Inbox.", ""]
    lines += [f"Master table: {result.get('master_rows', 0)} transactions, {result.get('master_categorised', 0)} categorised. "
              f"Dashboard: {result.get('dashboard', 'not rebuilt')}.", ""]
    if new:
        lines += [f"New transactions: {len(new)}, of which {len(unc)} are not yet categorised.", ""]
    if unc:
        lines += ["Largest new items not yet categorised:", ""] + \
                 [f"- {t['date']} {t['bank']} {t['amount']} {t['description'][:70]}" for t in unc[:20]] + [""]
    if result["warnings"]:
        lines += ["Warnings:", ""] + [f"- {w}" for w in result["warnings"]] + [""]
    with open(os.path.join(root, "Data", "last_run.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
