"""Nedbank current account statement parser.

Columns are identified by the right edge of each number: debits (and fees, marked '*')
sit under the Debits column, credits under Credits, and the running balance at the far right.
Dates are printed in full (dd/mm/yyyy), so no year inference is needed.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

import pdfplumber

X_TOL = 1.5
ROW_TOLERANCE = 2.5
DATE_RE = re.compile(r"^\d\d/\d\d/\d{4}$")
NUM_RE = re.compile(r"^-?\d[\d,]*\.\d\d-?$")
DESC_X_MIN = 138
FEE_X1_MAX = 360
DEBIT_X1 = (360, 430)
CREDIT_X1 = (430, 500)
BALANCE_X1_MIN = 500


def dec(text: str) -> Decimal:
    neg = text.startswith("-") or text.endswith("-")
    value = Decimal(text.strip("-").replace(",", ""))
    return -value if neg else value


def rand(text: str) -> Decimal:
    neg = "-" in text
    return (-1 if neg else 1) * Decimal(re.sub(r"[^\d.]", "", text))


def parse_date(text: str) -> date:
    return datetime.strptime(text, "%d/%m/%Y").date()


def parse_header(text: str, file_no: int) -> dict:
    flat = re.sub(r"\s+", "", text)
    h: dict = {"statement_no": file_no, "product": "Current"}
    m = re.search(r"Statementperiod:(\d\d/\d\d/\d{4})\(cid:\d+\)(\d\d/\d\d/\d{4})", flat)
    h["period_start"], h["period_end"] = parse_date(m[1]), parse_date(m[2])
    h["account_number"] = re.search(r"Currentaccount(\d{8,})", flat)[1]
    h["opening"] = rand(re.search(r"Openingbalance(R-?[\d,]+\.\d\d-?)", flat)[1])
    h["closing"] = rand(re.search(r"Closingbalance(R-?[\d,]+\.\d\d-?)", flat)[1])
    h["credit_total"] = rand(re.search(r"Fundsreceived/Credits(R[\d,]+\.\d\d)", flat)[1])
    h["debit_total"] = rand(re.search(r"Fundsused/Debits(R[\d,]+\.\d\d)", flat)[1])
    return h


def group_rows(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= ROW_TOLERANCE:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def parse_statement(path: str, file_no: int) -> dict:
    with pdfplumber.open(path) as pdf:
        text = "\n".join((p.extract_text(x_tolerance=X_TOL) or "") for p in pdf.pages)
        header = parse_header(text, file_no)
        txs: list[dict] = []
        in_table = False
        for page_no, page in enumerate(pdf.pages, 1):
            for row in group_rows(page.extract_words(x_tolerance=X_TOL)):
                joined = " ".join(w["text"] for w in row)
                if "Tranlist" in joined.replace(" ", "") and "Balance" in joined:
                    in_table = True
                    continue
                if not in_table:
                    continue
                if joined.replace(" ", "").startswith("Closingbalance"):
                    in_table = False
                    continue
                dated = [w for w in row if DATE_RE.match(w["text"]) and 90 <= w["x0"] < 100]
                if not dated:
                    # wrapped description line: text only, in the description column
                    if (txs and txs[-1]["page"] == page_no and all(w["x0"] >= DESC_X_MIN for w in row)
                            and not any(NUM_RE.match(w["text"]) for w in row)):
                        txs[-1]["description"] += " " + joined
                    continue
                tdate = parse_date(dated[0]["text"])
                tranlist = next((w["text"] for w in row if w["x0"] < 90 and re.fullmatch(r"\d{4,8}", w["text"])), "")
                desc_words, amount, balance, fee_flag = [], None, None, False
                for w in row:
                    if w["x0"] < DESC_X_MIN:
                        continue
                    if w["text"] == "*":
                        fee_flag = True
                    elif NUM_RE.match(w["text"]) and w["x0"] > 280:
                        if w["x1"] >= BALANCE_X1_MIN:
                            balance = dec(w["text"])
                        elif w["x1"] < FEE_X1_MAX or DEBIT_X1[0] <= w["x1"] < DEBIT_X1[1]:
                            amount = -dec(w["text"])
                        elif CREDIT_X1[0] <= w["x1"] < CREDIT_X1[1]:
                            amount = dec(w["text"])
                    else:
                        desc_words.append(w["text"])
                desc = " ".join(desc_words)
                if desc.lower().replace(" ", "") == "openingbalance":
                    continue
                if amount is None or balance is None:
                    raise ValueError(f"{path} p{page_no}: unparsed row {[w['text'] for w in row]}")
                txs.append({"date": tdate, "description": desc, "desc_source": "text", "amount": amount,
                            "balance": balance, "accrued_charge": Decimal(0), "fee_flag": fee_flag,
                            "tranlist_no": tranlist, "page": page_no})
    for t in txs:
        t["description"] = re.sub(r"\s+", " ", t["description"]).strip()
    header["transactions"] = txs
    return header


def reconcile(st: dict) -> list[str]:
    problems = []
    txs = st["transactions"]
    if st["opening"] + sum(t["amount"] for t in txs) != st["closing"]:
        problems.append("opening plus transactions does not equal closing")
    running = st["opening"]
    for i, t in enumerate(txs, 1):
        running += t["amount"]
        if running != t["balance"]:
            problems.append(f"running balance breaks at row {i} ({t['date']} {t['description'][:30]})")
            break
    credits = sum(t["amount"] for t in txs if t["amount"] > 0)
    debits = -sum(t["amount"] for t in txs if t["amount"] < 0)
    if credits != st["credit_total"]:
        problems.append(f"credits {credits} vs statement {st['credit_total']}")
    if debits != st["debit_total"]:
        problems.append(f"debits {debits} vs statement {st['debit_total']}")
    return problems
