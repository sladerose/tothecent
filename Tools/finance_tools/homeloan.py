"""FNB Home Loan statement parser.

A home loan is a liability account. Amounts and balances are written from the borrower's side: a charge that
increases the debt (interest, fee, payout, initiation fee) is negative, a payment that reduces it is positive,
and the balance is negative while money is owed. Opening plus transactions then equals closing, the same rule
as every other account. Columns are read by position because amounts use spaces as thousand separators.
Rows without an amount (Rate Change, Schedule Loan, address maintenance) are not transactions and are skipped.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pdfplumber

X_TOL = 1.5
ROW_TOLERANCE = 2.5
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DESC_X = (85, 300)    # the description starts at about x 94 or 101 depending on the statement
DEBIT_X = (300, 380)    # right edge of the Debits column is about 370
CREDIT_X = (380, 470)   # right edge of the Credits column is about 455
BALANCE_X = 470         # right edge of the Balance column is about 556
MONTH_NAMES = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"], 1)}


def money(text: str) -> Decimal:
    return Decimal(re.sub(r"[^\d.]", "", text))


def signed_balance(text: str) -> Decimal:
    """`504 785.48Dr` is a debt of R504,785.48 and is stored as -504785.48. A Cr balance would be positive."""
    value = money(text)
    return -value if text.rstrip().endswith("Dr") else value


def group_rows(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= ROW_TOLERANCE:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def long_date(text: str) -> date:
    m = re.fullmatch(r"(\d{1,2}) (\w+) (\d{4})", text.strip())
    return date(int(m[3]), MONTH_NAMES[m[2].lower()], int(m[1]))


def parse_header(text: str) -> dict:
    flat = re.sub(r"[ \t]+", " ", text)
    h: dict = {}
    h["account_number"] = re.sub(r"\D", "", re.search(r"Account Number : ([\d-]+)", flat)[1])
    m = re.search(r"Home Loan Transaction History from ?(\d{1,2} \w+ \d{4}) to (\d{1,2} \w+ \d{4})", flat)
    h["period_start"], h["period_end"] = long_date(m[1]), long_date(m[2])
    h["product"] = "Home Loan"
    h["statement_no"] = ""  # the statement has no number: the importer numbers it
    m = re.search(r"Registered Bond Amount R ?([\d ]+\.\d\d)Dr", flat)
    h["bond_amount"] = money(m[1]) if m else None
    m = re.search(r"Current Interest Rate (\d+\.\d+)%", flat)
    h["interest_rate"] = Decimal(m[1]) if m else None
    m = re.search(r"Total Monthly Repayment R ?([\d ]+\.\d\d)Dr", flat)
    h["monthly_repayment"] = money(m[1]) if m else None
    return h


def parse_statement(path: str) -> dict:
    with pdfplumber.open(path) as pdf:
        text = "\n".join((p.extract_text(x_tolerance=X_TOL) or "") for p in pdf.pages)
        header = parse_header(text)
        opening = closing = None
        txs: list[dict] = []
        for page_no, page in enumerate(pdf.pages, 1):
            for row in group_rows(page.extract_words(x_tolerance=X_TOL)):
                if len(row) < 4 or not (re.fullmatch(r"\d{1,2}", row[0]["text"]) and row[1]["text"][:3].lower() in MON
                                        and re.fullmatch(r"\d{4}", row[2]["text"])):
                    continue
                d = date(int(row[2]["text"]), MON[row[1]["text"][:3].lower()], int(row[0]["text"]))
                desc = " ".join(w["text"] for w in row if DESC_X[0] <= w["x0"] < DEBIT_X[0] and w["x1"] <= DEBIT_X[0] + 5
                                and not re.fullmatch(r"[\d.,]+(Dr|Cr)?", w["text"]))
                bal_text = "".join(w["text"] for w in row if w["x0"] >= BALANCE_X)
                debit_text = "".join(w["text"] for w in row if DEBIT_X[0] <= w["x0"] < DEBIT_X[1] and w["x1"] <= DEBIT_X[1])
                credit_text = "".join(w["text"] for w in row if CREDIT_X[0] <= w["x0"] < CREDIT_X[1] and w["x1"] <= CREDIT_X[1] + 5)
                if not re.fullmatch(r"\d[\d.]*\.\d\d(Dr|Cr)", bal_text):
                    raise ValueError(f"{path} p{page_no}: unreadable balance {bal_text!r} on {desc!r}")
                balance = signed_balance(bal_text)
                if desc == "Opening Balance":
                    opening = balance
                    continue
                if desc == "Closing Balance":
                    closing = balance
                    continue
                if debit_text and credit_text:
                    raise ValueError(f"{path} p{page_no}: both a debit and a credit on {desc!r}")
                if not debit_text and not credit_text:
                    continue  # Rate Change, Schedule Loan, Domicilium Address Maintenance: no amount
                amount = -money(debit_text) if debit_text else money(credit_text)
                txs.append({"date": d, "description": desc, "desc_source": "text", "amount": amount, "balance": balance,
                            "accrued_charge": Decimal(0), "page": page_no})
        if opening is None or closing is None:
            raise ValueError(f"{path}: no opening or closing balance row found")
    header["opening"], header["closing"] = opening, closing
    header["transactions"] = txs
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
    return problems
