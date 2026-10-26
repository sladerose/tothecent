"""FNB current account statement parser (Aspire and Premier share one layout).

Text layer supplies dates, amounts and balances. Some descriptions (fees, declined
transactions) are embedded as images, so those are read with Tesseract OCR and flagged.
"""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from decimal import Decimal

import pdfplumber
import pytesseract

_WIN_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if os.path.exists(_WIN_TESSERACT):  # Windows install; elsewhere pytesseract finds tesseract on PATH
    pytesseract.pytesseract.tesseract_cmd = _WIN_TESSERACT

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
FULL_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}

AMOUNT_RE = re.compile(r"^\d[\d,]*\.\d\d(Cr|Dr)?$")
DATE_X_MAX = 25
DESC_X_MIN, DESC_X_MAX = 36, 430
AMOUNT_X1 = (440, 497)
BALANCE_X1 = (500, 562)
CHARGE_X1_MIN = 562
ROW_TOLERANCE = 2.5
IMAGE_MATCH = 4.5
X_TOL = 1.5  # some statements render words with 1.9pt gaps, which the default 3pt merges


def money(text: str) -> Decimal:
    """Parse '1,234.50Cr' style text into a signed Decimal (credit positive)."""
    text = text.strip()
    sign = Decimal(1)
    if text.endswith("Cr"):
        text = text[:-2]
    elif text.endswith("Dr"):
        text, sign = text[:-2], Decimal(-1)
    return sign * Decimal(text.replace(",", ""))


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if alt:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
        alt = not alt
    return total % 10 == 0


def mask_cards(text: str) -> str:
    """Reduce any full card number (16 digits, Luhn valid) to its last four digits."""
    text = re.sub(r"\b(\d{9,15}) (\d{1,6})\b", lambda m: m.group(1) + m.group(2)
                  if 15 <= len(m.group(1) + m.group(2)) <= 16 else m.group(0), text)

    def repl(m: re.Match) -> str:
        digits = m.group(0)
        return "****" + digits[-4:] if luhn_ok(digits) else digits
    return re.sub(r"\b\d{16}\b", repl, text)


def ocr_image(page: pdfplumber.page.Page, im: dict) -> str:
    box = (max(im["x0"] - 1, 0), max(im["top"] - 1, 0),
           min(im["x1"] + 1, page.width), min(im["bottom"] + 1, page.height))
    img = page.crop(box).to_image(resolution=300).original
    return pytesseract.image_to_string(img, config="--psm 7").strip()


def parse_header(text: str) -> dict:
    h: dict = {}
    m = re.search(r"Statement Period\s*:\s*(\d+) (\w+) (\d{4}) to\s*(\d+) (\w+) (\d{4})", text)
    h["period_start"] = date(int(m[3]), FULL_MONTHS[m[2]], int(m[1]))
    h["period_end"] = date(int(m[6]), FULL_MONTHS[m[5]], int(m[4]))
    h["statement_no"] = int(re.search(r"Statement Number\s*:\s*(\d+)", text)[1])
    m = re.search(r"FNB (\w+) Current Account\s*:\s*(\d+)", text)
    if not m:  # the Money Maximiser savings account shares the layout but names the account differently
        m = re.search(r"(Money Maximiser)\s*:\s*(\d+)", text)
    if not m:  # general savings pockets are all headed "Savings Account"; the account number tells them apart
        m = re.search(r"(Savings Account)\s*:\s*(\d+)", text)
    h["product"], h["account_number"] = m[1], m[2]
    h["opening"] = money(re.search(r"Opening Balance\s+([\d,]+\.\d\d(?:Cr|Dr)?)", text)[1])
    h["closing"] = money(re.search(r"Closing Balance\s+([\d,]+\.\d\d(?:Cr|Dr)?)", text)[1])
    m = re.search(r"No\. Credit Transactions\s*(\d+)\s*([\d,]+\.\d\d)", text)
    h["credit_count"], h["credit_total"] = int(m[1]), Decimal(m[2].replace(",", ""))
    m = re.search(r"No\. Debit Transactions\s*(\d+)\s*([\d,]+\.\d\d)", text)
    h["debit_count"], h["debit_total"] = int(m[1]), Decimal(m[2].replace(",", ""))
    return h


