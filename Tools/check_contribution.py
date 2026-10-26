r"""Contribution check: may these files leave this machine?

usage: python check_contribution.py [--root PATH] FILE [FILE ...]

Run it on exactly the files a contribution would send. Changes nothing, prints counts and file names,
never the values it found. Exit code 0 only when every file passes, 1 when any is refused.
A file is refused when it sits in a private place (Config, Data, Inbox, _Archive, Dashboard, Knowledge,
CLAUDE.local.md), is a statement or data file (PDF, spreadsheet, database, CSV outside a tests folder),
is not readable text, or contains one of this household's own values: owner names, account numbers,
meter numbers, rule match text, or the words in Config\household.json.
Fixtures sent with a contribution must be synthetic. This check cannot prove that they are. The person
who contributes must read the diff before it is sent.
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

PRIVATE_DIRS = {"config", "data", "inbox", "_archive", "dashboard", "knowledge"}
DATA_EXT = {".pdf", ".xlsx", ".xls", ".db", ".sqlite", ".zip", ".png", ".jpg", ".jpeg"}
GENERIC = {"home", "owner", "partner", "rental apartment", "the home", "property a", "property b"}


def read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def needles(root: str) -> dict:
    n = {}
    cfg_path = os.path.join(root, "Config", "household.json")
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    names = set(cfg.get("owners", {}).values()) | {cfg.get("second_tyme_holder", "")} | set(cfg.get("loan_properties", {}).values())
    names |= set(cfg.get("holder_keywords", {}))
    for v in names:
        if v and v.lower() not in GENERIC:
            n["name" + str(len(n))] = v
    for v in cfg.get("accounts", {}).values():
        for x in (v if isinstance(v, list) else [v]):
            if len(str(x)) >= 4:
                n["acct" + str(len(n))] = str(x)
    for p in glob.glob(os.path.join(root, "Data", "raw", "*_statements.csv")):
        for r in read_csv(p):
            a = (r.get("account_number") or "").strip()
            if len(a) >= 6:
                n["acct" + str(len(n))] = a
    for p in glob.glob(os.path.join(root, "Config", "*.csv")):
        for r in read_csv(p):
            for col in ("identifier_last4", "meter_number", "match_text"):
                v = (r.get(col) or "").strip()
                if len(v) >= 4:
                    n["cfg" + str(len(n))] = v
    return n


def refuse_path(path: str):
    parts = [p.lower() for p in re.split(r"[\\/]", os.path.normpath(path))]
    if any(p in PRIVATE_DIRS for p in parts[:-1]) or parts[-1] == "claude.local.md":
        return "sits in a private place"
    ext = os.path.splitext(path)[1].lower()
    if ext in DATA_EXT:
        return "is a statement or data file type"
    if ext == ".csv" and "tests" not in parts:
        return "is a CSV outside a tests folder"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    need = needles(root)
    refused = {}
    for f in a.files:
        inside = os.path.normcase(os.path.abspath(f)).startswith(os.path.normcase(root))
        why = refuse_path(os.path.relpath(os.path.abspath(f), root) if inside else f)
        if not why:
            try:
                with open(f, "rb") as fh:
                    raw = fh.read()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                why = "cannot be read as text"
            else:
                hits = [k for k, v in need.items()
                        if re.search(r"\b" + re.escape(v) + r"\b", text, re.I if k.startswith("name") else 0)]
                if hits:
                    why = f"contains {len(hits)} of this household's own values"
        if why:
            refused[f] = why
    print(f"{len(need)} household values checked across {len(a.files)} file(s)")
    for f, why in refused.items():
        print(f"REFUSED  {f}: {why}")
    print("clean: every file may be sent" if not refused else f"{len(refused)} file(s) refused: nothing may be sent")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
