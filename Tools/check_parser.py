r"""Contract test for a bank parser: does it meet what the importer expects?

usage: python check_parser.py MODULE FILE [--bank KEY]

MODULE is the parser module name inside finance_tools (for example discovery). FILE is a statement PDF.
Changes nothing. Exit code 0 when every check passes, 1 when any fails.
Checks: the module has the functions the importer calls, every statement carries the fields the raw
files need, every transaction carries the raw columns, amounts and dates have the right types, the
statement reconciles to the cent (opening plus transactions equals closing, and the running balance
agrees row by row when the statement prints one), and, with --bank, the BANKS entry is complete.
"""
import argparse
import importlib
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ST_FIELDS = ("statement_no", "account_number", "product", "period_start", "period_end", "opening", "closing", "transactions")
TX_FIELDS = ("date", "description", "desc_source", "amount", "balance", "accrued_charge", "page")
BANK_FIELDS = ("mod", "inbox", "archive", "stem", "signature")


def statements_of(mod, path):
    if hasattr(mod, "parse_file"):
        return list(mod.parse_file(path))
    return [mod.parse_statement(path)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("file")
    ap.add_argument("--bank")
    a = ap.parse_args()
    fails = []

    def check(ok: bool, label: str):
        print(("pass  " if ok else "FAIL  ") + label)
        if not ok:
            fails.append(label)

    mod = importlib.import_module(f"finance_tools.{a.module}")
    check(hasattr(mod, "reconcile"), "module has reconcile(statement)")
    check(hasattr(mod, "parse_statement") or hasattr(mod, "parse_file"), "module has parse_statement(path) or parse_file(path)")
    if fails:
        return 1
    sts = statements_of(mod, a.file)
    check(len(sts) > 0, f"parser returned {len(sts)} statement(s)")
    for i, st in enumerate(sts):
        tag = f"statement {i + 1}"
        missing = [k for k in ST_FIELDS if k not in st]
        check(not missing, f"{tag}: has the statement fields" + (f" (missing {missing})" if missing else ""))
        if missing:
            continue
        check(isinstance(st["period_start"], date) and isinstance(st["period_end"], date)
              and st["period_start"] <= st["period_end"], f"{tag}: period dates are dates and in order")
        check(isinstance(st["opening"], Decimal) and isinstance(st["closing"], Decimal), f"{tag}: opening and closing are Decimal")
        check(len(str(st["account_number"])) >= 4, f"{tag}: account number present")
        txs = st["transactions"]
        check(len(txs) > 0, f"{tag}: {len(txs)} transaction(s)")
        bad = [j for j, t in enumerate(txs) if any(k not in t for k in TX_FIELDS)]
        check(not bad, f"{tag}: every transaction has the raw columns" + (f" (rows {bad[:5]})" if bad else ""))
        if bad:
            continue
        check(all(isinstance(t["amount"], Decimal) and isinstance(t["date"], date) for t in txs), f"{tag}: amounts are Decimal and dates are dates")
        check(all(st["period_start"] <= t["date"] <= st["period_end"] for t in txs), f"{tag}: every transaction falls inside the period")
        check(all(str(t["description"]).strip() for t in txs), f"{tag}: no empty descriptions")
        problems = mod.reconcile(st)
        check(not problems, f"{tag}: reconciles to the cent" + (f" ({'; '.join(problems)})" if problems else ""))
        check(st["opening"] + sum(t["amount"] for t in txs) == st["closing"], f"{tag}: opening plus transactions equals closing (independent sum)")
        if all(t["balance"] is not None for t in txs):
            run, ok = st["opening"], True
            for t in txs:
                run += t["amount"]
                ok = ok and run == t["balance"]
            check(ok, f"{tag}: running balance agrees on every row")
    if a.bank:
        from finance_tools.importer import BANKS
        d = BANKS.get(a.bank)
        check(d is not None, f"BANKS has the key {a.bank!r}")
        if d:
            miss = [k for k in BANK_FIELDS if k not in d]
            check(not miss, "BANKS entry has mod, inbox, archive, stem, signature" + (f" (missing {miss})" if miss else ""))
            check(d.get("mod") is mod, "BANKS entry points at this module")
            others = [b for b, x in BANKS.items() if b != a.bank and x.get("signature") == d.get("signature")]
            check(not others, "signature is not shared with another bank")
    print("\nContract: " + ("all checks passed" if not fails else f"{len(fails)} check(s) failed"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