def infer_date(day: int, month: int, end: date) -> date:
    """Latest date with this day and month that is not after the statement end."""
    for year in (end.year, end.year - 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d <= end + timedelta(days=1):
            return d
    raise ValueError(f"cannot place {day}/{month} before {end}")


def group_rows(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= ROW_TOLERANCE:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def parse_statement(path: str) -> dict:
    from .tyme import open_pdf  # emailed FNB statements are locked with the holder's date of birth
    with open_pdf(path) as pdf:
        full_text = "\n".join((p.extract_text(x_tolerance=X_TOL) or "") for p in pdf.pages)
        header = parse_header(full_text)
        txs: list[dict] = []
        in_table = False
        for page_no, page in enumerate(pdf.pages, 1):
            images = list(page.images)
            for row in group_rows(page.extract_words(x_tolerance=X_TOL)):
                first = row[0]["text"]
                if first == "Date" and any(w["text"] == "Description" for w in row):
                    in_table = True
                    continue
                if first in ("Closing", "Turnover"):
                    in_table = False
                    continue
                if not in_table:
                    continue
                is_tx = (len(row) > 1 and re.fullmatch(r"\d\d", first) and row[0]["x0"] < DATE_X_MAX
                         and row[1]["text"] in MONTHS and row[1]["x0"] < 45)
                if not is_tx:
                    # continuation of the previous description, plain text only
                    if (txs and txs[-1]["page"] == page_no
                            and all(DESC_X_MIN <= w["x0"] < DESC_X_MAX for w in row)
                            and not any(AMOUNT_RE.match(w["text"]) for w in row)
                            and row[0]["top"] - txs[-1]["_top"] < 16
                            and first not in ("Accrued", "Charges", "Transactions", "Page", "Delivery",
                                              "Branch", "EN/16/WV/DDA")):
                        txs[-1]["description"] += " " + " ".join(w["text"] for w in row)
                        txs[-1]["_top"] = row[0]["top"]
                    continue
                amount = balance = charge = None
                desc_words = []
                for w in row[2:]:
                    if AMOUNT_RE.match(w["text"]):
                        if AMOUNT_X1[0] <= w["x1"] < AMOUNT_X1[1]:
                            amount = w["text"]
                        elif BALANCE_X1[0] <= w["x1"] < BALANCE_X1[1]:
                            balance = w["text"]
                        elif w["x1"] >= CHARGE_X1_MIN:
                            charge = w["text"]
                        continue
                    if DESC_X_MIN <= w["x0"] < DESC_X_MAX:
                        desc_words.append(w["text"])
                if amount is None or balance is None:
                    raise ValueError(f"{path} p{page_no}: unparsed row {[w['text'] for w in row]}")
                desc, source = " ".join(desc_words), "text"
                if desc.startswith("Cr.int.rate"):
                    continue  # savings accounts print interest rate changes as rows with no amount; not transactions
                if not desc:
                    near = [im for im in images if abs(im["top"] - row[0]["top"]) <= IMAGE_MATCH
                            and im["x0"] < 100]
                    if near:
                        desc, source = ocr_image(page, near[0]), "ocr"
                    elif header["product"] == "Money Maximiser":
                        desc = "Accrued bank charges"  # the month end posting of charges accrued on payments
                amt = money(amount)
                if not amount.endswith(("Cr", "Dr")):
                    amt = -amt
                bal = money(balance)
                if not balance.endswith(("Cr", "Dr")):
                    bal = -bal  # an overdrawn balance is printed without the Cr suffix
                txs.append({
                    "date": infer_date(int(first), MONTHS[row[1]["text"]], header["period_end"]),
                    "description": desc, "desc_source": source,
                    "amount": amt, "balance": bal,
                    "accrued_charge": Decimal(charge.replace(",", "")) if charge else Decimal(0),
                    "page": page_no, "_top": row[0]["top"],
                })
    for t in txs:
        t["description"] = mask_cards(re.sub(r"\s+", " ", t["description"]).strip())
        del t["_top"]
    header["transactions"] = txs
    return header


def reconcile(st: dict) -> list[str]:
    """Return a list of problems; an empty list means the statement balances."""
    problems = []
    txs = st["transactions"]
    credits = [t["amount"] for t in txs if t["amount"] > 0]
    debits = [-t["amount"] for t in txs if t["amount"] < 0]
    if st["opening"] + sum(t["amount"] for t in txs) != st["closing"]:
        problems.append("opening plus transactions does not equal closing")
    running = st["opening"]
    for i, t in enumerate(txs, 1):
        running += t["amount"]
        if running != t["balance"]:
            problems.append(f"running balance breaks at row {i} ({t['date']} {t['description'][:30]})")
            break
    if (len(credits), sum(credits)) != (st["credit_count"], st["credit_total"]):
        problems.append(f"credits {len(credits)}/{sum(credits)} vs statement {st['credit_count']}/{st['credit_total']}")
    if (len(debits), sum(debits)) != (st["debit_count"], st["debit_total"]):
        problems.append(f"debits {len(debits)}/{sum(debits)} vs statement {st['debit_count']}/{st['debit_total']}")
    return problems

