"""TymeBank monthly personal statement parser (the PDF that is emailed on the 1st or 2nd of each month).

Layout (see Knowledge\\statement-formats.md): page 1 carries the customer block with `Period dd Mon yyyy - dd Mon yyyy`. Older
statements add summary pages. The detail follows as one section per account: the EveryDay account, then each GoalSave
pocket. A section is a header row (the pocket name then its 11 digit account number), `Opening Balance`, a table with
Date, Description, Fees, Money Out, Money In and Balance columns, and `Closing Balance`. Quirks handled here:
- A header row can sit at the bottom of one page and its `Opening Balance` at the top of the next.
- `Opening Balance` repeats at the top of a continuation page, with the section's own opening value (not the running balance).
  It is accepted only when it equals the opening balance of the open section.
- A description can wrap over several rows, which carry no date.
- Amounts use spaces as thousand separators, so columns are read by the right edge of each amount.
Statements are locked with the account holder's ID number (see `tyme.open_pdf`). The number is never stored.
Output has the same shape as `tyme.parse_file`: one statement per section, amount = in - out - fees, accrued_charge = fees.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from . import tyme
from .homeloan import group_rows

X_TOL = 1.5
MONTHS = tyme.MONTHS
DESC_X0 = 90          # a wrapped description row starts at about x 98, a date at about x 28, a section header at about x 22
DESC_X1 = 352         # descriptions end before the Fees column
EDGES = (404, 471.5, 538.5)   # midpoints between the right edges of Fees (370), Money Out (438), Money In (505) and Balance (572)
HEADER_WORDS = {"Date", "Description", "Fees", "Money", "Out", "In", "Balance"}
SKIP_MARKERS = ("TymeBank is an Authorised", "Registered Credit Provider", "All fees are inclusive", "Avenue, Rosebank")
PERIOD_RE = re.compile(r"Period (\d\d) (\w{3}) (\d{4}) - (\d\d) (\w{3}) (\d{4})")
ACCT_RE = re.compile(r"^\d{11}$")


def amount(words: list[dict]) -> Decimal:
    t = "".join(w["text"] for w in words).replace(",", "")
    return Decimal("0") if t in ("", "-") else Decimal(t)


def column(w: dict) -> int:
    x = w["x1"]
    return 0 if x <= EDGES[0] else 1 if x <= EDGES[1] else 2 if x <= EDGES[2] else 3


def is_date_row(row: list[dict]) -> bool:
    return (len(row) >= 4 and row[0]["x0"] < 80 and re.fullmatch(r"\d\d", row[0]["text"]) and row[1]["text"] in MONTHS
            and re.fullmatch(r"\d{4}", row[2]["text"]) is not None)


def parse_file(path: str) -> list[dict]:
    out: list[dict] = []
    with tyme.open_pdf(path) as pdf:
        first = tyme.clean(pdf.pages[0]).extract_text(x_tolerance=X_TOL) or ""
        per = PERIOD_RE.search(first)
        if not per:
            raise ValueError("no statement period found on page 1")
        owner = next((o for k, o in tyme.OWNER_KEYWORDS.items() if k in first[:400].upper()), None)
        if owner is None:
            raise ValueError("cannot tell whose statement this is from the header")
        start, end = tyme.parse_date(*per.group(1, 2, 3)), tyme.parse_date(*per.group(4, 5, 6))
        pending: tuple[str, str] | None = None
        cur: dict | None = None
        tx: dict | None = None
        for pno, page in enumerate(pdf.pages, 1):
            for row in group_rows(tyme.clean(page).extract_words(x_tolerance=X_TOL, y_tolerance=1)):
                text = " ".join(w["text"] for w in row)
                if any(m in text for m in SKIP_MARKERS) or re.match(r"^Page \d+ of \d+", text):
                    continue
                if row[0]["text"] in ("Opening", "Closing") and len(row) > 1 and row[1]["text"] == "Balance":
                    value = amount([w for w in row[2:] if w["x0"] > 300])
                    if row[0]["text"] == "Opening":
                        if cur is not None and cur["closing"] is None:
                            if pending is None and value == cur["opening"]:
                                continue  # the section's own opening balance, repeated at the top of a continuation page
                            raise ValueError(f"page {pno}: opening balance {value} inside an open section (its opening balance is {cur['opening']})")
                        if pending is None:
                            raise ValueError(f"page {pno}: opening balance with no section header")
                        name, acct = pending
                        cur = {"kind": "current" if name.startswith("EveryDay account") else "savings", "owner": owner,
                               "account_number": acct, "name": name,
                               "product": "EveryDay" if name.startswith("EveryDay account") else "GoalSave",
                               "period_start": start, "period_end": end, "opening": value, "closing": None,
                               "credit_total": None, "debit_total": None, "fee_total": None,
                               "transactions": [], "statement_no": 0, "monthly": True}
                        out.append(cur)
                        pending, tx = None, None
                    else:
                        if cur is None or cur["closing"] is not None:
                            raise ValueError(f"page {pno}: closing balance with no open section")
                        cur["closing"] = value
                        tx = None
                    continue
                if is_date_row(row):
                    if cur is None or cur["closing"] is not None:
                        raise ValueError(f"page {pno}: transaction row outside a section: {text[:60]}")
                    nums: dict[int, list[dict]] = {}
                    for w in row[3:]:
                        if w["x0"] >= 300 and (re.fullmatch(r"[\d.]+", w["text"]) or w["text"] == "-"):
                            nums.setdefault(column(w), []).append(w)
                    if set(nums) != {0, 1, 2, 3}:
                        raise ValueError(f"page {pno}: row {text[:60]} has columns {sorted(nums)}")
                    fee, money_out, money_in, balance = (amount(nums[i]) for i in range(4))
                    desc = " ".join(w["text"] for w in row[3:] if w["x0"] >= DESC_X0 and w["x1"] <= DESC_X1)
                    tx = {"date": tyme.parse_date(row[0]["text"], row[1]["text"], row[2]["text"]), "description": desc,
                          "desc_source": "text", "amount": money_in - money_out - fee, "balance": balance,
                          "accrued_charge": fee, "page": pno}
                    cur["transactions"].append(tx)
                    continue
                if row[0]["x0"] < 60 and ACCT_RE.match(row[-1]["text"]) and not text.startswith("Account Num"):
                    pending = (" ".join(w["text"] for w in row[:-1]), row[-1]["text"])
                    continue
                if tx is not None and row[0]["x0"] >= DESC_X0 and not all(w["text"] in HEADER_WORDS for w in row):
                    extra = " ".join(w["text"] for w in row if w["x0"] >= DESC_X0 and w["x1"] <= DESC_X1)
                    if extra:
                        tx["description"] += " " + extra
        if cur is not None and cur["closing"] is None:
            raise ValueError("the last section has no closing balance")
    for s in out:
        for t in s["transactions"]:
            t["description"] = re.sub(r"\s+", " ", t["description"]).strip()
    return out


def reconcile(st: dict) -> list[str]:
    problems = []
    running = st["opening"]
    for i, t in enumerate(st["transactions"], 1):
        running += t["amount"]
        if running != t["balance"]:
            problems.append(f"running balance breaks at row {i} ({t['date']} {t['description'][:30]})")
            break
    if st["opening"] + sum(t["amount"] for t in st["transactions"]) != st["closing"]:
        problems.append("opening plus transactions does not equal closing")
    return problems