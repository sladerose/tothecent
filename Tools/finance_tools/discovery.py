"""Discovery Bank transaction account statement parser.

Each transaction has one anchor line (date, card, type, amount). Extra description lines
(merchant names, foreign currency amounts) sit just above or below it, so every non-anchor
line in the table is attached to the nearest anchor by vertical distance. The statement has
no running balance, so reconciliation checks opening plus transactions against closing only.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pdfplumber

X_TOL = 1.5
ROW_TOLERANCE = 2.5
MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
TYPE_X = (135, 215)  # type sits at x 145 when there is no card number, 155 otherwise; the card is at 97
DETAIL_X = (215, 505)
AMOUNT_X0 = 505
TABLE_BOTTOM = 736  # page footer starts below this line
MAX_LINE_DISTANCE = 12


def to_date(day: str, mon: str, year: str) -> date:
    return date(int(year), MON[mon[:3].lower()], int(day))


def to_money(text: str) -> Decimal:
    text = text.replace(" ", "")
    neg = text.startswith("-")
    value = Decimal(re.sub(r"[^\d.]", "", text))
    return -value if neg else value


def parse_header(text: str) -> dict:
    flat = re.sub(r"[ \t]+", " ", text)
    h: dict = {}
    h["statement_no"] = int(re.search(r"Statement number (\d+)", flat)[1])
    m = re.search(r"Statement period (\d+) (\w+) (\d{4}) - (\d+) (\w+) (\d{4})", flat)
    h["period_start"], h["period_end"] = to_date(m[1], m[2], m[3]), to_date(m[4], m[5], m[6])
    m = re.search(r"Discovery ([\w ]+?) Account (\d{8,})", flat)
    h["product"], h["account_number"] = m[1], m[2]
    h["opening"] = to_money(re.search(r"Opening balance on [\w ]+? R(-?[\d ,]+\.\d\d)", flat)[1])
    h["closing"] = to_money(re.search(r"Closing balance on [\w ]+? R(-?[\d ,]+\.\d\d)", flat)[1])
    return h


def group_rows(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= ROW_TOLERANCE:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def parse_statement(path: str) -> dict:
    with pdfplumber.open(path) as pdf:
        text = "\n".join((p.extract_text(x_tolerance=X_TOL) or "") for p in pdf.pages)
        header = parse_header(text)
        txs: list[dict] = []
        for page_no, page in enumerate(pdf.pages, 1):
            anchors: list[dict] = []
            extras: list[tuple[float, str]] = []
            in_table = False
            for row in group_rows(page.extract_words(x_tolerance=X_TOL)):
                top = row[0]["top"]
                joined = " ".join(w["text"] for w in row)
                if joined.startswith("Date Card no."):
                    in_table = True
                    continue
                if not in_table or top >= TABLE_BOTTOM:
                    continue
                if joined.startswith("Closing balance"):
                    break
                if joined.startswith("Opening balance"):
                    continue
                is_anchor = (len(row) > 3 and re.fullmatch(r"\d{1,2}", row[0]["text"])
                             and row[1]["text"][:3].lower() in MON and re.fullmatch(r"\d{4}", row[2]["text"]))
                if is_anchor:
                    day, mon, year = row[0]["text"], row[1]["text"], row[2]["text"]
                    card = next((w["text"] for w in row if w["text"].startswith("***")), "")
                    typ = " ".join(w["text"] for w in row if TYPE_X[0] <= w["x0"] < TYPE_X[1])
                    detail = " ".join(w["text"] for w in row if DETAIL_X[0] <= w["x0"] < DETAIL_X[1])
                    amt_text = "".join(w["text"] for w in row if w["x0"] >= AMOUNT_X0)
                    if not re.fullmatch(r"-?R-?[\d,]*\d\.\d\d", amt_text.replace(" ", "")):
                        raise ValueError(f"{path} p{page_no}: bad amount {amt_text!r} in {joined!r}")
                    anchors.append({"date": to_date(day, mon, year), "card": card, "type": typ,
                                    "detail": detail, "amount": to_money(amt_text), "top": top,
                                    "above": [], "below": [], "page": page_no})
                else:
                    extras.append((top, " ".join(w["text"] for w in row)))
            for top, line in extras:
                if not anchors:
                    raise ValueError(f"{path} p{page_no}: description line before any transaction: {line!r}")
                near = min(anchors, key=lambda a: abs(a["top"] - top))
                if abs(near["top"] - top) > MAX_LINE_DISTANCE:
                    raise ValueError(f"{path} p{page_no}: stray line {line!r}")
                (near["above"] if top < near["top"] else near["below"]).append(line)
            txs.extend(anchors)
    out = []
    for a in txs:
        parts = [a["type"]] + a["above"] + [a["detail"]] + a["below"]
        desc = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
        out.append({"date": a["date"], "description": desc, "desc_source": "text", "amount": a["amount"],
                    "balance": None, "accrued_charge": Decimal(0), "card": a["card"], "type": a["type"],
                    "page": a["page"]})
    header["transactions"] = out
    return header


def reconcile(st: dict) -> list[str]:
    problems = []
    if st["opening"] + sum(t["amount"] for t in st["transactions"]) != st["closing"]:
        problems.append("opening plus transactions does not equal closing")
    return problems
