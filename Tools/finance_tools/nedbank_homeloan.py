"""Nedbank Home Loan statement parser.

A home loan is a liability account, written from the borrower's side exactly as in `homeloan.py`: a charge that
increases the debt (interest, fee, initiation fee, disbursement) is negative, a payment is positive, and the balance
is negative while money is owed. Opening plus transactions then equals closing.

Layout facts (see Knowledge\\statement-formats.md):
- The statement is locked with the account holder's ID number. The password comes from the environment variable
  TYMEBANK_PDF_PASSWORDS through `tyme.open_pdf`, and is never stored in a file.
- Rows have a post date, an effective date, a description, then amounts that use spaces as thousand separators. Debit
  and credit amounts are told apart by the right edge of the amount, the balance is the right most amount.
- The first row is `Balance Brought Forward`, which is the opening balance.
- Interest is posted on the first day of the next month. Each statement prints the interest row that falls after the
  statement date, and the next statement prints the same row again as its first row. Rows dated after the statement
  date are therefore left out of the period, so that no transaction is counted twice. The period ends on the day
  before the first such row.
- The header balance outstanding includes that trailing interest row, so it is checked against the last printed row.
  A monthly statement can omit rows that its header balance already includes: it then fails the check and is not
  imported (the quarterly statement for the same period carries the full table).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from . import tyme
from .homeloan import group_rows

X_TOL = 1.5
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
DESC_X = (180, 300)      # the description starts at about x 184 or 187
DEBIT_MAX_X1 = 390       # right edge of the Debit column is about 380
CREDIT_MAX_X1 = 465      # right edge of the Credit column is about 453
BALANCE_MIN_X1 = 465     # right edge of the Balance column is about 528


def money(text: str) -> Decimal:
    return Decimal(re.sub(r"[^\d.]", "", text))


def to_date(text: str) -> date:
    d, m, y = text.split("/")
    return date(int(y), int(m), int(d))


def parse_header(text: str) -> dict:
    flat = re.sub(r"\s+", " ", text)
    h: dict = {}
    m = re.search(r"Account number: ?(\d+)", flat)
    if not m:
        raise ValueError("no account number found in the header")
    h["account_number"] = m[1]
    m = re.search(r"Statement date: ?(\d{2}/\d{2}/\d{4})", flat)
    if not m:
        raise ValueError("no statement date found in the header")
    h["statement_date"] = to_date(m[1])
    m = re.search(r"Loan amount Balance outstanding Instalment R ?([\d ]+\.\d\d) R ?([\d ]+\.\d\d) R ?([\d ]+\.\d\d)", flat)
    if not m:
        raise ValueError("no loan amount, balance outstanding and instalment line found in the header")
    h["loan_amount"], h["header_balance"], h["monthly_repayment"] = money(m[1]), money(m[2]), money(m[3])
    m = re.search(r"Interest rate \(pa\): ?(\d+\.\d+)%", flat)
    h["interest_rate"] = Decimal(m[1]) if m else None
    m = re.search(r"Registered amount: ?R ?([\d ]+\.\d\d)", flat)
    h["bond_amount"] = money(m[1]) if m else None
    m = re.search(r"Registration date: ?(\d{2}/\d{2}/\d{4})", flat)
    h["registration_date"] = to_date(m[1]) if m else None
    m = re.search(r"Loan term \(months\): ?(\d+)", flat)
    h["term_months"] = int(m[1]) if m else None
    h["kind"] = "Quarterly" if "Quarterly home loan statement" in flat else "Monthly" if "Home loan statement" in flat else ""
    h["product"] = "Home Loan"
    h["statement_no"] = ""  # the statement has no number of its own: the importer numbers it
    return h


def parse_statement(path: str) -> dict:
    with tyme.open_pdf(path) as pdf:
        text = "\n".join((p.extract_text(x_tolerance=X_TOL) or "") for p in pdf.pages)
        header = parse_header(text)
        opening = opening_date = None
        included: list[dict] = []
        trailing: list[dict] = []
        for page_no, page in enumerate(pdf.pages, 1):
            for row in group_rows(page.extract_words(x_tolerance=X_TOL)):
                if len(row) < 4 or not (DATE_RE.fullmatch(row[0]["text"]) and DATE_RE.fullmatch(row[1]["text"])):
                    continue
                d = to_date(row[0]["text"])
                desc = " ".join(w["text"] for w in row[2:] if DESC_X[0] <= w["x0"] and w["x1"] <= DESC_X[1])
                amount_words = [w for w in row[2:] if w["x0"] >= DESC_X[1]]
                bal_text = "".join(w["text"] for w in amount_words if w["x1"] > BALANCE_MIN_X1)
                debit_text = "".join(w["text"] for w in amount_words if w["x1"] <= DEBIT_MAX_X1)
                credit_text = "".join(w["text"] for w in amount_words if DEBIT_MAX_X1 < w["x1"] <= CREDIT_MAX_X1)
                if not re.fullmatch(r"\d+\.\d\d", bal_text):
                    raise ValueError(f"{path} p{page_no}: unreadable balance {bal_text!r} on {desc!r}")
                balance = -money(bal_text) if money(bal_text) else Decimal("0.00")
                if desc == "Balance Brought Forward":
                    opening, opening_date = balance, d
                    continue
                if debit_text and credit_text:
                    raise ValueError(f"{path} p{page_no}: both a debit and a credit on {desc!r}")
                if not debit_text and not credit_text:
                    raise ValueError(f"{path} p{page_no}: no amount on {desc!r}")
                amount = -money(debit_text) if debit_text else money(credit_text)
                tx = {"date": d, "description": desc, "desc_source": "text", "amount": amount, "balance": balance,
                      "accrued_charge": Decimal(0), "page": page_no}
                (trailing if d > header["statement_date"] else included).append(tx)
    if opening is None:
        raise ValueError(f"{path}: no Balance Brought Forward row found")
    header["opening"] = opening
    header["closing"] = included[-1]["balance"] if included else opening
    header["period_start"] = opening_date
    header["period_end"] = (trailing[0]["date"] - timedelta(days=1)) if trailing else header["statement_date"]
    header["last_printed_balance"] = (trailing or included or [{"balance": opening}])[-1]["balance"]
    header["trailing_rows"] = len(trailing)
    header["transactions"] = included
    return header


def reconcile(st: dict) -> list[str]:
    problems = []
    run = st["opening"]
    for t in st["transactions"]:
        run += t["amount"]
        if run != t["balance"]:
            problems.append(f"running balance breaks at {t['date']} {t['description']}: expected {run}, statement shows {t['balance']}")
            break
    if st["opening"] + sum(t["amount"] for t in st["transactions"]) != st["closing"]:
        problems.append("opening plus transactions does not equal closing")
    if -st["header_balance"] != st["last_printed_balance"]:
        problems.append(f"the balance outstanding in the header ({st['header_balance']}) does not equal the last balance printed in the "
                        f"table ({-st['last_printed_balance']}): the table is missing rows that the header balance includes")
    return problems