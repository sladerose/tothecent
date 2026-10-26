"""GoTyme (TymeBank) statement parser: the current account and the GoalSave savings pockets.

Both statement types share a layout. A current account file holds one account over a period.
A savings file holds one GoalSave pocket after another, each starting on a page that carries
`Account Number:` and continuing over further pages until the next pocket starts.

Every row sits between two horizontal rules, so a description that wraps above or below the date
line is still read as one row. A diagonal watermark is printed over the table and is dropped
before reading. Statements are locked with the account holder's ID number. The password is read
from the environment variable TYMEBANK_PDF_PASSWORDS (comma separated candidates) and is never
stored in any file.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import Decimal

import pdfplumber

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
# First names printed in the statement header, mapped to the household owner. The name itself is never stored.
OWNER_KEYWORDS: dict[str, str] = {}  # holder name in the statement header to owner, filled by household.apply
NUM_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?$")
DATE_RE = re.compile(r"^\d\d$")


def passwords() -> list[str]:
    """Candidates from the environment, plus the date of birth (yyyymmdd) each 13 digit ID number carries, which FNB uses
    to lock emailed statements. The dates are worked out in memory only, never printed or stored."""
    found = [p.strip() for p in os.environ.get("TYMEBANK_PDF_PASSWORDS", "").split(",") if p.strip()]
    dobs = [century + p[:6] for p in found if re.fullmatch(r"\d{13}", p) for century in ("19", "20")]
    return found + dobs


def open_pdf(path: str) -> pdfplumber.PDF:
    """Open a statement, trying no password first, then each candidate from the environment."""
    last = None
    for pw in [None] + passwords():
        try:
            pdf = pdfplumber.open(path, password=pw) if pw else pdfplumber.open(path)
            pdf.pages[0].extract_text()  # forces decryption, raises if the password is wrong
            return pdf
        except Exception as exc:  # wrong or missing password
            last = exc
    raise ValueError("the statement is locked and no password in TYMEBANK_PDF_PASSWORDS opens it") from last


def money(text: str) -> Decimal:
    t = text.strip().lstrip("R").replace(",", "")
    return Decimal("0") if t in ("", "-") else Decimal(t)


def parse_date(day: str, mon: str, year: str):
    return datetime(int(year), MONTHS[mon], int(day)).date()


def clean(page):
    """Drop the diagonal watermark (characters drawn off axis)."""
    return page.filter(lambda o: o.get("object_type") != "char"
                       or (abs(o["matrix"][1]) <= 0.01 and abs(o["matrix"][2]) <= 0.01))


def rules_of(page) -> list[float]:
    """Row separators. Each rule is drawn as several cell wide segments, so add up the segments at each height."""
    span: dict[float, float] = {}
    for l in page.lines:
        if abs(l["top"] - l["bottom"]) < 1:
            k = round(l["top"], 1)
            span[k] = span.get(k, 0) + (l["x1"] - l["x0"])
    return sorted(k for k, w in span.items() if w > 0.6 * page.width)


def parse_rows(page, columns: list[str]) -> list[dict]:
    """Read the transaction rows of one page. `columns` names the numeric columns left to right."""
    words = clean(page).extract_words(x_tolerance=1.5, y_tolerance=1)
    head = [w for w in words if w["text"] == "Date" and any(
        v["text"] == "Details" and abs(v["top"] - w["top"]) < 3 for v in words)]
    if not head:
        return []
    hy = head[0]["top"]
    hrow = [w for w in words if abs(w["top"] - hy) < 3]
    # right edge of each numeric column: the last header word of that column
    edges = {}
    edge_words = sorted([w for w in hrow if w["text"] in ("Fees*(-)", "(-)", "(+)", "Balance")], key=lambda w: w["x1"])
    if len(edge_words) != len(columns):
        raise ValueError(f"unexpected header columns {[w['text'] for w in hrow]}")
    for name, w in zip(columns, edge_words):
        edges[name] = w["x1"]
    first_numeric_x0 = min(w["x0"] for w in hrow if w["text"] in ("Fees*(-)", "Credits", "Debits", "Running"))
    detail_x0 = min(w["x0"] for w in hrow if w["text"] == "Details")
    seps = [s for s in rules_of(page) if s > hy]
    anchors = [w for w in words if w["top"] > hy and w["x0"] < 40 and DATE_RE.match(w["text"])]
    rows = []
    for a in anchors:
        line = [w for w in words if abs(w["top"] - a["top"]) < 3 and w["x0"] < 70]
        line.sort(key=lambda w: w["x0"])
        if len(line) < 3 or line[1]["text"] not in MONTHS or not re.fullmatch(r"\d{4}", line[2]["text"]):
            continue
        above = [s for s in seps if s <= a["top"] + 1]
        below = [s for s in seps if s > a["top"] + 1]
        if not above or not below:
            raise ValueError(f"no row rules around the row at {a['top']:.0f}")
        lo, hi = above[-1], below[0]
        band = [w for w in words if lo - 0.5 <= w["top"] < hi]
        nums = {}
        desc = []
        for w in sorted(band, key=lambda w: (round(w["top"]), w["x0"])):
            if w["x0"] < 70 and abs(w["top"] - a["top"]) < 3 and (w in line):
                continue  # the date itself
            if w["x1"] > first_numeric_x0 - 4 and (NUM_RE.match(w["text"]) or w["text"] == "-"):
                col = min(edges, key=lambda c: abs(edges[c] - w["x1"]))
                if abs(edges[col] - w["x1"]) > 14:
                    raise ValueError(f"number {w['text']} is not under a column")
                nums[col] = w["text"]
            elif w["x0"] >= detail_x0 - 2 and w["x1"] <= first_numeric_x0 - 2:
                desc.append(w["text"])
        if set(nums) != set(columns):
            raise ValueError(f"row on {line[0]['text']} {line[1]['text']} {line[2]['text']} has columns {sorted(nums)}")
        rows.append({"date": parse_date(line[0]["text"], line[1]["text"], line[2]["text"]),
                     "description": " ".join(desc), "nums": nums})
    return rows


def is_monthly(path: str) -> bool:
    """The emailed monthly personal statement (one section per account, no `Account Number:` header)."""
    with open_pdf(path) as pdf:
        text = clean(pdf.pages[0]).extract_text(x_tolerance=1.5) or ""
    return "Tax invoice number" in text and "Account Num." in text and "Account Number:" not in text


def parse_file(path: str) -> list[dict]:
    """Return one statement per account in the file."""
    if is_monthly(path):
        from . import tyme_monthly
        return tyme_monthly.parse_file(path)
    out: list[dict] = []
    cur: dict | None = None
    with open_pdf(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            text = clean(page).extract_text(x_tolerance=1.5) or ""
            acct = re.search(r"Account Number:\s*(\d+)", text)
            if acct:
                cur = start_statement(text, acct.group(1))
                out.append(cur)
            if cur is None:
                raise ValueError(f"page {pno} has rows before any account header")
            columns = ["fees", "credit", "debit", "balance"] if cur["kind"] == "current" else ["credit", "debit", "balance"]
            for r in parse_rows(page, columns):
                n = r["nums"]
                credit, debit = money(n["credit"]), money(n["debit"])
                fee = money(n["fees"]) if "fees" in n else Decimal(0)
                cur["transactions"].append({
                    "date": r["date"], "description": re.sub(r"\s+", " ", r["description"]).strip(),
                    "desc_source": "text", "amount": credit - debit - fee, "balance": money(n["balance"]),
                    "accrued_charge": fee, "page": pno})
    return out


def start_statement(text: str, account: str) -> dict:
    head = text.split("Account Number")[0]
    kind = "savings" if "GoalSave" in head else "current"
    per = re.search(r"Period:\s*(\d\d) (\w{3}) (\d{4}) - (\d\d) (\w{3}) (\d{4})", text)
    owner = next((o for k, o in OWNER_KEYWORDS.items() if k in head.upper()), None)
    if owner is None:
        raise ValueError("cannot tell whose statement this is from the header")

    def total(label):
        m = re.search(label + r"\*?\s+R([\d,]+(?:\.\d+)?)", text)
        return money(m.group(1)) if m else Decimal(0)
    return {
        "kind": kind, "owner": owner, "account_number": account,
        "product": "GoalSave" if kind == "savings" else "Current account",
        "period_start": parse_date(*per.group(1, 2, 3)), "period_end": parse_date(*per.group(4, 5, 6)),
        "opening": total("Opening balance"), "closing": total("Closing balance"),
        "credit_total": total("Total Credit"), "debit_total": total("Total Debit"),
        "fee_total": total("Total Fees"), "transactions": [], "statement_no": 0}


def reconcile(st: dict) -> list[str]:
    if st.get("monthly"):
        from . import tyme_monthly
        return tyme_monthly.reconcile(st)
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
    credits = sum(t["amount"] + t["accrued_charge"] for t in txs if t["amount"] + t["accrued_charge"] > 0)
    debits = sum(-(t["amount"] + t["accrued_charge"]) for t in txs if t["amount"] + t["accrued_charge"] < 0)
    if credits != st["credit_total"]:
        problems.append(f"credits {credits} vs statement {st['credit_total']}")
    if debits != st["debit_total"]:
        problems.append(f"debits {debits} vs statement {st['debit_total']}")
    if sum(t["accrued_charge"] for t in txs) != st["fee_total"]:
        problems.append(f"fees {sum(t['accrued_charge'] for t in txs)} vs statement {st['fee_total']}")
    return problems
