"""FNB Premier Credit card statement parser.

A credit card is a normal account written from the holder's side: a purchase or fee is negative, a payment into the
card or interest on a credit balance is positive, and the balance is positive while the card is in credit (`Cr`) and
negative while money is owed. Opening plus transactions then equals closing, the same rule as every other account.

The statement prints no running balance and no year on a row. The year comes from the statement date. The importer
needs a balance on each row, so rows are put in date order (statement order breaks ties) and a running balance is
computed from the opening balance. Each card section ends with a "Card Total" line, which is checked against its rows.
Only the Straight facility is read: a row that shows an amount in the Budget facility column is refused for review.
The card number is reduced to its last four digits, and the statement number comes from the statement itself.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pdfplumber

MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
AMOUNT = r"(\d{1,3}(?: \d{3})*\.\d\d)(Cr)?"
ROW = re.compile(rf"^(\d{{2}}) ([A-Z][a-z]{{2}})(?: (.*?))? {AMOUNT}$")
BALANCE_ROW = re.compile(rf"^(Opening|Closing) Balance {AMOUNT}(?: \d[\d ]*\.\d\d)?$")


def money(text: str, credit: str | None) -> Decimal:
    value = Decimal(text.replace(" ", ""))
    return value if credit else -value


def parse_header(text: str) -> dict:
    flat = re.sub(r"[ \t]+", " ", text)
    m = re.search(r"Statement Date ?(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})", flat)
    end = date(int(m[3]), MON[m[2].lower()], int(m[1]))
    n = re.search(r"Statement No\.? ?: ?(\d+)", flat)
    c = re.search(r"FNB PREMIER CREDIT ?(\d{4}) (\d{4}) (\d{4}) (\d{4})", flat)
    if not (n and c):
        raise ValueError("no statement number or card number found")
    return {"account_number": c[4], "product": "Credit Card", "kind": "current", "statement_no": str(int(n[1])), "period_end": end,
            "period_start": end}  # the importer sets period_start to the day after the previous statement


def parse_statement(path: str) -> dict:
    with pdfplumber.open(path) as pdf:
        pages = [(p.extract_text(x_tolerance=1.5) or "") for p in pdf.pages]
    header = parse_header(pages[0] + "\n" + pages[1] if len(pages) > 1 else pages[0])
    end = header["period_end"]
    opening = closing = None
    txs: list[dict] = []
    section: list[dict] = []
    lines = [re.sub(r"[ \t]+", " ", x).strip() for x in "\n".join(pages).splitlines()]
    if "Transaction Details" not in lines:
        raise ValueError(f"{path}: no Transaction Details section found")
    for line in lines[lines.index("Transaction Details") + 1:]:  # the summary on page 1 repeats the balances
        b = BALANCE_ROW.match(line)
        if b and b[1] == "Opening" and opening is None:
            opening = money(b[2], b[3])
            continue
        if b and b[1] == "Closing" and opening is not None and closing is None:
            closing = money(b[2], b[3])
            break
        if opening is None:
            continue
        if line.startswith("Card No."):
            section = []
            continue
        t = re.fullmatch(rf"Card Total {AMOUNT}", line)
        if t:
            if sum(x["amount"] for x in section) != -Decimal(t[1].replace(" ", "")):
                raise ValueError(f"{path}: the rows under a card do not add up to its Card Total of {t[1]}")
            section = []
            continue
        r = ROW.match(line)
        if not r:
            continue  # page footers, "Foreign Amount" lines and the like
        month = MON[r[2].lower()]
        d = date(end.year - (1 if month > end.month else 0), month, int(r[1]))
        desc = re.sub(r"\s+", " ", (r[3] or "").lstrip("#")).strip() or "(no description on statement)"
        row = {"date": d, "description": desc, "desc_source": "text", "amount": money(r[4], r[5]),
               "accrued_charge": Decimal(0)}
        txs.append(row)
        section.append(row)
    if opening is None or closing is None:
        raise ValueError(f"{path}: no opening or closing balance row found")
    txs.sort(key=lambda t: t["date"])  # stable: statement order breaks ties
    run = opening
    for t in txs:
        run += t["amount"]
        t["balance"] = run
        t["page"] = 2
    header["opening"], header["closing"] = opening, closing
    header["transactions"] = txs
    return header


def reconcile(st: dict) -> list[str]:
    problems = []
    if st["opening"] + sum(t["amount"] for t in st["transactions"]) != st["closing"]:
        problems.append("opening plus transactions does not equal closing")
    return problems
